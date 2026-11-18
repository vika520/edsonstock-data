#!/usr/bin/env python
"""TMT 成交占比（第 04 栏增强）· 取数与逐日累积。

数据源
------
  * 东财行业板块当日成交额：push2.eastmoney.com/api/qt/ulist.np/get（f6，单位元）。
    按板块名称归入申万口径四大行业：电子 / 通信 / 计算机 / 传媒。
  * 沪深两市成交额：data/workbench.json → market 最新行 amount（单位元，新浪全 A 快照口径）。

口径说明
--------
  东财行业板块为互斥分类（每只股票只属一个东财二级行业），四大行业合计可直接相加。
  历史序列从入库首日起逐日累积（data/tmt.json → history），样本不足时页面不输出分位。

历史回补
--------
  同口径历史需要东财 push2his 板块 K 线的每日成交额（f57）。实测 11 个 TMT
  二级行业板块中通常仅 3~5 个能取到历史（上游随机限流，6 轮补齐后仍不全），
  已核对可取板块的 f57 与当日 f6 精确一致，说明口径一致、问题只在覆盖不全。
  缺任一板块都不能加总（当日锁定值 44.5443% 是 11 个板块全量口径），否则会
  系统性低估占比，因此 --backfill 只记录 partial 状态、不发布残缺历史。
  同花顺板块 K 线板块划分不同，混用会破坏锁定口径，故不采用。
  待 11 个板块历史全部可取后重跑 --backfill 即自动补齐。

用法：python fetch_tmt.py [--dump] [--date YYYYMMDD] [--backfill]
"""
import argparse
import datetime as dt
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "tmt.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
URL = ("https://push2.eastmoney.com/api/qt/ulist.np/get"
       "?secids={}&fltt=2&fields=f12,f14,f6,f3")

GROUPS = {
    "电子": ["半导体", "消费电子", "元件", "光学光电子", "电子化学品Ⅱ", "电子化学品",
             "其他电子Ⅱ", "其他电子", "电子设备", "电子元件", "电子制造"],
    "通信": ["通信设备", "通信服务", "通信运营", "电信运营"],
    "计算机": ["软件开发", "互联网服务", "IT服务", "计算机设备", "计算机应用"],
    "传媒": ["游戏", "影视院线", "出版", "广告营销", "数字媒体", "文化传媒",
             "传媒", "广播电视", "院线"],
}


def fetch_boards():
    codes = ["BK%04d" % i for i in range(400, 1101)]
    batches = [codes[i:i + 60] for i in range(0, len(codes), 60)]
    out = {}

    def one(b):
        u = URL.format(",".join("90." + c for c in b))
        # 东财 push2 在本机代理下对 Python urllib 会直接断连，必须用 curl 子进程
        try:
            r = subprocess.run(["curl", "-s", "-m", "20", "-A", UA,
                                "-H", "Referer: https://quote.eastmoney.com/", u],
                               capture_output=True, timeout=25)
            d = json.loads(r.stdout.decode("utf-8", "replace"))
            diff = ((d.get("data") or {}).get("diff") or [])
            return [x for x in diff if x.get("f14") and isinstance(x.get("f6"), (int, float))]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        for rows in ex.map(one, batches):
            for x in rows:
                out[x["f12"]] = {"name": x["f14"], "amount": float(x["f6"]),
                                 "chg": x.get("f3")}
    return out


def classify(boards):
    """返回 {行业: [板块明细]}，每个板块只归入一个行业，避免重复计入。"""
    used, res = set(), {k: [] for k in GROUPS}
    for g, names in GROUPS.items():
        for exact in names:
            for code, v in boards.items():
                if code in used or v["name"] != exact:
                    continue
                used.add(code)
                res[g].append({"code": code, "name": v["name"],
                               "amount": v["amount"], "chg": v["chg"]})
    return res


KL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
      "?secid=90.{}&ut=fa5fd1943c7b386f172d6893dbfba10b"
      "&fields1=f1,f2,f3,f4,f5,f6"
      "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
      "&klt=101&fqt=1&beg=0&end=20500101&lmt=800")


def board_daily_amount(code):
    """拿单个板块的历史每日成交额（f57，单位元）；域名不可达时返回空 dict。"""
    try:
        r = subprocess.run(["curl", "-s", "-m", "20", "-A", UA,
                            "-H", "Referer: https://quote.eastmoney.com/",
                            KL.format(code)],
                           capture_output=True, timeout=25)
        d = json.loads(r.stdout.decode("utf-8", "replace"))
        kl = (d.get("data") or {}).get("klines") or []
        out = {}
        for line in kl:
            p = line.split(",")
            if len(p) > 6:
                out[p[0].replace("-", "")] = float(p[6])
        return out
    except Exception:
        return {}


