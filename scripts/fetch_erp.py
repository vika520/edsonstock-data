#!/usr/bin/env python
"""股债利差(ERP)栏目 · 取数与「一次性计算并锁定」脚本。

固定口径（用户指定，不得改动）
------------------------------
    ERP = 100 / 沪深300 PE-TTM − 中债10年期国债收益率
    通道 = 1250 交易日（约5年）滚动均值 ± 2σ
    档位 = 全样本分位：>=98 极端低估 / 95-98 深度低估 / 90-95 低估 /
           75-90 偏便宜 / 50-75 中性 / 10-50 偏贵 / <10 极贵

数据源
------
  * 沪深300 PE-TTM：乐咕乐股 index-basic-pe 的 addTtmPe（整体法滚动市盈率），
    月末锚点 + 最新交易日，2005-04-29 起；token=md5(当日) + 页面 _csrf。
  * 中债10Y：东财数据中心 RPTA_WEB_TREASURYYIELD 的 EMM00166466（单位 %）。
  * 沪深300 日线：data/indices.json → indices.sh000300.rows = [date, close, high, low]。

重要披露
--------
  乐咕只提供月末 PE-TTM，1250 交易日通道需要日频序列。日频 PE 由「相邻两个
  月末官方锚点 + 沪深300 日收盘比例」分段重构（端点精确等于官方值），并在
  meta.dailyPeMethod 中写明。最新交易日 2026-11-13 本身就是官方锚点，因此
  「当日 ERP / 盈利收益率 / PE-TTM」均为官方实测值，不是插值结果。

产出：data/erp.json —— series 供画图；snapshot 为全部锁定数字，页面、复盘、
      独立报告一律直接读取，禁止二次计算或改口径。
"""
import argparse
import datetime as dt
import hashlib
import http.cookiejar
import json
import math
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SRC_PE = os.path.join(DATA, "_pe_raw.json")
OUT = os.path.join(DATA, "erp.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PE_PAGE = "https://legulegu.com/stockdata/sz50-ttm-lyr"
PE_API = "https://legulegu.com/api/stockdata/index-basic-pe?token={}&indexCode=000300.SH"
BOND_API = ("https://datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD"
            "&sty=ALL&st=SOLAR_DATE&sr=-1&token=894050c76af8597a853f5b408b759f5d"
            "&p={}&ps=500")
BOND_FIELD = "EMM00166466"

WINDOW = 1250
TIERS = [(98, "极端低估"), (95, "深度低估"), (90, "低估"), (75, "偏便宜"),
         (50, "中性"), (10, "偏贵"), (0, "极贵")]
HORIZONS = (20, 60, 120)


def get_json(url, headers=None, timeout=25):
    h = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/",
         "Accept": "application/json,text/plain,*/*"}
    if headers:
        h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- PE-TTM
def fetch_pe(offline=False):
    """返回 [(YYYYMMDD, addTtmPe), ...] 升序。带当日缓存，规避乐咕 429。"""
    today = dt.date.today().isoformat()
    if offline and os.path.isfile(SRC_PE):
        d = json.load(open(SRC_PE, encoding="utf-8"))
        return [(x["date"].replace("-", ""), x["addTtmPe"]) for x in d["data"]]
    if os.path.isfile(SRC_PE):
        d = json.load(open(SRC_PE, encoding="utf-8"))
        if d.get("_fetched") == today and d.get("data"):
            return [(x["date"].replace("-", ""), x["addTtmPe"]) for x in d["data"]]

    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    h = {"User-Agent": UA, "Referer": PE_PAGE, "Accept": "text/html,application/json,*/*"}
    page = op.open(urllib.request.Request(PE_PAGE, headers=h), timeout=25).read().decode("utf-8", "ignore")
    m = re.search(r'name=["\']_csrf["\'][^>]*content=["\']([^"\']+)', page)
    csrf = m.group(1) if m else ""
    h.update({"X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest"})
    token = hashlib.md5(today.encode()).hexdigest()
    last = None
    for attempt in range(4):
        try:
            with op.open(urllib.request.Request(PE_API.format(token), headers=h), timeout=30) as r:
                last = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception as e:
            if attempt == 3:
                raise RuntimeError("乐咕 PE-TTM 抓取失败：%s" % e)
            time.sleep(2 + attempt * 3)
    rows = last.get("data") or []
    if len(rows) < 100 or "addTtmPe" not in rows[-1]:
        raise RuntimeError("乐咕 PE-TTM 数据结构异常")
    with open(SRC_PE, "w", encoding="utf-8") as f:
        json.dump({"_fetched": today, "data": rows}, f, ensure_ascii=False)
    return [(x["date"].replace("-", ""), float(x["addTtmPe"])) for x in rows]


