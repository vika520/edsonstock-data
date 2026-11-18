"""威科夫栏目多周期行情（长历史版）：新浪财经公开 K 线。

数据源：vip.stock.finance.sina.com.cn/.../CN_MarketData.getKLineData
scale: 15 / 30 / 60 分钟、240(日)、1200(周)。

2026-11-14 重新实测推翻了旧结论「datalen 上限 320」：
  - 240  日线   datalen=3000 -> 3000 根（2014-07 起）
  - 1200 周线   datalen=3000 -> 1823 根（1990-12 起，全历史）
  - 60   60分钟 datalen=6000 -> 4111 根（2022-08 起，>4 年）
  - 30   30分钟 datalen>=5001 -> **服务端硬上限 5001 根**（2024-04 起，约 2.6 年）
  - 15   15分钟 datalen>=5001 -> **服务端硬上限 5001 根**（2025-07 起，约 1.3 年）
腾讯 mkline 备用域名只有 320 根，主域名本环境 502，无法补 30/15 分钟的 3 年缺口。
因此日线/周线/60 分钟覆盖 >=3 年，30/15 分钟如实标注不足 3 年。

两种模式
--------
  python fetch_wyckoff.py --full   全量重抓长历史（初始化 / 手工刷新）
  python fetch_wyckoff.py          增量：只抓最近一小段并 upsert 进长历史（盘后定时任务用）

输出 data/wyckoff.json：
{
  meta, asof, coverage,
  symbols: [{code,name,type}],
  periods: [{k,n}],
  bars: { code: { "240": [[t,o,h,l,c,v], ...], ... } }
}
"""
import argparse
import datetime as dt
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wyckoff.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
API = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
       "CN_MarketData.getKLineData?symbol={}&scale={}&datalen={}")

SYMBOLS = [
    ("sh000001", "上证指数", "指数"),
    ("sz399001", "深证成指", "指数"),
    ("sz399006", "创业板指", "指数"),
    ("sh000300", "沪深300", "指数"),
    ("sh510300", "沪深300ETF", "ETF"),
    ("sh510050", "上证50ETF", "ETF"),
    ("sh510500", "中证500ETF", "ETF"),
    ("sz159915", "创业板ETF", "ETF"),
]
PERIODS = [("240", "日线"), ("1200", "周线"), ("60", "60分钟"),
           ("30", "30分钟"), ("15", "15分钟")]

# 每个周期保留的目标根数：日线/周线/60分钟覆盖 3 年以上并留预热；
# 30/15 分钟是服务端硬上限，无法再长。
CAP = {"240": 1000, "1200": 500, "60": 4000, "30": 5001, "15": 5001}
# 增量模式每次只取最近这些根，40 个请求约 2~5 秒
INC = {"240": 120, "1200": 60, "60": 240, "30": 240, "15": 240}


def get(url, timeout=30, retries=3):
    last = None
    for k in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Referer": "https://finance.sina.com.cn/"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
            if k < retries:
                time.sleep(0.6 * (k + 1))
    raise RuntimeError(str(last))


def one(job):
    code, scale, n = job
    try:
        rows = json.loads(get(API.format(code, scale, n)).decode("utf-8", "ignore"))
        if not isinstance(rows, list) or len(rows) < 20:
            return code, scale, None
        out = []
        for r in rows:
            try:
                out.append([r["day"], round(float(r["open"]), 4), round(float(r["high"]), 4),
                            round(float(r["low"]), 4), round(float(r["close"]), 4),
                            int(float(r["volume"]))])
            except (ValueError, KeyError, TypeError):
                continue
        return code, scale, out
    except Exception:
        return code, scale, None


def merge_rows(old, new, cap):
    m = {r[0]: r for r in (old or [])}
    for r in (new or []):
        m[r[0]] = r
    out = [m[k] for k in sorted(m)]
    return out[-cap:] if cap else out


def coverage(rows):
    if not rows:
        return None
    a, b = rows[0][0], rows[-1][0]
    try:
        d0 = dt.date(int(a[:4]), int(a[5:7]), int(a[8:10]))
        d1 = dt.date(int(b[:4]), int(b[5:7]), int(b[8:10]))
        years = (d1 - d0).days / 365.25
    except (ValueError, TypeError):
        years = 0.0
    return {"from": a, "to": b, "bars": len(rows),
            "years": round(years, 2), "meets3y": years >= 3.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全量重抓长历史；默认增量 upsert")
    args = ap.parse_args()

    old = None
    if os.path.isfile(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            old = None
    full = args.full or not old or not old.get("bars")
    mode = "FULL" if full else "INCREMENTAL"

    bars = {c: {} for c, _, _ in SYMBOLS}
    if not full:
        for c in bars:
            for k, _ in PERIODS:
                bars[c][k] = list((old.get("bars", {}).get(c, {}).get(k) or []))

    jobs = [(c, k, (CAP[k] if full else INC[k])) for c, _, _ in SYMBOLS for k, _ in PERIODS]
    t0 = time.time()
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, scale, rows in ex.map(one, jobs):
            if rows:
                bars[code][scale] = merge_rows(bars[code].get(scale), rows, CAP[scale])
                ok += 1
            else:
                fail += 1
    print("[%s] 抓取 %d/%d 组成功，失败 %d，用时 %.1fs"
          % (mode, ok, len(jobs), fail, time.time() - t0))
    for c, name, _ in SYMBOLS:
        print("  %-12s %s" % (name, " ".join("%s:%d" % (k, len(bars[c].get(k, [])))
                                             for k, _ in PERIODS)))
    # 全量时不允许大面积缺失；增量时允许少量失败并沿用旧数据
    if full and fail > 2:
        raise RuntimeError("长历史抓取缺失 %d 组，终止（不写半成品）" % fail)
    if not full and fail > len(jobs) // 2:
        raise RuntimeError("增量抓取大面积失败 %d 组，终止" % fail)

    cov = {k: coverage(bars["sh000001"].get(k)) for k, _ in PERIODS}
    last = bars["sh000001"].get("240") or []
    payload = {
        "meta": {
            "source": "新浪财经公开 K 线接口 CN_MarketData.getKLineData（不复权）",
            "note": ("datalen 实测：日线/周线/60分钟可覆盖 3 年以上；"
                     "30/15 分钟服务端硬上限 5001 根（约 2.6 / 1.3 年），无法拉到 3 年。"
                     "volume 单位为股，仅作相对量能使用；所有周期同源，不做跨源拼接。"),
            "fetchedAt": dt.date.today().isoformat(),
            "mode": mode,
            "cap": CAP,
        },
        "asof": (last[-1][0][:10].replace("-", "") if last else (old or {}).get("asof", "")),
        "coverage": cov,
        "symbols": [{"code": c, "name": n, "type": t} for c, n, t in SYMBOLS],
        "periods": [{"k": k, "n": n} for k, n in PERIODS],
        "bars": bars,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print("saved:", OUT, round(os.path.getsize(OUT) / 1024 / 1024, 2), "MB")
    for k, n in PERIODS:
        c = cov[k]
        if c:
            print("  %-6s %5d 根  %s ~ %s  %.2f 年  %s"
                  % (n, c["bars"], c["from"], c["to"], c["years"],
                     "覆盖3年" if c["meets3y"] else "不足3年（接口上限）"))


if __name__ == "__main__":
    main()