def try_backfill(groups, market_rows):
    """用与当日完全相同的 11 个板块代码回补历史占比，缺任一天任一板块则不写该日。"""
    codes = [x["code"] for items in groups.values() for x in items]
    if not codes:
        return [], {"status": "unavailable", "reason": "当日未命中任何 TMT 板块代码"}
    # 上游对不同板块的响应是随机限流（同一批代码每次成功的子集都不同），
    # 因此多轮补齐缺失项；只有 11 个板块全齐才允许加总。
    import time
    amounts, pending = {}, list(codes)
    for _ in range(6):
        if not pending:
            break
        with ThreadPoolExecutor(max_workers=2) as ex:
            for code, m in zip(pending, ex.map(board_daily_amount, pending)):
                if m:
                    amounts[code] = m
        pending = [c for c in codes if c not in amounts]
        if pending:
            time.sleep(0.5)
    # 必须 11 个板块全部有历史，少一个都不能加总：缺项会系统性低估 TMT 占比，
    # 而当日锁定值 44.5443% 是 11 个板块全量口径，二者不可拼接。
    if len(amounts) < len(codes):
        missing = [c for c in codes if c not in amounts]
        return [], {
            "status": "partial",
            "reason": ("同口径历史需 push2his 板块 K 线的 f57 每日成交额。实测 11 个 TMT 二级行业中仅 %d 个"
                       "能取到历史（%s），其余 %d 个经 6 轮补齐仍为空（%s）。已核对可取板块的 f57 与当日 "
                       "ulist.np/get f6 精确一致（如消费电子 46623680731、通信设备 119824772329），"
                       "说明口径本身一致、问题在历史覆盖不全；用部分板块加总会严重低估占比，"
                       "因此不发布残缺历史，保持逐日累积。待覆盖恢复后重跑 --backfill 自动补齐。"
                       % (len(amounts), "、".join(sorted(amounts)) or "无",
                          len(missing), "、".join(missing))),
            "attemptedBoards": len(codes), "okBoards": len(amounts),
            "okCodes": sorted(amounts), "missingCodes": missing,
        }
    mk = {str(r[0]): float(r[1]) for r in market_rows}
    by = {}
    for code, m in amounts.items():
        for d, v in m.items():
            by.setdefault(d, {})[code] = v
    rows = []
    for d in sorted(by):
        if len(by[d]) < len(codes) or not mk.get(d):
            continue
        tmt = sum(by[d].values())
        rows.append([d, round(tmt / 1e8, 2), round(mk[d] / 1e8, 2),
                     round(100.0 * tmt / mk[d], 4)])
    return rows, {"status": "ok", "attemptedBoards": len(codes),
                  "okBoards": len(amounts), "backfilledDays": len(rows)}


def make_history_note(status):
    """由回补状态生成统一口径说明，供 standalone_backfill 与 main 共用。"""
    st = status.get("status")
    if st == "ok":
        return ("同口径历史已用东财 push2his 11 个 TMT 二级行业板块 K 线（f57 每日成交额）"
                "按日回补 %s 个交易日，且与当日锁定占比做过同日一致性校验。"
                % status.get("backfilledDays", 0))
    if st in ("partial", "unavailable", "mismatch"):
        return ("同口径历史需东财 push2his 板块 K 线的每日成交额；实测 %s 个 TMT 二级行业仅 "
                "%s/%s 个可取到历史（%s），其余仍为空（%s）。"
                "用部分板块加总会严重低估占比（当日锁定值 44.5443%% 是 11 个板块全量口径），"
                "因此不发布残缺历史，只保留当日锁定读数与逐日累积。"
                "待覆盖恢复后重跑 --backfill 自动补齐。" %
                (status.get("attemptedBoards", 11), status.get("okBoards", 0),
                 status.get("attemptedBoards", 11),
                 "、".join(status.get("okCodes") or []) or "无",
                 "、".join(status.get("missingCodes") or []) or "全部"))
    return ("同口径历史需东财 push2his 11 个 TMT 二级行业板块 K 线的每日成交额；"
            "当前只保留当日锁定读数与逐日累积，接口恢复后重跑 --backfill 自动补齐。")