# ---------------------------------------------------------------- 中债10Y
def fetch_bond():
    first = get_json(BOND_API.format(1))
    res = first.get("result") or {}
    pages = int(res.get("pages") or 1)
    want = min(pages, 12)          # 6000 行 ≈ 24 年，足够 1250 日通道与全样本分位
    out = list(res.get("data") or [])

    def one(p):
        try:
            return (get_json(BOND_API.format(p)).get("result") or {}).get("data") or []
        except Exception:
            return []

    if want > 1:
        with ThreadPoolExecutor(max_workers=5) as ex:
            for rows in ex.map(one, range(2, want + 1)):
                out.extend(rows)
    m = {}
    for r in out:
        v = r.get(BOND_FIELD)
        d = (r.get("SOLAR_DATE") or "")[:10].replace("-", "")
        if d and isinstance(v, (int, float)):
            m[d] = float(v)
    if len(m) < 1000:
        raise RuntimeError("中债10Y 样本不足：%d" % len(m))
    return m


# ---------------------------------------------------------------- 工具
def r4(x):
    return None if x is None or not math.isfinite(x) else round(float(x), 4)


def pct_rank(arr, v):
    """全样本分位：<= v 的占比（%）。"""
    return 100.0 * sum(1 for x in arr if x <= v) / len(arr)


def quantile(arr, q):
    """线性插值分位数，q 为 0~1。"""
    a = sorted(arr)
    if not a:
        return None
    p = (len(a) - 1) * q
    lo, hi = int(math.floor(p)), int(math.ceil(p))
    if lo == hi:
        return a[lo]
    return a[lo] + (a[hi] - a[lo]) * (p - lo)


def tier_of(p):
    for lo, name in TIERS:
        if p >= lo:
            return name
    return "极贵"


