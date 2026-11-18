"""为威科夫吸筹结构回测抓取长历史日线 OHLCV。

页面第 02 栏 data/wyckoff.json 只有新浪接口上限的 320 根（约 1.4 年），
不足以回测「近三年」。本脚本改用腾讯 fqkline 抓 2022 年以来上证指数日线，
字段统一为 [date, open, high, low, close, volume]，与前端 [t,o,h,l,c,v] 一致。

指数不做复权；qfq 对指数等价于不复权。volume 单位与新浪不同，
但算法只使用 v / MA20(v)，是量纲无关的比值，不影响事件识别。
"""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wyckoff_history.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
URL = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
       "?param=sh000001,day,2022-01-01,2026-11-14,2000,qfq")


def fetch():
    last = None
    for k in range(4):
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": UA,
                                                       "Referer": "https://gu.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8")
            doc = json.loads(raw)
            node = doc.get("data")
            if not isinstance(node, dict) or "sh000001" not in node:
                raise RuntimeError("param error / empty: " + raw[:160])
            x = node["sh000001"]
            src = x.get("day") or x.get("qfqday") or []
            if len(src) < 900:
                raise RuntimeError("数据不足 %d 根" % len(src))
            rows = []
            for r in src:
                # 腾讯: [date, open, close, high, low, volume]
                rows.append([str(r[0]).replace("-", ""), round(float(r[1]), 4),
                             round(float(r[3]), 4), round(float(r[4]), 4),
                             round(float(r[2]), 4), float(r[5])])
            rows.sort(key=lambda z: z[0])
            return rows
        except Exception as e:
            last = e
            if k < 3:
                time.sleep(0.8 * (k + 1))
    raise RuntimeError(str(last))


def main():
    rows = fetch()
    # 基础校验：OHLC 关系、日期单调、无重复
    for r in rows:
        if not (r[3] <= min(r[1], r[4]) and r[2] >= max(r[1], r[4]) and r[5] >= 0):
            raise RuntimeError("OHLC 异常: %s" % (r,))
    if len({r[0] for r in rows}) != len(rows):
        raise RuntimeError("存在重复日期")
    payload = {
        "meta": {
            "source": "腾讯证券行情 web.ifzq.gtimg.cn fqkline（指数不复权）",
            "note": "为回测专门抓取的长历史；页面第02栏 wyckoff.json 仅 320 根，不用于本回测",
            "fetchedAt": time.strftime("%Y-%m-%d"),
            "fields": ["date", "open", "high", "low", "close", "volume"],
        },
        "asof": rows[-1][0],
        "symbol": "sh000001",
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("saved:", OUT, len(rows), "根", rows[0][0], "~", rows[-1][0],
          "最新收盘", rows[-1][4], round(os.path.getsize(OUT) / 1024, 1), "KB")


if __name__ == "__main__":
    main()
