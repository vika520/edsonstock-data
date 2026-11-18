"""工作台 4 个新栏目的统一取数：全市场成交额、板块快照、宽基/主题 ETF 日线。

数据源：
  1) 通达信 MCP 880005（涨跌家数指标，Amount=全市场成交额）、ETF 日线
  2) 新浪财经板块快照（行业 / 概念 / 地域），GBK 编码
所有字段原样落盘；不编造资金流、份额、国家队持仓数据。
"""
import json
import os
import re
import urllib.request

import tdx_direct as tdx

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "workbench.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# (代码, 显示名, 宽基分类 / 主题标签, 是否重点宽基)
ETF = [
    ("510300", "沪深300ETF", "沪深300", 1), ("159919", "300ETF(深)", "沪深300", 1),
    ("510310", "300ETF(易)", "沪深300", 1), ("510050", "上证50ETF", "上证50", 1),
    ("510500", "中证500ETF", "中证500", 1), ("159922", "500ETF(深)", "中证500", 1),
    ("512100", "中证1000ETF", "中证1000", 1), ("159845", "1000ETF(深)", "中证1000", 1),
    ("159915", "创业板ETF", "创业板", 1), ("159949", "创业板50ETF", "创业板", 1),
    ("588000", "科创50ETF", "科创板", 1), ("588080", "科创板50ETF", "科创板", 1),
    ("510180", "上证180ETF", "上证180", 1), ("159901", "深100ETF", "深证100", 1),
    ("512050", "中证A50ETF", "中证A50", 1), ("563300", "中证2000ETF", "中证2000", 1),
    ("159338", "中证A500ETF", "中证A500", 1), ("515800", "中证800ETF", "中证800", 1),
    ("159629", "1000ETF(广)", "中证1000", 1), ("510330", "300ETF(华)", "沪深300", 1),
    ("512880", "证券ETF", "券商", 0), ("512480", "半导体ETF", "半导体", 0),
    ("515790", "光伏ETF", "光伏", 0), ("515030", "新能源车ETF", "新能源车", 0),
    ("512690", "酒ETF", "白酒", 0), ("512170", "医疗ETF", "医疗", 0),
    ("512660", "军工ETF", "军工", 0), ("515880", "通信ETF", "通信", 0),
    ("512800", "银行ETF", "银行", 0), ("515220", "煤炭ETF", "煤炭", 0),
    ("512400", "有色ETF", "有色", 0), ("159928", "消费ETF", "消费", 0),
]

SINA = [
    ("行业", "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"),
    ("概念", "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"),
    ("地域", "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=area"),
]


def kline(code, want=700, start=0, setcode=None):
    if setcode is None:
        setcode = "1" if code[0] == "5" else "0"
    txt = tdx.call_text("tdx_kline", {
        "code": code, "setcode": setcode, "period": "4",
        "wantNum": str(want), "startxh": str(start), "tqFlag": "0",
    })
    rows = tdx.parse_rows(txt)
    m = re.search(r'"Name":\s*"([^"]+)"', txt)
    return (m.group(1) if m else ""), rows


def fetch_market():
    """880005 全历史：成交额 Amount + 涨跌家数（与 breadth.json 同源，补 Amount 字段）"""
    merged = {}
    for start in range(0, 4200, 700):
        _, rows = kline("880005", 700, start, "1")
        if not rows:
            break
        for r in rows:
            merged[str(r["Data"])] = [
                str(r["Data"]), int(float(r["Amount"])), int(float(r["Volume"])),
                int(float(r["Open"])), int(float(r["Close"])), int(float(r["Low"])),
                int(float(r["High"])),
            ]
        if len(rows) < 700:
            break
    return [merged[k] for k in sorted(merged)]


def fetch_etfs():
    out = {}
    for code, name, cat, core in ETF:
        try:
            tdx_name, rows = kline(code, 700)
        except Exception as e:
            print(f"  ETF {code} {name}: ERR {str(e)[:60]}")
            continue
        if not rows:
            print(f"  ETF {code} {name}: 空数据，跳过")
            continue
        series = []
        for r in rows:
            try:
                series.append([str(r["Data"]), round(float(r["Close"]), 4),
                               int(float(r["Amount"])), int(float(r["Volume"]))])
            except (ValueError, KeyError):
                continue
        out[code] = {"name": name or tdx_name, "tdxName": tdx_name,
                     "cat": cat, "core": core, "rows": series}
        print(f"  ETF {code} {name:14s} {len(series):4d} 根  {series[0][0]} ~ {series[-1][0]}")
    return out


def fetch_boards():
    boards = []
    for cat, url in SINA:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
            s = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
            m = re.search(r"=\s*(\{.*\})", s, re.S)
            if not m:
                print(f"  板块 {cat}: 解析失败")
                continue
            d = json.loads(m.group(1))
            n = 0
            for k, v in d.items():
                f = v.split(",")
                if len(f) < 8:
                    continue
                try:
                    boards.append({
                        "code": f[0], "name": f[1], "cat": cat,
                        "count": int(f[2]), "chg": round(float(f[5]), 2),
                        "amount": int(float(f[7])),
                        "leader": f[12] if len(f) > 12 else "",
                        "leaderCode": f[8] if len(f) > 8 else "",
                        "leaderChg": round(float(f[9]), 2) if len(f) > 9 and f[9] else None,
                    })
                    n += 1
                except (ValueError, IndexError):
                    continue
            print(f"  板块 {cat}: {n} 个")
        except Exception as e:
            print(f"  板块 {cat}: ERR {str(e)[:80]}")
    return boards


def main():
    tdx.init()
    print("=== 全市场成交额（880005） ===")
    market = fetch_market()
    print(" 共", len(market), "根", market[0][0], "~", market[-1][0],
          "最新成交额(元)", market[-1][1] if market else "—")
    if len(market) < 500:
        raise RuntimeError("全市场成交额样本不足，终止")
    print("=== ETF 日线 ===")
    etfs = fetch_etfs()
    print("=== 新浪板块快照 ===")
    boards = fetch_boards()
    idx = json.load(open(os.path.join(HERE, "data", "indices.json"), encoding="utf-8"))
    px = {r[0]: r[1] for r in idx["indices"]["sh000001"]["rows"]}
    payload = {
        "meta": {
            "marketSource": "通达信 880005 指标日线 Amount 字段（全市场成交额，单位元）",
            "etfSource": "通达信 MCP ETF 日线（不复权收盘 / 成交额 / 成交量）",
            "boardSource": "新浪财经板块快照（行业 / 概念 / 地域），当日收盘横截面，GBK 解码",
            "indexSource": idx["source"],
            "asof": market[-1][0], "fetchedAt": "2026-11-14",
            "caveat": "无公开日度 ETF 份额/申赎与真实资金流数据；国家队持仓仅季度披露。页面中的『净流入』『主动净买入』『护盘』均为量价代理，不是真实资金数据。",
        },
        "market": market, "etfs": etfs, "boards": boards, "px": px,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("saved:", OUT, round(os.path.getsize(OUT) / 1024, 1), "KB")


if __name__ == "__main__":
    main()
