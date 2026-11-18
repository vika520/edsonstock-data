"""为板块快照补充『领涨股 1/5/20 日动量 + 量能比』真实历史（新浪日线）。

注意：领涨股是当日快照选出的，其历史走势只代表该股自身，不等于板块指数，
存在选择偏差。页面必须标注为『领涨股代理动量』。
"""
import json, os, ssl, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "data", "workbench.json")
OUT = os.path.join(HERE, "data", "board_mom.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData?symbol={}&scale=240&datalen=30")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def one(sym):
    try:
        req = urllib.request.Request(BASE.format(sym),
                                     headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
        raw = urllib.request.urlopen(req, timeout=10, context=CTX).read().decode("utf-8", "ignore")
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) < 21:
            return sym, None
        c = [float(r["close"]) for r in rows]
        v = [float(r["volume"]) for r in rows]
        if c[-1] <= 0 or c[-6] <= 0 or c[-21] <= 0:
            return sym, None
        vr = (sum(v[-5:]) / 5) / max(1.0, sum(v[-20:]) / 20)
        return sym, {
            "r1": round((c[-1] / c[-2] - 1) * 100, 2),
            "r5": round((c[-1] / c[-6] - 1) * 100, 2),
            "r20": round((c[-1] / c[-21] - 1) * 100, 2),
            "vr": round(vr, 2),
            "last": rows[-1]["day"],
        }
    except Exception:
        return sym, None


def main():
    w = json.load(open(W, encoding="utf-8"))
    boards = w["boards"]
    picked = {}
    def add(b):
        if b.get("leaderCode", "").startswith(("sh", "sz")):
            picked[b["leaderCode"]] = b["name"]
    for b in sorted(boards, key=lambda x: -x["amount"])[:60]:
        add(b)
    for b in sorted(boards, key=lambda x: -x["chg"])[:15]:
        add(b)
    for b in sorted(boards, key=lambda x: x["chg"])[:15]:
        add(b)
    print("待抓取领涨股:", len(picked))
    out = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(one, s) for s in picked]
        for f in as_completed(futs):
            sym, v = f.result()
            if v:
                out[sym] = v
    print("成功:", len(out), "/", len(picked))
    if out:
        print("样例:", list(out.items())[:3])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("saved:", OUT, round(os.path.getsize(OUT) / 1024, 1), "KB")


if __name__ == "__main__":
    main()
