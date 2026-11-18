#!/usr/bin/env python
"""EdsonStock 数据一键查询 · 零依赖（Python 3.8+ 标准库）。

两种数据来源：
  --local  <dir>   读本地已入库的 data/*.json（如工作台的 wolf-cycle/data）
  --repo           从 GitHub raw 拉最新 JSON（默认，缓存到临时目录，TTL 内不重复下载）

示例
----
  python query.py --what erp                     # ERP 当前快照 + 分位 + 档位
  python query.py --what erp --history 30        # ERP 最近 30 个交易日
  python query.py --what leverage --tail 5       # 两融最近 5 行
  python query.py --what sentiment --tail 10     # 涨停 / 连板 / 晋级率历史
  python query.py --what tmt                     # TMT 当日成交占比
  python query.py --what cross                   # 外盘 / 汇率 / 中债10Y
  python query.py --what valuation --top 10      # 行业 PE 中位数（前 10 低位）
  python query.py --what unlock --top 5          # 未来 5 笔解禁
  python query.py --what review --tail 1         # 最新一篇复盘标题 + 摘要
  python query.py --what list                    # 列出全部可用数据与覆盖范围
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/vika520/Edsonstock/main/wolf-cycle/data/{name}.json"
CACHE_DIR = Path(os.environ.get("EDSONSTOCK_CACHE", str(Path.home() / ".cache" / "edsonstock")))
CACHE_TTL = 3600  # 秒：1 小时内不重复下载

FILES = {
    "erp":       ("erp.json",       "ERP 股债利差：5234 日序列 / ±2σ 通道 / 分位档位 / 条件概率 / 90 分位区间事件"),
    "leverage":  ("extras.json",    "两融余额与融资明细（2010 至今，T+1 披露）+ ETF 季度申赎"),
    "derivatives":("extras.json",   "期指基差 / 年化贴水 / PCR / 会员净多空（中金所 XML）"),
    "sentiment": ("extras.json",    "涨跌停池当日快照；history.json 另有涨停/连板/晋级率 250 日"),
    "valuation": ("extras.json",    "行业 PE 当日横截面（219 板块）+ 业绩预告方向代理"),
    "events":    ("extras.json",    "限售解禁 / 龙虎榜 / 业绩预告事件"),
    "cross":     ("extras.json",    "外盘 6 标的 / USDCNY / 中债 10Y（跨时区，非实时）"),
    "tmt":       ("tmt.json",       "TMT 四大行业成交占比（当日锁定 + 逐日累积）"),
    "flow":      ("flow.json",      "市场宽度：ADL/麦克莱伦/NH-NL/TRIN/UV-DV/OBV，600 日逐股聚合"),
    "workbench": ("workbench.json", "两市成交额 / 涨跌家数 / 32 只 ETF 日线 / 252 板块快照"),
    "history":   ("history.json",   "长历史：行业PE中位数250日 / 涨停连板250日 / 外部环境460日"),
    "review":    ("reviews.json",   "每日复盘文章（标题 / 快照 / 分栏判读 / 建议）"),
    "list":      (None,             "列出全部可用数据与覆盖范围"),
}
KEYMAP = {  # extras.json 内的子键
    "leverage": "leverage", "derivatives": "derivatives", "sentiment": "sentiment",
    "valuation": "valuation", "events": "events", "cross": "cross",
}


def load(name, use_repo):
    """返回 (data dict, 来源描述)。"""
    if name == "list":
        return {}, ""
    if not use_repo:
        p = Path(name) / (FILES[name][0] if False else FILES[name][0])
        return None, None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fname = FILES[name][0]
    cache = CACHE_DIR / fname
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        return json.loads(cache.read_text(encoding="utf-8")), "cache(%s)" % cache
    url = RAW.format(name=fname[:-5])
    req = urllib.request.Request(url, headers={"User-Agent": "edsonstock-data-skill/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    cache.write_bytes(data)
    return json.loads(data), url


def tail(rows, n):
    return rows[-n:] if n and n > 0 else rows


def out_erp(d, a):
    s = d.get("snapshot") or {}
    m = d.get("meta") or {}
    print("ERP 股债利差 · asof %s · 锁定基日 %s" % (d.get("asof"), m.get("lockedBase")))
    print("  ERP=%s（%s，全样本分位 %s%%）" % (s.get("erp"), s.get("tier"), s.get("historyPercentile")))
    print("  沪深300 PE-TTM=%s  盈利收益率=%s%%  中债10Y=%s%%" %
          (s.get("peTtm"), s.get("earningsYield"), s.get("y10")))
    ch = s.get("channelMid"), s.get("channelSigma")
    print("  1250日通道：中枢 %s ± σ%s（上轨 %s / 下轨 %s）  σ偏离 %s" %
          (ch[0], ch[1], s.get("channelUpper"), s.get("channelLower"), s.get("sigmaDev")))
    ev = s.get("event") or {}
    print("  90分位区间（阈值 %s）：当前%s，历史 %s 次" %
          (ev.get("thresholdErp"), "在区间内(%d日)" % ev["runDays"] if ev.get("inEvent") else "未进入，距线 %s" % ev.get("toEnterGap"), ev.get("eventCount")))
    for cp in s.get("condProb") or []:
        print("  条件概率 %d日：胜率 %s%%  均值 %s%%  样本 %s（重叠口径，仅描述）" %
              (cp["horizon"], cp["winRate"], cp["avgRet"], cp["samples"]))
    if a.history:
        rows = (d.get("series") or {}).get("rows") or []
        print("  最近 %d 日序列 [date, erp, pe, y10, close]：" % a.history)
        for r in tail(rows, a.history):
            print("   ", r)


def out_extras(d, a, key):
    x = d.get(key) or {}
    meta = d.get("meta") or {}
    print("%s · extras.json asof %s" % (key, meta.get("asof")))
    if key == "leverage":
        rows = x.get("rows") or []
        fields = x.get("fields") or (d.get("meta") or {}).get("leverageFields")
        print("  覆盖 %d 日（%s ~ %s）" % (len(rows), rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
        if fields:
            print("  字段：%s" % fields)
        for r in tail(rows, a.tail):
            print("   ", r)
    elif key == "derivatives":
        print("  期货合约：%s" % list((x.get("futures") or {}).keys()))
        print("  日数：%s  期权品种：%s" % (len(x.get("dates") or []), list((x.get("options") or {}).keys())))
        dates = x.get("dates") or []
        for dd in tail(dates, a.tail):
            print("   ", dd)
    elif key == "sentiment":
        snap = x.get("snapshot") or x
        print("  当日：", json.dumps({k: snap.get(k) for k in list(snap)[:10]}, ensure_ascii=False))
    elif key == "valuation":
        ind = x.get("industry") or []
        # 兼容两种结构：[名称, PE, ...] 或 [{name, pe, ...}]
        items = []
        for r in ind:
            if isinstance(r, (list, tuple)) and len(r) >= 2 and isinstance(r[1], (int, float)):
                items.append((str(r[0]), r[1], r[2:] if len(r) > 2 else []))
            elif isinstance(r, dict):
                pe = r.get("pe") or r.get("f9")
                if isinstance(pe, (int, float)):
                    items.append((str(r.get("name")), pe, []))
        items.sort(key=lambda t: t[1])
        print("  有效行业 %d（按 PE 升序前 %d）：" % (len(items), a.top or 10))
        for nm, pe, _ in items[: a.top or 10]:
            print("   %-8s PE %s" % (nm, pe))
    elif key == "events":
        for sub in ("unlock", "billboard", "predict"):
            arr = x.get(sub) or []
            print("  %s：%d 条" % (sub, len(arr)))
            for r in tail(arr, a.top or 5):
                print("   ", json.dumps(r, ensure_ascii=False)[:180])
    elif key == "cross":
        print("  ", json.dumps({k: x.get(k) for k in list(x)[:12]}, ensure_ascii=False)[:400])


def out_generic(d, a, name):
    if name == "tmt":
        s = d.get("snapshot") or {}
        print("TMT 成交占比 · asof %s" % d.get("asof"))
        print("  TMT %s 亿 / 两市 %s 亿 = %s%%（历史 %s 日）" %
              (s.get("tmtAmount"), s.get("marketAmount"), s.get("ratio"), s.get("historyDays")))
        for g, v in (d.get("groups") or {}).items():
            print("   %-4s %s 亿（%s%%）· %d 板块" % (g, v.get("amount"), v.get("share"), v.get("boardCount")))
        if a.tail:
            for r in tail(d.get("history") or [], a.tail):
                print("   ", r)
    elif name == "flow":
        print("市场宽度 · asof %s · %d 行（%s ~ %s）" %
              (d.get("asof"), len(d.get("rows") or []), (d.get("meta") or {}).get("from"), (d.get("meta") or {}).get("to")))
        print("  fields:", d.get("fields"))
        for r in tail(d.get("rows") or [], a.tail or 3):
            print("   ", r)
    elif name == "workbench":
        print("大盘 · asof %s" % d.get("asof"))
        mk = d.get("market") or []
        for r in tail(mk, a.tail or 3):
            print("   [date,amount元,vol,down,up,flat,total]", r)
        print("  ETF 数：%d  板块数：%d" % (len(d.get("etfs") or {}), len(d.get("boards") or [])))
    elif name == "history":
        m = d.get("meta") or {}
        print("长历史覆盖：", json.dumps(m.get("coverage"), ensure_ascii=False))
        for u in m.get("unavailable") or []:
            print("  不可回补：%s — %s" % (u.get("label"), (u.get("reason") or "")[:80]))
        for sec in ("valuation", "sentiment", "cross"):
            v = d.get(sec) or {}
            arr = v.get("daily") or v.get("rows") or []
            if arr:
                print("  %s：%d 行，最近：" % (sec, len(arr)))
                for r in tail(arr, a.tail or 2):
                    print("   ", r)
    elif name == "review":
        revs = d.get("reviews") or []
        for r in tail(revs, a.tail or 1):
            print("%s（%s）%s" % (r.get("date"), r.get("weekday"), r.get("title")))
            print("  ", (r.get("digest") or "")[:200])


def out_list():
    print("EdsonStock 公开数据一览（GitHub raw 每交易日盘后自动更新）\n")
    for k, (f, desc) in FILES.items():
        print("  %-13s %-14s %s" % (k, f or "-", desc))
    print("\n数据口径分三档，详见 caveats 参考：真实公开数据 / 量价代理 / 确认不可得。")
    print("仓库：https://github.com/vika520/Edsonstock")


def main():
    ap = argparse.ArgumentParser(description="EdsonStock 公开数据一键查询")
    ap.add_argument("--what", required=True, choices=list(FILES.keys()), help="要查的数据")
    ap.add_argument("--tail", type=int, default=0, help="序列取最后 N 行")
    ap.add_argument("--history", type=int, default=0, help="erp 序列取最近 N 日")
    ap.add_argument("--top", type=int, default=0, help="列表类取前 N 项")
    a = ap.parse_args()

    if a.what == "list":
        out_list()
        return
    d, src = load(a.what, use_repo=True)
    if d is None:
        print("读取失败", file=sys.stderr)
        return
    print("# 来源: %s\n" % src)
    if a.what == "erp":
        out_erp(d, a)
    elif a.what in KEYMAP:
        out_extras(d, a, KEYMAP[a.what])
    else:
        out_generic(d, a, a.what)


if __name__ == "__main__":
    main()