def standalone_backfill():
    """独立回补：不碰当日锁定的 groups / snapshot，只追加历史行与状态说明。

    这样即使当日 push2 板块快照接口也不可达，仍可安全尝试回补并如实记录结果。
    """
    if not os.path.isfile(OUT):
        print("[backfill] tmt.json 不存在，跳过")
        return 1
    old = json.load(open(OUT, encoding="utf-8"))
    groups = {g: (v.get("boards") or []) for g, v in (old.get("groups") or {}).items()}
    w = json.load(open(os.path.join(DATA, "workbench.json"), encoding="utf-8"))
    extra, status = try_backfill(groups, w["market"])
    # 同日一致性校验：回补出的 asof 当日占比必须与锁定快照 44.5443% 对齐，
    # 否则说明口径已经漂移，宁可不发布历史。
    lock = (old.get("snapshot") or {}).get("ratio")
    same = next((r for r in extra if r[0] == old.get("asof")), None)
    if same and lock is not None and abs(same[3] - lock) > 0.02:
        status = {"status": "mismatch",
                  "reason": ("回补出的当日占比 %.4f%% 与锁定快照 %.4f%% 不一致，"
                             "说明板块 K 线成交额口径与 ulist.np/get f6 已漂移，拒绝发布历史。"
                             % (same[3], lock)),
                  "backfillRatio": same[3], "lockedRatio": lock}
        extra = []
    hist = list(old.get("history") or [])
    have = {x[0] for x in hist}
    added = 0
    for r in extra:
        if r[0] not in have:
            hist.append(r)
            have.add(r[0])
            added += 1
    hist.sort(key=lambda x: x[0])
    old["history"] = hist
    meta = old.setdefault("meta", {})
    meta["historyBackfill"] = status
    meta["historyNote"] = make_history_note(status)
    if isinstance(old.get("snapshot"), dict):
        old["snapshot"]["historyDays"] = len(hist)
    json.dump(old, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("[backfill] status=%s | 新增 %d 日 | 历史共 %d 日"
          % (status.get("status"), added, len(hist)))
    if status.get("reason"):
        print("[backfill] " + status["reason"])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="打印全部抓到的行业板块")
    ap.add_argument("--date", default="", help="指定 asof（默认取 workbench 最新交易日）")
    ap.add_argument("--backfill", action="store_true",
                    help="尝试用东财板块历史 K 线回补同口径 TMT 成交占比（接口不可达时优雅跳过）")
    a = ap.parse_args()
    if a.backfill:
        return standalone_backfill()

    boards = fetch_boards()
    if len(boards) < 50:
        raise RuntimeError("东财板块样本不足：%d" % len(boards))
    if a.dump:
        for code, v in sorted(boards.items(), key=lambda x: -x[1]["amount"]):
            print("%-9s %-12s %14.0f 亿  %s" % (code, v["name"], v["amount"] / 1e8, v["chg"]))
        print("TOTAL", len(boards))
    groups = classify(boards)

    w = json.load(open(os.path.join(DATA, "workbench.json"), encoding="utf-8"))
    last = w["market"][-1]
    asof = a.date or str(last[0])
    market = float(last[1])          # 元
    gsum = {}
    for g, items in groups.items():
        gsum[g] = {"amount": sum(x["amount"] for x in items),
                   "boards": items,
                   "chg": (sum(x["amount"] * (x["chg"] or 0) for x in items) /
                           sum(x["amount"] for x in items)) if items and sum(x["amount"] for x in items) else None}
    tmt = sum(v["amount"] for v in gsum.values())
    ratio = 100.0 * tmt / market if market else None

    history = []
    if os.path.isfile(OUT):
        try:
            history = json.load(open(OUT, encoding="utf-8")).get("history") or []
        except Exception:
            history = []
    # 保留 standalone_backfill 写入的状态，避免盘后重建把它冲掉
    backfill = {"status": "not-attempted",
                "reason": "独立回补见 --backfill；历史仍为逐日累积。"}
    try:
        prev = (json.load(open(OUT, encoding="utf-8")).get("meta") or {}).get("historyBackfill")
        if prev:
            backfill = prev
    except Exception:
        pass
    row = [asof, round(tmt / 1e8, 2), round(market / 1e8, 2), round(ratio, 4)]
    history = [x for x in history if x[0] != asof] + [row]
    history.sort(key=lambda x: x[0])

    out = {
        "meta": {
            "source": "东方财富行业板块行情 ulist.np/get (f6 成交额) + 新浪全A成交额(workbench.json)",
            "classify": "按东财二级行业板块名称精确匹配归入申万口径四大行业，板块互斥不重复计入",
            "historyNote": make_history_note(backfill),
            "historyBackfill": backfill,
            "unit": "成交额亿元；ratio 为 TMT 成交额占沪深两市成交额百分比",
            "notRealFundFlow": True,
        },
        "asof": asof,
        "groups": {g: {"amount": round(v["amount"] / 1e8, 2),
                       "share": round(100.0 * v["amount"] / market, 4),
                       "chg": round(v["chg"], 2) if v["chg"] is not None else None,
                       "boardCount": len(v["boards"]),
                       "boards": v["boards"]} for g, v in gsum.items()},
        "snapshot": {
            "marketAmount": round(market / 1e8, 2),
            "tmtAmount": round(tmt / 1e8, 2),
            "ratio": round(ratio, 4),
            "historyDays": len(history),
        },
        "history": history,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("saved tmt.json | asof %s | TMT %.2f 亿 / 两市 %.2f 亿 = %.4f%% | 历史 %d 日" %
          (asof, tmt / 1e8, market / 1e8, ratio, len(history)))
    for g, v in gsum.items():
        print("  %-5s %10.2f 亿 (%5.2f%%)  %d 个板块: %s" %
              (g, v["amount"] / 1e8, 100 * v["amount"] / market, len(v["boards"]),
               "、".join(x["name"] for x in v["boards"]) or "—"))


if __name__ == "__main__":
    main()
