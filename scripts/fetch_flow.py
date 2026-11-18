#!/usr/bin/env python
"""第 04 栏「市场宽度与拥挤度」：全市场逐股日线 → 每日可公式化聚合序列。

核心原则
--------
1. **不做比例分摊代理**：Up/Down Volume、NH-NL、TRIN 的输入全部来自逐只个股日线，
   不是「按涨跌家数比例去切全市场成交额」。
2. **除权由价格序列本身推导**：链式复权因子 factor，遇到 |r-1| 超出涨跌停物理上限
   （r<0.68 或 r>1.45）判为除权/拆股，把后续价格统一到此前口径。不依赖外部复权表。
3. **输出可分两层**：flow.json 只有聚合后的每日序列 + 4 个指数 OHLCV，可内嵌单文件 HTML；
   flow_state.json 保存最近 260 根对齐矩阵（不复权的原始日线对齐后复权），供盘后增量。
4. 全部使用 Python 标准库，无第三方依赖。

用法
----
  python fetch_flow.py --full          # 首次全量初始化（约 3-6 分钟）
  python fetch_flow.py                 # 增量：抓最近 6 根，只补新增交易日
  python fetch_flow.py --probe 200     # 小样本联调
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FLOW = os.path.join(DATA, "flow.json")
STATE = os.path.join(DATA, "flow_state.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HQ = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
      "Market_Center.getHQNodeData?page={}&num=100&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page")
KL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
      "CN_MarketData.getKLineData?symbol={}&scale=240&datalen={}")
TX = "https://{}/appstock/app/fqkline/get?param={},day,{},{},2000,qfq"
TX_HOSTS = ["web.ifzq.gtimg.cn", "ifzq.gtimg.cn"]
INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000300"]

FULL_BARS = 600          # 首次全量抓取根数
STATE_KEEP = 260         # 滑窗保留根数（>= 250 日新高窗口 + 1）
INC_BARS = 6
WINDOWS = (60, 250)
FIELDS = ["date", "up", "dn", "fl", "n", "uv", "dv",
          "nh60", "nl60", "nh250", "nl250", "e60", "e250", "ew", "exd"]


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def http(url, ref="https://finance.sina.com.cn/", timeout=20, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
            if k < tries - 1:
                time.sleep(0.35 * (k + 1))
    raise RuntimeError("HTTP 失败 %s : %s" % (url[:100], last))


def jdump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- 取数
def fetch_universe():
    def page(i):
        try:
            return json.loads(http(HQ.format(i), timeout=15).decode("utf-8", "ignore")) or []
        except Exception:
            return []

    t = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        pages = list(ex.map(page, range(1, 60)))
    seen = {}
    for p in pages:
        for r in p or []:
            seen[r["symbol"]] = r
    if len(seen) < 5000:
        raise RuntimeError("全 A 快照只取到 %d 只，疑似限频，终止" % len(seen))
    log("全 A 快照 %d 只 / %.1fs" % (len(seen), time.time() - t))
    return sorted(seen)


def fetch_kline(sym, n):
    try:
        raw = http(KL.format(sym, n), timeout=20, tries=2).decode("utf-8", "ignore")
        arr = json.loads(raw)
        out = []
        for r in arr:
            try:
                out.append((r["day"].replace("-", ""), float(r["close"]), float(r["volume"])))
            except (ValueError, KeyError, TypeError):
                continue
        return sym, out
    except Exception:
        return sym, None


def fetch_klines(syms, n, workers=12):
    out, failed, t0 = {}, [], time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (sym, rows) in enumerate(ex.map(lambda s: fetch_kline(s, n), syms), 1):
            if rows is None:
                failed.append(sym)
            else:
                out[sym] = rows
            if k % 800 == 0:
                log("  日线 %d/%d · %.0fs · 失败 %d" % (k, len(syms), time.time() - t0, len(failed)))
    log("日线完成 %d/%d 成功 · %.1fs · 失败 %d"
        % (len(out), len(syms), time.time() - t0, len(failed)))
    if len(out) < len(syms) * 0.9:
        raise RuntimeError("日线成功率 <90%%，疑似限频，终止（成功 %d）" % len(out))
    return out, failed


def calendar_from(kl, nsyms):
    """用日期出现次数识别真实交易日，避免依赖外部日历更新顺序。"""
    cnt = collections.Counter()
    for rows in kl.values():
        cnt.update(set(d for d, _, _ in rows))
    thr = max(80, int(nsyms * 0.12))
    return sorted(d for d, c in cnt.items() if c >= thr)


def fetch_index_ohlcv():
    """4 个宽基指数 OHLCV（不复权），2016 起；OBV / A-D / 相对强度的真实输入。"""
    out = {}
    for code in INDEX_CODES:
        allrows = {}
        for a, b in (("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-12-31")):
            raw = None
            for host in TX_HOSTS:
                try:
                    raw = json.loads(http(TX.format(host, code, a, b),
                                          "https://gu.qq.com/", timeout=20, tries=2)
                                     .decode("utf-8", "ignore"))
                    break
                except Exception:
                    continue
            if not raw:
                continue
            node = (raw.get("data") or {}).get(code) or {}
            for r in (node.get("day") or node.get("qfqday") or []):
                try:
                    allrows[r[0].replace("-", "")] = [r[0].replace("-", ""),
                                                       float(r[2]), float(r[3]),
                                                       float(r[4]), float(r[5])]
                except (ValueError, IndexError):
                    continue
            time.sleep(0.12)
        rows = [allrows[k] for k in sorted(allrows)]
        if len(rows) < 1500:
            raise RuntimeError("指数 %s 日线不足: %d" % (code, len(rows)))
        out[code] = rows
        log("指数 %s %d 根 %s ~ %s" % (code, len(rows), rows[0][0], rows[-1][0]))
    return out


# ---------------------------------------------------------------- 公式化计算
def adjust(raw):
    """链式复权：由 close 序列自身推导除权点，返回复权后序列、除权位、末因子。

    A 股单日涨跌幅物理上限 30%（北交所），正常 r 落在 [0.70, 1.30]；
    用 [0.68, 1.45] 作为除权判定带，避免把涨跌停误判成除权。
    """
    out, ex, factor, prev = [], [], 1.0, None
    for c in raw:
        if c is None or c <= 0:
            out.append(None)
            continue
        if prev is not None:
            r = c / prev
            if r < 0.68 or r > 1.45:
                factor *= r
                ex.append(len(out))
        out.append(round(c / factor, 6))
        prev = c
    return out, ex, factor


def roll_ext(arr, w):
    """O(n) 单调队列：窗口 [i-w+1, i] 内有效值的 max / min + 有效数前缀。"""
    n = len(arr)
    mx, mn, cnt = [None] * n, [None] * n, [0] * n
    dq1, dq2 = collections.deque(), collections.deque()
    c = 0
    for i, v in enumerate(arr):
        if v is not None:
            c += 1
        cnt[i] = c
        left = i - w + 1
        while dq1 and dq1[0][0] < left:
            dq1.popleft()
        while dq2 and dq2[0][0] < left:
            dq2.popleft()
        if v is not None:
            while dq1 and dq1[-1][1] <= v:
                dq1.pop()
            dq1.append((i, v))
            while dq2 and dq2[-1][1] >= v:
                dq2.pop()
            dq2.append((i, v))
        if dq1:
            mx[i], mn[i] = dq1[0][1], dq2[0][1]
    return mx, mn, cnt


def aggregate(dates, symbols, cm, vm, rawlast, start=0):
    """逐股票算方向/量能/新高新低，再按交易日累加。start 之后才产出（增量用）。"""
    n, ns = len(dates), len(symbols)
    keys = ("up", "dn", "fl", "n", "uv", "dv", "nh60", "nl60", "nh250", "nl250",
            "e60", "e250", "exd")
    acc = {k: [0.0] * n for k in keys}
    rsum, rn = [0.0] * n, [0] * n

    for j in range(ns):
        cs, vs = cm[j], vm[j]
        arr = [cs[t] if (t < len(cs) and cs[t] is not None
                         and t < len(vs) and vs[t] and vs[t] > 0) else None
               for t in range(n)]
        ext = {w: roll_ext(arr, w) for w in WINDOWS}
        for t in range(start, n):
            c = arr[t]
            if c is None:
                continue
            v = vs[t]
            acc["n"][t] += 1
            if t > 0 and arr[t - 1] is not None:
                p = arr[t - 1]
                r = c / p - 1
                if c > p:
                    acc["up"][t] += 1
                    acc["uv"][t] += v
                elif c < p:
                    acc["dn"][t] += 1
                    acc["dv"][t] += v
                else:
                    acc["fl"][t] += 1
                rsum[t] += r
                rn[t] += 1
            for w in WINDOWS:
                mx, mn, cnt = ext[w]
                left = t - w + 1
                if left < 0:
                    continue
                valid = cnt[t] - (cnt[left - 1] if left > 0 else 0)
                if valid < w * 0.70:
                    continue
                acc["e%d" % w][t] += 1
                eps = 1e-9
                if mx[t] is not None and c >= mx[t] - eps:
                    acc["nh%d" % w][t] += 1
                if mn[t] is not None and c <= mn[t] + eps:
                    acc["nl%d" % w][t] += 1
    rows = []
    for t in range(start, n):
        rows.append([
            dates[t],
            int(acc["up"][t]), int(acc["dn"][t]), int(acc["fl"][t]), int(acc["n"][t]),
            round(acc["uv"][t] / 1e8, 4), round(acc["dv"][t] / 1e8, 4),
            int(acc["nh60"][t]), int(acc["nl60"][t]),
            int(acc["nh250"][t]), int(acc["nl250"][t]),
            int(acc["e60"][t]), int(acc["e250"][t]),
            round(rsum[t] / rn[t] * 100, 4) if rn[t] else None,
            int(acc["exd"][t]),
        ])
    return rows


def make_meta(asof, nrows, nbars, universe_n, failed_n):
    return {
        "asof": asof,
        "source": "新浪财经全 A 快照（股票名单）+ CN_MarketData 日线（逐股 OHLCV，不复权）"
                  " + 腾讯证券 fqkline（4 个宽基指数 OHLCV）",
        "universe": "当期全 A 共 %d 只，日线成功 %d 只；北交所纳入，停牌/缺失自动排除"
                    % (universe_n, universe_n - failed_n),
        "bars": nbars,
        "rows": nrows,
        "fields": FIELDS,
        "formula": {
            "adjust": "链式复权：r=close_t/close_(t-1)，r<0.68 或 r>1.45 判除权，后续 close 除以累乘因子。"
                      " 仅由公开收盘价推导，无外部复权表；小幅送转/分红未复权，NH/NL 偏保守。",
            "direction": "以复权后 close 对前一有效交易日 close 比较，涨/跌/平家数；volume>0 才计入。",
            "updown_vol": "UV/DV = 涨股/跌股当日成交量之和（亿股，新浪 volume 单位=股），非按家数比例分摊。",
            "nhnl": "NH_w / NL_w = 当日复权收盘达到过去 w 个交易日（含当日）最高/最低的股票数；"
                    "窗口内有效样本 ≥70% 才计入 e_w。",
            "ew": "等权平均涨跌幅 = mean(复权 close 日收益) ×100（%），排除停牌与无前收样本。",
            "trin": "TRIN = (up/down) / (UV/DV)，页面计算；UV/DV 为真实逐股成交量合计。",
            "adl": "ADL_t = Σ(up − down)，页面计算，用 workbench market 2016 年起 2622 天全序列。",
            "mcclellan": "RANA=(up−down)/(up+down+flat)×100；OSC=EMA19(RANA)−EMA39(RANA)；SUM=ΣOSC。",
            "obv": "OBV_t = OBV_(t-1) + sign(C_t−C_(t-1))×V_t，C/V 取上证指数真实收盘与成交量。",
            "ad": "Chaikin MFV = ((2C−H−L)/(H−L))×V，AD = ΣMFV；C/H/L/V 取上证指数真实日线。",
            "rs": "等权全 A 净值（Σew 复利）/ 宽基指数净值，以及宽度相对强度 = 归一化 ADL / 归一化指数。",
        },
        "caveat": [
            "个股日线为不复权数据，除权仅按价格跳空阈值链式修正，小幅送转/现金分红未复权，"
            "NH/NL 与等权收益可能略偏保守。",
            "股票池为『当前』全 A 名单回溯，已退市股票不在样本内，存在幸存者偏差；"
            "新股上市不足窗口者不计入 NH/NL（分母 e_w 同步变化），故请优先看占比 NH%/NL%。",
            "TRIN、Up/Down Volume、NH-NL 为完全公式化的量价统计，不是真实资金流；"
            "真实资金流需要逐笔成交的主动买卖方向标记，本页没有该数据。",
            "拥挤度综合分是本页自定义的历史分位等权合成，不是行业标准指标，仅作状态描述，不含预测。",
        ],
        "fetchedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------- 全量
def build_full(limit=None):
    syms = fetch_universe()
    if limit:
        syms = syms[:limit]
    kl, failed = fetch_klines(syms, FULL_BARS)
    dates = calendar_from(kl, len(syms))
    if len(dates) > FULL_BARS:
        dates = dates[-FULL_BARS:]
    di = {d: i for i, d in enumerate(dates)}
    log("交易日历 %d 天 %s ~ %s" % (len(dates), dates[0], dates[-1]))

    cm, vm, rawlast, exd_total = [], [], [], collections.Counter()
    for s in syms:
        rawc = [None] * len(dates)
        rawv = [0.0] * len(dates)
        for d, c, v in kl.get(s, []):
            i = di.get(d)
            if i is not None:
                rawc[i], rawv[i] = c, v
        adj, ex, fac = adjust(rawc)
        for t in ex:
            exd_total[dates[t]] += 1
        cm.append(adj)
        vm.append([round(v, 2) for v in rawv])
        rawlast.append(next((c for c in reversed(rawc) if c is not None), None))

    t = time.time()
    rows = aggregate(dates, syms, cm, vm, rawlast)
    for r in rows:
        r[14] = exd_total.get(r[0], 0)
    log("聚合完成 %d 行 / %.1fs" % (len(rows), time.time() - t))

    idx = fetch_index_ohlcv()
    asof = dates[-1]
    jdump(FLOW, {"meta": make_meta(asof, len(rows), len(dates), len(syms), len(failed)),
                 "asof": asof, "fields": FIELDS, "rows": rows, "idx": idx})
    keep = min(STATE_KEEP, len(dates))
    jdump(STATE, {"asof": asof, "dates": dates[-keep:], "symbols": syms,
                  "C": [c[-keep:] for c in cm], "V": [v[-keep:] for v in vm],
                  "rawLast": rawlast, "factor": [1.0] * len(syms)})
    log("写入 %s (%.1f KB) / %s (%.1f MB)"
        % (os.path.basename(FLOW), os.path.getsize(FLOW) / 1024,
           os.path.basename(STATE), os.path.getsize(STATE) / 1024 / 1024))
    return rows


# ---------------------------------------------------------------- 增量
def build_incremental():
    if not (os.path.exists(STATE) and os.path.exists(FLOW)):
        log("无 state，转全量初始化")
        return build_full()
    st = json.load(open(STATE, encoding="utf-8"))
    fl = json.load(open(FLOW, encoding="utf-8"))
    old_dates, old_syms = st["dates"], st["symbols"]
    syms = fetch_universe()
    kl, failed = fetch_klines(syms, INC_BARS, workers=12)
    newdates = sorted({d for rows in kl.values() for d, _, _ in rows if d > st["asof"]})
    if not newdates:
        log("无新增交易日（state.asof=%s），仅刷新指数与元信息" % st["asof"])
    else:
        log("新增交易日 %d 天: %s" % (len(newdates), ",".join(newdates)))

    symset = set(old_syms)
    C, V, rawLast = st["C"], st["V"], st["rawLast"]
    for s in syms:
        if s not in symset:
            symset.add(s)
            old_syms.append(s)
            C.append([None] * len(old_dates))
            V.append([0.0] * len(old_dates))
            rawLast.append(None)
    pos = {s: i for i, s in enumerate(old_syms)}
    exd = collections.Counter()
    for s, rows in kl.items():
        j = pos[s]
        bydate = {d: (c, v) for d, c, v in rows}
        for d in newdates:
            c, v = bydate.get(d, (None, 0.0))
            prev = rawLast[j]
            if c is not None and prev and prev > 0:
                r = c / prev
                if r < 0.68 or r > 1.45:
                    exd[d] += 1
            # 复权：把新价格对齐到 state 最后一根复权收盘的口径
            lastadj = next((z for z in reversed(C[j]) if z is not None), None)
            if c is None:
                adj = None
            elif lastadj is not None and prev and prev > 0:
                adj = round(lastadj * (c / prev), 6) if 0.68 <= c / prev <= 1.45 else lastadj
            else:
                adj = c
            C[j].append(adj)
            V[j].append(round(v, 2))
            if c is not None:
                rawLast[j] = c
        # 同一交易日重复执行时不改写 state.C：
        # 里面存的是链式复权价，而 bydate 里是未复权原始收盘，直接覆盖会污染历史。

    alldates = old_dates + newdates
    keep = STATE_KEEP
    s0 = len(alldates) - keep
    if s0 > 0:
        alldates = alldates[s0:]
        C = [c[s0:] for c in C]
        V = [v[s0:] for v in V]
    start = max(0, len(alldates) - len(newdates))
    if not newdates:
        start = len(alldates) - 1
    rows = aggregate(alldates, old_syms, C, V, rawLast, start=start)
    for r in rows:
        r[14] = exd.get(r[0], 0)

    merged = {r[0]: r for r in fl["rows"]}
    for r in rows:
        merged[r[0]] = r
    outrows = [merged[k] for k in sorted(merged)]
    idx = fetch_index_ohlcv()
    asof = alldates[-1]
    jdump(FLOW, {"meta": make_meta(asof, len(outrows), len(outrows), len(old_syms), len(failed)),
                 "asof": asof, "fields": FIELDS, "rows": outrows, "idx": idx})
    jdump(STATE, {"asof": asof, "dates": alldates, "symbols": old_syms,
                  "C": C, "V": V, "rawLast": rawLast,
                  "factor": st.get("factor", [1.0] * len(old_syms))})
    log("增量完成：flow %d 行 / state %d 根 / flow.json %.1f KB"
        % (len(outrows), len(alldates), os.path.getsize(FLOW) / 1024))
    return outrows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--probe", type=int, default=0)
    a = ap.parse_args()
    if a.probe:
        rows = build_full(a.probe)
    elif a.full:
        rows = build_full()
    else:
        rows = build_incremental()
    print("\n最后 3 行:")
    for r in rows[-3:]:
        print(" ", dict(zip(FIELDS, r)))


if __name__ == "__main__":
    main()
