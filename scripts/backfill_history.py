"""历史序列回补：把「逐日累积」指标能补的历史一次性补齐。

实测结论（2026-11-15）：
  ✅ 行业 PE 横截面  —— 东财 RPT_VALUEINDUSTRY_DET，2017-01-03 起每日 127~128 个行业 PE_TTM
  ✅ 涨停 / 连板 / 晋级率 —— 同花顺 limit_up_pool + continuous_limit_up，date 生效，可回溯约 1 年
  ✅ 跨市场日度     —— 腾讯 hkHSI / us.IXIC / us.INX / us.DJI 日线 + erp.json 中债 10Y（5233 日）
  ❌ ETF 日度份额   —— 交易所日度申赎份额无免费公开源；东财 f116 只有当日快照且无历史；
                        腾讯 ETF 日线有 693 根但不含份额 / 换手率；RPT_FUND_ETFLIST 的
                        DEC_TOTALSHARE 也只是当前快照，不是时间序列。
  ❌ TMT 成交占比   —— 同口径需东财 push2his 板块历史 K 线（f57 成交额）且 11 个板块全齐；
                        实测上游随机限流，每次只有 3~5 个板块返回，6 轮补齐仍凑不齐。
                        用残缺样本加总会系统性低估占比，因此不发布；fetch_tmt.py --backfill
                        带同日一致性校验，覆盖恢复后自动补齐。
  ❌ 炸板率 / 跌停家数历史 —— 东财炸板池、跌停池 date 参数失效；同花顺无对应历史池；
                        逐股缓存 flow_state.json 只有收盘价与成交量，无最高价，无法自算「触板未封」。

用法：
  python backfill_history.py                # 全量回补（首次）
  python backfill_history.py --incremental  # 增量：只补最近 8 个交易日，供盘后任务调用
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

from fetch_extras import curl

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "history.json")

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get?"
THS = "https://data.10jqka.com.cn/dataapi/limit_up/"
QQ = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2024-01-01,2026-12-31,800,qfq"
THS_FIELD = ("199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
             "1968584,3475914,9003,9004")


def jload(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def jdump(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def jparse(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def pct(a, b):
    """有序数组的线性插值分位。"""
    if not a:
        return None
    s = sorted(a)
    if b <= 0:
        return s[0]
    if b >= 1:
        return s[-1]
    k = b * (len(s) - 1)
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def trade_dates(n):
    w = jload(os.path.join(DATA, "workbench.json"), {}) or {}
    return [r[0] for r in (w.get("market") or [])][-n:]


# ---------------------------------------------------------------- 1. 行业 PE 历史
def industry_pages(pages):
    def one(p):
        u = (DC + "reportName=RPT_VALUEINDUSTRY_DET&columns=ALL&pageNumber=%d&pageSize=500"
             "&source=WEB&client=WEB&sortColumns=TRADE_DATE&sortTypes=-1" % p)
        return jparse(curl(u, "https://data.eastmoney.com/", retries=2))
    out = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for j in ex.map(one, range(1, pages + 1)):
            out.extend(((j.get("result") or {}).get("data") or []))
    return out


def build_valuation(pages, keep=250):
    rows = industry_pages(pages)
    by = defaultdict(list)
    for x in rows:
        d = (x.get("TRADE_DATE") or "")[:10].replace("-", "")
        pe = x.get("PE_TTM")
        if d and isinstance(pe, (int, float)) and pe > 0:
            by[d].append(float(pe))
    daily = []
    for d in sorted(by)[-keep:]:
        v = by[d]
        daily.append([d, round(pct(v, .5), 4), round(pct(v, .25), 4),
                      round(pct(v, .75), 4), len(v)])
    # 当日横截面（报表口径，供页面按行业列出）
    last = sorted(by)[-1] if by else ""
    ind = []
    seen = set()
    for x in rows:
        if (x.get("TRADE_DATE") or "")[:10].replace("-", "") != last:
            continue
        c = x.get("BOARD_CODE") or ""
        pe = x.get("PE_TTM")
        if c in seen or not isinstance(pe, (int, float)) or pe <= 0:
            continue
        seen.add(c)
        ind.append({"code": c, "name": x.get("BOARD_NAME") or c,
                    "pe": round(float(pe), 4), "pb": x.get("PB_MRQ"),
                    "ps": x.get("PS_TTM"), "mcap": x.get("TOTAL_MARKET_CAP")})
    ind.sort(key=lambda z: z["pe"])
    v = by.get(last) or []
    stat = {"n": len(v), "min": round(min(v), 4) if v else None,
            "p25": round(pct(v, .25), 4), "median": round(pct(v, .5), 4),
            "p75": round(pct(v, .75), 4), "max": round(max(v), 4) if v else None}
    print("  行业 PE 历史：%d 个交易日（%s ~ %s），当日 %d 个行业，中位数 %.2f"
          % (len(daily), daily[0][0] if daily else "-", daily[-1][0] if daily else "-",
             len(ind), stat["median"] or 0))
    return {"fields": ["date", "median", "p25", "p75", "n"], "daily": daily,
            "industry": ind, "stat": stat,
            "source": "东财 RPT_VALUEINDUSTRY_DET（行业估值报表，127~128 个行业 PE-TTM）",
            "note": "与第 13 栏当日 219 个东财二级行业板块 f9 横截面口径不同，绝对值不可直接比较；此序列用于看趋势。"}


# ---------------------------------------------------------------- 2. 涨停 / 连板 / 晋级率
def ths_day(d):
    zt_u = (THS + "limit_up_pool?page=1&limit=1&field=" + THS_FIELD +
            "&filter=HS,GEM2STAR&order_field=330324&order_type=0&date=" + d)
    # 同花顺连板池 limit 上限 200，超过会返回 status_code=-1 导致梯队全空
    lb_u = (THS + "continuous_limit_up?page=1&limit=200&field=" + THS_FIELD +
            "&filter=HS,GEM2STAR&order_field=330324&order_type=0&date=" + d)
    jz = jparse(curl(zt_u, "https://data.10jqka.com.cn/", retries=1))
    jl = jparse(curl(lb_u, "https://data.10jqka.com.cn/", retries=1))
    total = ((jz.get("data") or {}).get("page") or {}).get("total")
    if not isinstance(total, int):
        return None
    tiers = jl.get("data") or []
    if not isinstance(tiers, list):
        tiers = []
    heights = [t.get("height") for t in tiers if isinstance(t.get("height"), int)]
    lb2 = sum(t.get("number") or len(t.get("code_list") or [])
              for t in tiers if isinstance(t, dict) and (t.get("height") or 0) >= 2)
    return {"zt": total, "maxLb": max(heights) if heights else None, "lb2": lb2 or None}


def build_sentiment(dates):
    out = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for d, v in zip(dates, ex.map(ths_day, dates)):
            if v:
                out[d] = v
    ds = sorted(out)
    rows = []
    for i, d in enumerate(ds):
        v = out[d]
        prev = out.get(ds[i - 1]) if i else None
        promo = (round(v["lb2"] / prev["zt"] * 100, 2)
                 if (prev and prev["zt"] and v["lb2"] is not None) else None)
        rows.append([d, v["zt"], v["maxLb"], v["lb2"], promo])
    print("  短线情绪历史：%d / %d 个交易日（%s ~ %s）"
          % (len(rows), len(dates), rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
    return {"fields": ["date", "zt", "maxLb", "lb2", "promotion"], "rows": rows,
            "source": "同花顺 limit_up_pool（涨停家数） + continuous_limit_up（连板梯队），按 date 回补",
            "note": "仅涨停家数 / 最高连板 / 连板家数 / 晋级率可回补；炸板率与跌停家数历史不可得，当日仍用东财快照口径。"}


# ---------------------------------------------------------------- 3. 跨市场日度
def qq_close(code):
    j = jparse(curl(QQ % code, retries=2))
    node = (j.get("data") or {}).get(code) or {}
    arr = node.get("day") or node.get("qfqday") or []
    out = {}
    for r in arr:
        try:
            out[r[0].replace("-", "")] = float(r[2])
        except Exception:
            pass
    return out


def series_chg(m):
    ds = sorted(m)
    out = {}
    for i, d in enumerate(ds):
        if i and m[ds[i - 1]]:
            out[d] = round((m[d] / m[ds[i - 1]] - 1) * 100, 4)
    return out


def build_cross():
    codes = {"hsi": "hkHSI", "ndx": "us.IXIC", "spx": "us.INX", "djia": "us.DJI"}
    chg = {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for k, c in zip(codes, ex.map(qq_close, codes.values())):
            chg[k] = series_chg(c)
    erp = jload(os.path.join(DATA, "erp.json"), {}) or {}
    se = erp.get("series") or {}
    fi = (se.get("fields") or []).index("y10") if "y10" in (se.get("fields") or []) else 3
    y10 = {r[0]: r[fi] for r in (se.get("rows") or []) if len(r) > fi}
    # 以港股交易日为主轴（与 A 股交易日接近），美股取「不晚于当日」的最近一次收盘
    days = sorted(chg["hsi"])[-460:]

    def at(m, d):
        if d in m:
            return m[d]
        prev = [x for x in m if x <= d]
        return m[prev[-1]] if prev else None

    rows = [[d, chg["hsi"].get(d), at(chg["ndx"], d), at(chg["spx"], d),
             None, None, y10.get(d)] for d in days]
    print("  跨市场历史：%d 个交易日（%s ~ %s）；中债10Y 库 %d 日；美元指数/汇率无历史源，留空"
          % (len(rows), rows[0][0] if rows else "-", rows[-1][0] if rows else "-", len(y10)))
    return {"fields": ["date", "hsi", "ndx", "spx", "udi", "fx", "y10"], "daily": rows,
            "source": "腾讯 hkHSI / us.IXIC / us.INX / us.DJI 日线涨跌幅 + erp.json 中债 10Y 日频",
            "note": "美股为不晚于该日的最近收盘（隔夜），跨时区不同步；美元指数与人民币汇率无免费日度历史源，历史列留空，当日 KPI 仍有读数。"}


# ---------------------------------------------------------------- 4. 不可回补项（显式声明）
UNAVAILABLE = [
    {"key": "etfDailyShares", "label": "ETF 日度份额估计 / 净申赎",
     "reason": "交易所日度申赎份额无免费公开源。东财 f116 总市值只有当日快照、无历史接口；腾讯 ETF 日线 693 根含 OHLCV 但不含份额或换手率；RPT_FUND_ETFLIST 的 DEC_TOTALSHARE 只是当前快照。因此无法反推历史份额，只能从 2026-11-13 起逐日累积。",
     "alternative": "第 07 栏已改用真实可得的每日量价序列（32 只 ETF 逐日成交额、量比、方向代理、宽基分类堆积，约 690 日）作为日度主视觉；季度真实申赎保留为唯一可核验份额口径。"},
    {"key": "tmtRatio", "label": "TMT 交易拥挤度（成交占比）历史",
     "reason": ("同口径历史需要东财 push2his 板块 K 线的 f57 每日成交额，且必须 11 个 TMT 二级行业板块全部有数据。"
                "2026-11-15 实测上游对板块代码随机限流：同一批 11 个代码每次仅 3~5 个返回，经 6 轮补齐仍无法凑齐。"
                "已验证可取板块的 f57 与当日 ulist.np/get f6 精确一致（消费电子 46623680731、通信设备 119824772329），"
                "说明口径本身一致、问题在历史覆盖不全；用部分板块加总会系统性低估占比，因此不发布残缺历史。"),
     "alternative": ("fetch_tmt.py 已实现 --backfill：11 个板块全齐、且回补出的当日占比与锁定 44.5443% 一致时自动补齐，"
                     "并带同日一致性校验（不一致则拒绝发布）。当前状态为 partial，历史保持逐日累积。"
                     "同花顺板块 K 线虽有 2007 年起成交额，但板块划分不同，不混用以免破坏锁定口径。")},
    {"key": "limitPoolHistory", "label": "炸板率 / 跌停家数历史",
     "reason": "东财涨停池、跌停池、炸板池的 date 参数实测失效（请求 20261112 仍返回 qdate=20261113）；同花顺只有涨停池与连板池的历史接口，无炸板 / 跌停历史池；逐股缓存 flow_state.json 只保留收盘价与成交量，没有最高价，无法自行判定「盘中触板但收盘未封」。",
     "alternative": "涨停家数、最高连板、连板家数、晋级率已用同花顺按日回补约 1 年；炸板率与跌停家数仍只有当日快照，图上历史段留空并标注，不插值、不伪造。"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true", help="只补最近 8 个交易日（盘后任务用）")
    ap.add_argument("--pages", type=int, default=80, help="行业 PE 报表页数（每页 500 行）")
    args = ap.parse_args()
    inc = args.incremental
    old = jload(OUT, {}) or {}
    print("=== 历史回补 %s ===" % ("增量" if inc else "全量"))

    dates = trade_dates(8 if inc else 250)
    val = build_valuation(6 if inc else args.pages, keep=250)
    sen = build_sentiment(dates)
    cro = build_cross()

    # 增量模式下把新数据合并进已有序列，避免覆盖已回补的长历史
    def merge_rows(oldrows, newrows, key=0):
        m = {r[key]: r for r in (oldrows or [])}
        for r in newrows or []:
            m[r[key]] = r
        return [m[k] for k in sorted(m)]

    if inc and old:
        val["daily"] = merge_rows((old.get("valuation") or {}).get("daily"), val["daily"])
        sen["rows"] = merge_rows((old.get("sentiment") or {}).get("rows"), sen["rows"])
        cro["daily"] = merge_rows((old.get("cross") or {}).get("daily"), cro["daily"])

    out = {
        "meta": {
            "asof": dates[-1] if dates else "",
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "incremental" if inc else "full",
            "coverage": {
                "valuationDays": len(val["daily"]),
                "sentimentDays": len(sen["rows"]),
                "crossDays": len(cro["daily"]),
            },
            "unavailable": UNAVAILABLE,
        },
        "valuation": val, "sentiment": sen, "cross": cro,
    }
    jdump(OUT, out)
    print("saved:", OUT, round(os.path.getsize(OUT) / 1024, 1), "KB")
    print("coverage:", json.dumps(out["meta"]["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