def build_daily_pe(anchors, px_dates, px_close):
    """月末官方 PE 锚点 + 日收盘比例 → 日频 PE-TTM（端点精确锚定）。"""
    idx = {d: i for i, d in enumerate(px_dates)}
    a = [(d, v) for d, v in anchors if d in idx]
    if len(a) < 2:
        raise RuntimeError("PE 锚点不足")
    out = [None] * len(px_dates)
    for k in range(len(a) - 1):
        d0, p0 = a[k]
        d1, p1 = a[k + 1]
        i0, i1 = idx[d0], idx[d1]
        c0, c1 = px_close[i0], px_close[i1]
        # 盈利变化因子：令 t=i1 时结果精确等于官方 p1
        kval = (p1 / p0) / (c1 / c0) if c0 and c1 else 1.0
        span = max(1, i1 - i0)
        for i in range(i0, i1 + 1):
            w = (i - i0) / span
            out[i] = p0 * (px_close[i] / c0) * (1.0 + (kval - 1.0) * w)
    # 最后一个锚点之后：盈利不变，PE 随价格比例变化
    d0, p0 = a[-1]
    i0 = idx[d0]
    c0 = px_close[i0]
    for i in range(i0, len(px_dates)):
        out[i] = p0 * px_close[i] / c0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="用本地 PE 缓存，不联网")
    ap.add_argument("--incremental", action="store_true",
                    help="盘后模式：无新交易日直接跳过（不覆写锁定文件）；有则保留已锁定历史行，只追加新行")
    a = ap.parse_args()

    idx = json.load(open(os.path.join(DATA, "indices.json"), encoding="utf-8"))
    px = idx["indices"]["sh000300"]["rows"]
    dates = [r[0] for r in px]
    close = [float(r[1]) for r in px]

    old = None
    if a.incremental and os.path.isfile(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            old = None
        old_asof = str((old or {}).get("asof") or "")
        if dates[-1] <= old_asof:
            print("ERP 增量：最新交易日 %s 已锁定（asof=%s），无新数据，跳过重算，锁定文件保持不变"
                  % (dates[-1], old_asof))
            return
        print("ERP 增量：检测到新交易日 %s（原 %s），按同一固定口径追加，"
              "已锁定的历史数值不回溯修改" % (dates[-1], old_asof))

    pe_anchors = fetch_pe(a.offline)
    bond = fetch_bond()
    pe_daily = build_daily_pe(pe_anchors, dates, close)

    # 中债10Y 前向填充（只用 t 及之前，无未来函数）
    y10, last = [], None
    for d in dates:
        if d in bond:
            last = bond[d]
        y10.append(last)

    d, erp, pe, yy, cc = [], [], [], [], []
    for i, dt_ in enumerate(dates):
        if pe_daily[i] is None or y10[i] is None or pe_daily[i] <= 0:
            continue
        d.append(dt_)
        pe.append(pe_daily[i])
        yy.append(y10[i])
        erp.append(100.0 / pe_daily[i] - y10[i])
        cc.append(close[i])
    n = len(d)
    if n < WINDOW + 130:
        raise RuntimeError("ERP 日频样本不足：%d" % n)

    # 增量模式：已存在日期的 ERP / PE / 10Y / 收盘一律沿用锁定值，
    # 只让新增交易日按同一固定口径计算；后续通道、分位、阈值、条件概率
    # 全部基于这个「锁定历史 + 新行」的合并序列，保证内部一致且历史不回溯改动。
    if a.incremental and old:
        oldmap = {str(r[0]): r for r in (old.get("series") or {}).get("rows", [])}
        kept = 0
        for k, dt_ in enumerate(d):
            r = oldmap.get(dt_)
            if r:
                erp[k], pe[k], yy[k], cc[k] = r[1], r[2], r[3], r[4]
                kept += 1
        print("ERP 增量：沿用 %d 根锁定历史行，新增 %d 根" % (kept, n - kept))

    # 1250 日滚动均值 ±2σ（样本标准差 ddof=1）
    mid, up, dn, sd = [None] * n, [None] * n, [None] * n, [None] * n
    s = 0.0
    for i in range(n):
        s += erp[i]
        if i >= WINDOW:
            s -= erp[i - WINDOW]
        if i >= WINDOW - 1:
            m = s / WINDOW
            var = sum((erp[k] - m) ** 2 for k in range(i - WINDOW + 1, i + 1)) / (WINDOW - 1)
            v = math.sqrt(var)
            mid[i], sd[i], up[i], dn[i] = m, v, m + 2 * v, m - 2 * v

    i = n - 1
    erp_now, pe_now, y_now, px_now = erp[i], pe[i], yy[i], cc[i]
    ey_now = 100.0 / pe_now
    p_now = pct_rank(erp, erp_now)
    tier = tier_of(p_now)
    sigma_dev = (erp_now - mid[i]) / sd[i] if sd[i] else None

    # 三档阈值 + 极端上轨 → 点位换算（假设盈利与国债收益率不变）
    q90, q95, q98 = (quantile(erp, q) for q in (0.90, 0.95, 0.98))
    thresholds = []
    for name, target in (("90 分位（低估线）", q90), ("95 分位（深度低估）", q95),
                         ("98 分位（极端低估）", q98), ("1250 日 +2σ 极端上轨", up[i])):
        pe_t = 100.0 / (target + y_now) if (target + y_now) > 0 else None
        point = px_now * pe_t / pe_now if pe_t else None
        thresholds.append({
            "name": name, "erp": r4(target), "pe": r4(pe_t), "point": r4(point),
            "gapPoint": r4(point - px_now) if point else None,
            "gapPct": r4((point / px_now - 1) * 100) if point else None,
            "erpGap": r4(target - erp_now),
        })

    # 历史条件概率：历史上 ERP >= 当前档位下界后，未来 N 日沪深300 表现
    lower_tier = next((lo for lo, nm in TIERS if nm == tier), 0)
    cond_erp = quantile(erp, lower_tier / 100.0) if lower_tier > 0 else None
    cond_prob = []
    for h in HORIZONS:
        wins, rets, samples = 0, [], 0
        for t in range(n - h):
            if cond_erp is not None and erp[t] < cond_erp:
                continue
            if lower_tier == 0 and erp[t] > quantile(erp, 0.10):
                continue
            r = cc[t + h] / cc[t] - 1
            samples += 1
            rets.append(r)
            if r > 0:
                wins += 1
        cond_prob.append({
            "horizon": h, "samples": samples,
            "winRate": r4(100.0 * wins / samples) if samples else None,
            "avgRet": r4(100.0 * sum(rets) / samples) if samples else None,
            "medianRet": r4(100.0 * quantile(rets, 0.5)) if rets else None,
        })

    # 本轮区间事件：ERP >= 90 分位阈值的连续区间
    events, run = [], 0
    for k in range(n):
        if erp[k] >= q90:
            run += 1
        else:
            if run:
                events.append(run)
            run = 0
    if run:
        events.append(run)
    cur_run = 0
    k = n - 1
    while k >= 0 and erp[k] >= q90:
        cur_run += 1
        k -= 1
    hist_median = quantile([e for e in events if e >= 2], 0.5) if events else None
    event = {
        "definition": "ERP ≥ 全样本 90 分位阈值的连续交易日区间",
        "thresholdErp": r4(q90), "inEvent": cur_run > 0,
        "runDays": cur_run, "eventCount": len(events),
        "histMedianDays": r4(hist_median),
        "histMaxDays": max(events) if events else None,
        "progressPct": r4(100.0 * cur_run / hist_median) if (hist_median and cur_run) else None,
        "toEnterGap": r4(q90 - erp_now) if cur_run == 0 else None,
    }

    advice_map = {
        "极端低估": ("中长期配置价值显著，但 ERP 高位不等于立即见底",
                  "分批建仓宽基，等待 90 分位回落 + 指数站上 20 日均线确认",
                  "国债收益率快速上行或盈利大幅下修，会抬升 PE 使 ERP 自然回落，属假信号"),
        "深度低估": ("股债性价比偏向权益，适合中长期逐步加仓",
                  "按周分批，若 ERP 继续升至 98 分位则加大一档",
                  "若盈利预期下修导致 PE 跳升、ERP 被动回落，需重新评估"),
        "低估": ("权益相对债券有性价比，可开始左侧布局",
               "小仓位分批 + 等待放量确认，不追单日反弹",
               "ERP 长时间停留在 75-90 分位而指数不涨，说明盈利端在恶化"),
        "偏便宜": ("略偏权益，但安全垫不厚",
                "维持均衡配置，等 ERP 进入 90 分位再加仓",
                "若国债收益率下行推动 ERP 被动抬升，不等于股票更便宜"),
        "中性": ("股债性价比处于历史中位，无显著择时优势",
              "以既定配置比例为主，不做基于 ERP 的主动加减仓",
              "ERP 对指数的领先性不稳定，中性档位的方向胜率接近基准"),
        "偏贵": ("权益性价比偏低，注意控制仓位与回撤",
              "降低高估值暴露，等待 ERP 回到 50 分位以上",
              "盈利上修或国债收益率下行可使 ERP 回升，不必急于减仓"),
        "极贵": ("股债性价比明显偏向债券，历史高位风险区",
              "显著降低权益仓位，保留现金/债券",
              "若指数继续上涨而 ERP 走低，需警惕估值泡沫延续较久"),
    }
    stance, action, falsify = advice_map[tier]

    meta = {
        "locked": True,
        "computedBy": ("fetch_erp.py（一次性计算并锁定，页面/复盘/报告只读，不二次计算、不改口径）；"
                       "盘后 --incremental 仅在出现新交易日时按同一口径追加新行，已锁定历史数值不回溯修改"),
        "formula": "ERP = 100 / 沪深300 PE-TTM − 中债10年期国债收益率（单位：百分点）",
        "channel": "1250 交易日滚动均值 ± 2σ（样本标准差 ddof=1）",
        "tierRule": "全样本分位：≥98 极端低估 / 95-98 深度低估 / 90-95 低估 / 75-90 偏便宜 / 50-75 中性 / 10-50 偏贵 / <10 极贵",
        "dailyPeMethod": ("乐咕仅提供月末 PE-TTM(addTtmPe)。日频序列由相邻两个官方月末锚点按沪深300"
                          "日收盘比例分段重构，端点精确锚定官方值；最后一个锚点之后按盈利不变、"
                          "PE 随价格比例变动。最新交易日 2026-11-13 本身是官方锚点，当日读数非插值。"),
        "pointConversion": "点位 = 当前沪深300收盘 × 目标PE / 当前PE，其中 目标PE = 100/(目标ERP + 当前10Y)；假设盈利与国债收益率不变",
        "condProbDef": "历史条件＝ERP 处于当前档位（或不低于该档下界）；统计其后 20/60/120 交易日沪深300 收益。样本重叠，仅作描述",
        "sources": ["乐咕乐股 index-basic-pe / addTtmPe", "东方财富 RPTA_WEB_TREASURYYIELD / EMM00166466",
                    "腾讯证券 沪深300 日线（data/indices.json）"],
        "notInvestmentAdvice": True,
        "caveats": [
            "日频 PE 为月末锚点重构值，不是官方发布的全历史日频 PE-TTM；跨季度盈利跳变被平滑到相邻锚点之间。",
            "中债10Y 缺失交易日采用前向填充，只用当日及之前数据，无未来函数。",
            "历史条件概率存在样本重叠与区间依赖，不能视为独立样本，未做显著性检验。",
            "ERP 是估值性价比指标，对短期指数方向无稳定预测力；所有建议均为条件化描述，不构成投资建议。",
        ],
    }
    series = {"fields": ["date", "erp", "pe", "y10", "close", "mid", "upper", "lower"],
              "rows": [[d[k], r4(erp[k]), r4(pe[k]), r4(yy[k]), r4(cc[k]),
                        r4(mid[k]), r4(up[k]), r4(dn[k])] for k in range(n)]}
    snapshot = {
        "asof": d[-1], "erp": r4(erp_now), "peTtm": r4(pe_now),
        "earningsYield": r4(ey_now), "y10": r4(y_now), "close": r4(px_now),
        "historyPercentile": r4(p_now), "tier": tier,
        "channelMid": r4(mid[i]), "channelSigma": r4(sd[i]),
        "channelUpper": r4(up[i]), "channelLower": r4(dn[i]),
        "sigmaDev": r4(sigma_dev), "sampleDays": n,
        "thresholds": thresholds, "condProb": cond_prob, "event": event,
        "advice": {"stance": stance, "action": action, "falsify": falsify},
    }
    out = {"meta": meta, "asof": d[-1], "series": series, "snapshot": snapshot}
    if old:
        # 首次锁定快照永久保留：即使后续交易日追加新读数，原始全部数字仍完整留档
        out["lockedSnapshot"] = old.get("lockedSnapshot") or old.get("snapshot")
        out["meta"]["lockedBase"] = (old.get("meta") or {}).get("lockedBase") or old.get("asof")
    else:
        out["lockedSnapshot"] = snapshot
        out["meta"]["lockedBase"] = d[-1]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("saved erp.json | asof %s | 日频 %d 根 | ERP %.4f | 分位 %.2f%% | 档位 %s" %
          (d[-1], n, erp_now, p_now, tier))
    print("  PE-TTM %.4f | 盈利收益率 %.4f | 10Y %.4f | 中枢 %.4f ±2σ [%.4f, %.4f] | σ偏离 %.3f" %
          (pe_now, ey_now, y_now, mid[i], dn[i], up[i], sigma_dev))
    for t in thresholds:
        print("  %-22s ERP %7.4f -> PE %6.3f -> 点位 %8.2f (%+.2f%%)" %
              (t["name"], t["erp"], t["pe"], t["point"], t["gapPct"]))
    print("  区间事件: %s 已 %d 日 / 历史中位 %s 日 / 进度 %s%%" %
          (event["inEvent"], event["runDays"], event["histMedianDays"], event["progressPct"]))


if __name__ == "__main__":
    main()
