#!/usr/bin/env python
"""6 类专业补充数据的统一取数与逐日累积。

输出：data/extras.json（工作台新增栏目唯一数据源）

数据源与口径（2026-11-15 实测）
--------------------------------
  1) 衍生品/对冲压力
     - 中金所日行情 XML  http://www.cffex.com.cn/sj/hqsj/rtj/YYYYMM/DD/index.xml
       含 IF/IC/IM/IH 期货全部合约 + HO/IO/MO 期权全部合约的收盘/结算/持仓/成交量/delta。
       可按交易日回补历史；本脚本默认补最近 --days 个 A 股交易日。
     - 中金所会员持仓排名 XML http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IF|IC|IM|IH}.xml
       datatypeid 0/1/2 按中金所排名表惯例＝成交量 / 持买单量 / 持卖单量（前 20 会员）。
     - 现货指数：腾讯 sh000300 / sh000905 / sh000852 / sh000016 日线。
     基差＝当月合约收盘−现货收盘；年化＝基差/现货×365/剩余自然日。
     PCR＝Σ认沽/Σ认购（成交量、持仓量各算一次）。
     ⚠ XML 无隐含波动率字段，真实 IV / IV skew 未接入，页面显式标注，不做推算冒充。

  2) 真实资金与杠杆
     - 两融全市场历史：东财 RPTA_RZRQ_LSHJ（分页，T+1 披露）。
     - ETF 真实申赎份额：东财 F10 gmbd（http 域名可用），季度频率，期间申购/赎回/期末份额。
     - ETF 日度「份额估计」：当日总市值 f116 / 当日收盘价反推，日差分＝净申赎估计。
       ⚠ 交易所公布的 ETF 日度申赎份额无免费公开源；估计值含折溢价与计价口径误差，
         且只从入库首日起累积，不回补历史。本栏目不做「国家队」归因。

  3) 筹码供给与事件：限售解禁、龙虎榜、业绩预告（东财数据中心）。

  4) 微观结构 / 短线情绪：东财涨停池 / 跌停池 / 炸板池当日快照。
     ⚠ 历史 date 参数实测失效（请求 20261112 仍返回 qdate=20261113），
       因此只从入库首日起逐日累积，绝不按 date 回补伪造历史。

  5) 估值与盈利预期：东财行业板块 PE（f9，单独请求，与成交额分两次取）、
     业绩预告的预告类型与变动幅度（一致预期修正的代理，不是分析师一致预期）。

  6) 跨市场与宏观：恒指/纳指/道指/标普/美元指数/COMEX 黄金、人民币汇率、中债 10Y。

用法：
  python fetch_extras.py            # 增量：刷新当日快照 + 补最近 5 个缺失交易日
  python fetch_extras.py --full     # 首次：回补最近 60 个交易日的 CFFEX 日行情
  python fetch_extras.py --days 120 # 指定回补天数
"""
import argparse
import calendar
import datetime as dt
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "extras.json")
WORKBENCH = os.path.join(DATA, "workbench.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
E = "https://datacenter-web.eastmoney.com/api/data/v1/get"
P = "https://push2.eastmoney.com/api/qt"
CFFEX = "http://www.cffex.com.cn"

FUT_SPOT = {"IF": "sh000300", "IH": "sh000016", "IC": "sh000905", "IM": "sh000852"}
OPT_UNDER = {"IO": "沪深300", "HO": "上证50", "MO": "中证1000"}
SPOT_CODES = ["sh000300", "sh000905", "sh000852", "sh000016"]
SPOT_NAMES = ["沪深300", "中证500", "中证1000", "上证50"]

CORE_ETF = ["510300", "510050", "510500", "512100", "588000", "159915", "159919", "510180"]

WARN = []
FALLBACK = []       # 允许的降级：本次取数失败但已有上一版有效数据，明确记录「非当日新数据」


def warn(msg):
    WARN.append(msg)
    print("  [warn]", msg)


def fallback(msg):
    FALLBACK.append(msg)
    print("  [fallback]", msg)


def curl(url, referer=None, retries=3, timeout=20):
    cmd = ["curl", "-sS", "-m", str(timeout), "--compressed", "-A", UA]
    if referer:
        cmd += ["-H", "Referer: " + referer]
    cmd.append(url)
    for i in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout + 5)
            if r.stdout and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return ""


def jget(url, referer=None, retries=3):
    b = curl(url, referer, retries)
    try:
        return json.loads(b)
    except Exception:
        return {}


def num(v, default=None):
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except Exception:
        return default


def jload(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def jdump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def xml_tag(block, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
    return m.group(1).strip() if m else ""


def trade_dates(n=90):
    w = jload(WORKBENCH, {})
    rows = w.get("market") or []
    return [r[0] for r in rows[-n:]]


def third_friday(y, m):
    cal = calendar.monthcalendar(y, m)
    fr = [w[calendar.FRIDAY] for w in cal if w[calendar.FRIDAY] != 0]
    return dt.date(y, m, fr[2])


def expiry_of(contract):
    """IF2611 -> 2026-11 第三个周五。"""
    tail = contract[-4:]
    if not tail.isdigit():
        return None
    y, m = 2000 + int(tail[:2]), int(tail[2:])
    if not 1 <= m <= 12:
        return None
    return third_friday(y, m)


# ---------------------------------------------------------------- 现货指数
def fetch_spot():
    """现货指数日线收盘（衍生品基差的现货端）。

    2026-11-17 根治：原固定区间 + qfq 通道 web.ifzq.gtimg.cn/.../fqkline/get
    实测对 4 个指数全部返回 0 根（16:00 主任务因此每天写空基差并告警）。
    改用与主任务同源的 tx_kline（腾讯双域名 + 新浪日线降级），已实测当日
    4 指数均可取到收盘。函数内延迟 import 避免模块顶层循环依赖。
    """
    import daily_update as du
    out = {}
    for code in SPOT_CODES:
        try:
            rows = du.tx_kline(code, 260, allow_sina=True)
        except Exception as e:
            print("  现货", code, "日线失败:", str(e)[:100])
            rows = []
        for r in rows:
            try:
                out.setdefault(r[0].replace("-", ""), {})[code] = float(r[2])
            except Exception:
                continue
        print("  现货", code, len(rows), "根")
    if not out:
        warn("现货指数日线抓取失败，基差将缺现货端")
    return out


# ---------------------------------------------------------------- CFFEX 日行情
def cffex_daily_url(d):
    return "%s/sj/hqsj/rtj/%s/%s/index.xml" % (CFFEX, d[:6], d[6:8])


def parse_cffex_day(raw, date, spot):
    """返回 (futures_rows, options_rows, option_delta)。"""
    blocks = re.findall(r"<dailydata>(.*?)</dailydata>", raw or "", re.S)
    if not blocks:
        return None
    fut, opt = {}, {}
    for b in blocks:
        pid = xml_tag(b, "productid")
        if pid in FUT_SPOT:
            fut.setdefault(pid, []).append({
                "id": xml_tag(b, "instrumentid"),
                "close": num(xml_tag(b, "closeprice")),
                "settle": num(xml_tag(b, "settlementprice")),
                "oi": num(xml_tag(b, "openinterest"), 0),
                "vol": num(xml_tag(b, "volume"), 0),
                "exp": expiry_of(xml_tag(b, "instrumentid")),
            })
        elif pid in OPT_UNDER:
            opt.setdefault(pid, []).append({
                "id": xml_tag(b, "instrumentid"),
                "cp": "C" if "-C-" in xml_tag(b, "instrumentid") else ("P" if "-P-" in xml_tag(b, "instrumentid") else ""),
                "vol": num(xml_tag(b, "volume"), 0),
                "oi": num(xml_tag(b, "openinterest"), 0),
                "settle": num(xml_tag(b, "settlementprice")),
                "delta": num(xml_tag(b, "delta")),
            })
    frows, orows, delta = {}, {}, {}
    sp = spot.get(date, {})
    for pid, arr in fut.items():
        arr = [a for a in arr if a["close"] and a["exp"]]
        if not arr:
            continue
        arr.sort(key=lambda a: a["exp"])
        near = arr[0]
        second = arr[1] if len(arr) > 1 else arr[0]
        far = arr[-1]
        today = dt.date(int(date[:4]), int(date[4:6]), int(date[6:8]))
        s = sp.get(FUT_SPOT[pid])
        days = max(1, (near["exp"] - today).days)
        days2 = max(1, (second["exp"] - today).days)
        basis = near["close"] - s if s else None
        basis2 = second["close"] - s if s else None
        ann = (basis / s * 365 / days * 100) if (basis is not None and s) else None
        # 次月合约年化：临近到期时当月年化会被 365/days 放大，次月口径更平滑
        ann2 = (basis2 / s * 365 / days2 * 100) if (basis2 is not None and s) else None
        frows[pid] = {
            "near": near["id"], "close": near["close"], "oi": near["oi"], "vol": near["vol"],
            "spot": s, "basis": basis, "ann": ann, "days": days,
            "second": second["id"], "secondClose": second["close"],
            "basis2": basis2, "ann2": ann2, "days2": days2,
            "far": far["id"], "farClose": far["close"], "spread": near["close"] - far["close"],
            "totalOI": sum(a["oi"] for a in arr), "totalVol": sum(a["vol"] for a in arr),
            "contracts": [{"id": a["id"], "close": a["close"], "oi": a["oi"], "vol": a["vol"],
                           "exp": a["exp"].isoformat()} for a in arr],
        }
    for pid, arr in opt.items():
        c = [a for a in arr if a["cp"] == "C"]
        p = [a for a in arr if a["cp"] == "P"]
        cv, pv = sum(a["vol"] for a in c), sum(a["vol"] for a in p)
        co, po = sum(a["oi"] for a in c), sum(a["oi"] for a in p)
        orows[pid] = {"cv": cv, "pv": pv, "co": co, "po": po,
                      "pcrV": pv / cv if cv else None, "pcrOI": po / co if co else None,
                      "nC": len(c), "nP": len(p),
                      "callSettle": sum(a["settle"] or 0 for a in c) / (len(c) or 1),
                      "putSettle": sum(a["settle"] or 0 for a in p) / (len(p) or 1)}
        # delta 分布（真实字段，仅当日展示；不是隐含波动率）
        delta[pid] = {
            "call": [round(a["delta"], 3) for a in c if a["delta"] is not None],
            "put": [round(a["delta"], 3) for a in p if a["delta"] is not None],
        }
    return frows, orows, delta


def fetch_cffex_history(dates):
    out = {}

    def one(d):
        return d, curl(cffex_daily_url(d), retries=2, timeout=22)

    with ThreadPoolExecutor(max_workers=6) as ex:
        for d, raw in ex.map(one, dates):
            out[d] = raw
    ok = sum(1 for v in out.values() if "<dailydata>" in v)
    print("  CFFEX 日行情:", ok, "/", len(dates), "个交易日")
    if ok < len(dates):
        warn("CFFEX 日行情缺 %d 个交易日（非交易日或接口失败），图中留空不插值" % (len(dates) - ok))
    return out


def fetch_cffex_rank(date):
    out = {}

    def one(pid):
        u = "%s/sj/ccpm/%s/%s/%s.xml" % (CFFEX, date[:6], date[6:8], pid)
        raw = curl(u, retries=2, timeout=20)
        rows = re.findall(r"<data .*?</data>", raw or "", re.S)
        buy, sell, vol = {}, {}, {}
        for r in rows:
            t = xml_tag(r, "datatypeid")
            name = xml_tag(r, "shortname")
            v = num(xml_tag(r, "volume"), 0) or 0
            if t == "1":
                buy[name] = buy.get(name, 0) + v
            elif t == "2":
                sell[name] = sell.get(name, 0) + v
            elif t == "0":
                vol[name] = vol.get(name, 0) + v
        names = set(buy) | set(sell)
        top = sorted(([n, buy.get(n, 0), sell.get(n, 0), buy.get(n, 0) - sell.get(n, 0)]
                      for n in names), key=lambda x: -abs(x[3]))[:12]
        return pid, {"date": date, "buy": sum(buy.values()), "sell": sum(sell.values()),
                     "net": sum(buy.values()) - sum(sell.values()),
                     "volumeTop": sum(vol.values()), "top": top}

    with ThreadPoolExecutor(max_workers=4) as ex:
        for pid, v in ex.map(one, list(FUT_SPOT)):
            out[pid] = v
    if not any(v["buy"] for v in out.values()):
        warn("中金所会员持仓排名抓取失败，本日净多空留空")
    return out


# ---------------------------------------------------------------- 两融
def fetch_margin():
    rows = []
    for page in range(1, 8):
        u = (E + "?reportName=RPTA_RZRQ_LSHJ&columns=ALL&pageSize=800&pageNumber=%d"
             "&sortColumns=DIM_DATE&sortTypes=-1&source=WEB&client=WEB" % page)
        d = ((jget(u, retries=2).get("result") or {}).get("data") or [])
        if not d:
            break
        rows.extend(d)
        if len(d) < 800:
            break
    out = []
    for r in rows:
        d = (r.get("DIM_DATE") or "")[:10].replace("-", "")
        if len(d) != 8:
            continue
        out.append([d,
                    num(r.get("RZYE")), num(r.get("RZMRE")), num(r.get("RZJME")),
                    num(r.get("RQYE")), num(r.get("RZRQYE")),
                    num(r.get("RZYEZB")), num(r.get("LTSZ"))])
    out.sort(key=lambda x: x[0])
    print("  两融历史:", len(out), "行", out[0][0] if out else "-", "~", out[-1][0] if out else "-")
    if not out:
        warn("两融历史抓取失败")
    return out


# ---------------------------------------------------------------- ETF 申赎
def fetch_etf_quarterly():
    out = {}

    def one(code):
        u = ("http://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&mode=0"
             "&code=%s&rt=0.1" % code)
        raw = curl(u, "http://fundf10.eastmoney.com/", retries=4, timeout=25)
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", raw or "", re.S)[1:]:
            cells = [re.sub(r"<.*?>", "", c).replace("&nbsp;", " ").strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if len(cells) >= 4 and re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
                rows.append([cells[0].replace("-", ""),
                             num(cells[1]), num(cells[2]), num(cells[3]),
                             num(cells[4].replace(",", "")) if len(cells) > 4 else None])
        return code, rows[:12]

    with ThreadPoolExecutor(max_workers=2) as ex:
        for code, rows in ex.map(one, CORE_ETF):
            if rows:
                out[code] = rows
    # 整批断连时再补一轮，季度数据不常变，但也不该静默沿用旧值
    miss = [c for c in CORE_ETF if c not in out]
    if miss:
        with ThreadPoolExecutor(max_workers=2) as ex:
            for code, rows in ex.map(one, miss):
                if rows:
                    out[code] = rows
    print("  ETF 季度真实申赎:", len(out), "只 /", sum(len(v) for v in out.values()), "期")
    if not out:
        warn("ETF 季度申赎抓取失败")
    return out


def fetch_etf_shares_est(date):
    """份额估计 = 当日总市值 f116 / 当日收盘价（workbench.json ETF 日线）。"""
    w = jload(WORKBENCH, {})
    etfs = w.get("etfs") or {}
    px = {}
    for code, o in etfs.items():
        for r in (o.get("rows") or []):
            if r[0] == date:
                px[code] = r[1]

    def one(code):
        secid = ("1." if code[0] == "5" else "0.") + code
        u = P + "/stock/get?secid=%s&fields=f57,f58,f43,f116,f117" % secid
        d = (jget(u, "https://quote.eastmoney.com/", retries=3).get("data") or {})
        mv = num(d.get("f116")) or num(d.get("f117"))
        close = num(d.get("f43"))
        if close and close > 100:      # 行情价格按 1/1000 缩放
            close = close / 1000.0
        if mv and close:
            return code, round(mv / close / 1e8, 4)   # 亿份
        return code, None

    # 该端点在本机代理下有约一半概率 schannel 断连，低并发 + 多轮重试补齐
    res = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        for code, v in ex.map(one, list(etfs.keys())):
            res[code] = v
    for round_ in range(2):
        miss = [c for c, v in res.items() if v is None]
        if not miss:
            break
        with ThreadPoolExecutor(max_workers=2) as ex:
            for code, v in ex.map(one, miss):
                if v is not None:
                    res[code] = v
    ok = sum(1 for v in res.values() if v is not None)
    print("  ETF 份额估计:", ok, "/", len(etfs), "只")
    if ok < len(etfs) * 0.6:
        warn("ETF 当日市值端点本次仅成功 %d/%d 只，份额估计样本不足，页面标注" % (ok, len(etfs)))
    return res


# ---------------------------------------------------------------- 短线情绪
POOL = {
    "zt": "https://push2ex.eastmoney.com/getTopicZTPool",
    "dt": "https://push2ex.eastmoney.com/getTopicDTPool",
    "zb": "https://push2ex.eastmoney.com/getTopicZBPool",
}
POOL_PARAM = {
    "zt": "ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt:asc&date={d}",
    "dt": "ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fund:asc&date={d}",
    "zb": "ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt:asc&date={d}",
}


def fetch_sentiment(date):
    pools = {}
    for k, base in POOL.items():
        d = (jget(base + "?" + POOL_PARAM[k].format(d=date),
                  "https://quote.eastmoney.com/", retries=3).get("data") or {})
        pools[k] = {"tc": num(d.get("tc"), 0), "qdate": str(d.get("qdate") or ""),
                    "pool": d.get("pool") or []}
    zt, dt_, zb = pools["zt"], pools["dt"], pools["zb"]
    # 服务端 qdate 与请求日期不一致时，不写入历史（防止把当日数据伪装成历史）
    served = {str(x["qdate"]) for x in pools.values() if x["qdate"]}
    if served and served != {date}:
        warn("涨跌停池返回的 qdate=%s 与请求 %s 不一致，本次只作当日快照、不回补历史"
             % (",".join(sorted(served)), date))
    lbc = [num(x.get("lbc"), 0) or 0 for x in zt["pool"]]
    maxlb = max(lbc) if lbc else 0
    lb2 = sum(1 for v in lbc if v >= 2)
    nzt, nzb = zt["tc"] or len(zt["pool"]), zb["tc"] or len(zb["pool"])
    return {
        "date": date,
        "zt": nzt, "dt": dt_["tc"] or len(dt_["pool"]), "zb": nzb,
        "maxLb": maxlb, "lb2": lb2,
        "zbRate": round(nzb / (nzt + nzb) * 100, 2) if (nzt + nzb) else None,
        "ztCodes": [x.get("c") for x in zt["pool"]],
        "lbCodes": [x.get("c") for x in zt["pool"] if (num(x.get("lbc"), 0) or 0) >= 2],
        "top": [{"code": x.get("c"), "name": x.get("n"), "lbc": x.get("lbc"),
                 "zbc": x.get("zbc"), "hybk": x.get("hybk"),
                 "fund": num(x.get("fund"))} for x in
                sorted(zt["pool"], key=lambda x: -(num(x.get("lbc"), 0) or 0))[:12]],
        "dtTop": [{"code": x.get("c"), "name": x.get("n"), "hybk": x.get("hybk")}
                  for x in dt_["pool"][:8]],
    }


# ---------------------------------------------------------------- 行业估值 / 预期
def fetch_industry():
    codes = ["BK%04d" % i for i in range(400, 1101)]
    batches = [codes[i:i + 55] for i in range(0, len(codes), 55)]
    pe, px = {}, {}

    def one(b):
        ids = ",".join("90." + c for c in b)
        a = jget(P + "/ulist.np/get?secids=%s&fltt=2&fields=f12,f14,f9" % ids,
                 "https://quote.eastmoney.com/", retries=2)
        c = jget(P + "/ulist.np/get?secids=%s&fltt=2&fields=f12,f14,f6,f3" % ids,
                 "https://quote.eastmoney.com/", retries=2)
        r = {}
        for x in ((a.get("data") or {}).get("diff") or []):
            if x.get("f12") and isinstance(x.get("f9"), (int, float)):
                r.setdefault(x["f12"], {})["pe"] = float(x["f9"])
                r[x["f12"]]["name"] = x.get("f14")
        for x in ((c.get("data") or {}).get("diff") or []):
            if x.get("f12"):
                r.setdefault(x["f12"], {})["name"] = x.get("f14")
                if isinstance(x.get("f6"), (int, float)):
                    r[x["f12"]]["amt"] = float(x["f6"])
                if isinstance(x.get("f3"), (int, float)):
                    r[x["f12"]]["chg"] = float(x["f3"])
        return r

    def collect(bs):
        with ThreadPoolExecutor(max_workers=3) as ex:
            return list(ex.map(one, bs))

    parts = collect(batches)
    for _round in range(2):
        # 某个批次整批断连时补抓，避免横截面样本数随机腰斩
        empty = [b for b, part in zip(batches, parts) if len(part) < 5]
        if not empty:
            break
        fixed = collect(empty)
        fi = iter(fixed)
        parts = [next(fi) if len(part) < 5 else part for part in parts]
    for part in parts:
        for k, v in part.items():
            pe.setdefault(k, {}).update(v)
    rows = [{"code": k, "name": v.get("name") or k, "pe": v.get("pe"),
             "amt": v.get("amt"), "chg": v.get("chg")}
            for k, v in pe.items() if v.get("pe") is not None and v["pe"] > 0]
    rows.sort(key=lambda x: x["pe"])
    print("  行业 PE:", len(rows), "个（已剔除 PE<=0 / 缺失）")
    if len(rows) < 20:
        warn("行业 PE 本次仅取到 %d 个，横截面分位样本不足，页面标注" % len(rows))
    return rows


def fetch_forecast():
    u = (E + "?reportName=RPT_PUBLIC_OP_NEWPREDICT&columns=ALL&pageSize=200&pageNumber=1"
         "&sortColumns=notice_date&sortTypes=-1&source=WEB&client=WEB")
    data = ((jget(u, retries=2).get("result") or {}).get("data") or [])
    rows = []
    for r in data:
        rows.append({
            "date": (r.get("NOTICE_DATE") or "")[:10].replace("-", ""),
            "code": r.get("SECURITY_CODE"), "name": r.get("SECURITY_NAME_ABBR"),
            "type": r.get("PREDICT_FINANCE"), "content": (r.get("PREDICT_CONTENT") or "")[:80],
            "lo": num(r.get("ADD_AMP_LOWER")), "hi": num(r.get("ADD_AMP_UPPER")),
            "kind": r.get("PREDICT_KIND") or r.get("PREDICT_TYPE"),
        })
    up = sum(1 for r in rows if (r["lo"] or 0) > 0)
    dn = sum(1 for r in rows if (r["hi"] or 0) < 0)
    print("  业绩预告:", len(rows), "条 | 预增", up, "预减", dn)
    if not rows:
        warn("业绩预告抓取失败")
    return {"rows": rows[:60], "total": len(rows), "up": up, "down": dn}


# ---------------------------------------------------------------- 事件
def fetch_events():
    out = {}
    specs = {
        "unlock": (E + "?reportName=RPT_LIFT_STAGE&columns=ALL&pageSize=120&pageNumber=1"
                   "&sortColumns=FREE_DATE&sortTypes=1&filter=(FREE_DATE%3E%3D%27{t}%27)&source=WEB&client=WEB",
                   lambda r: {"date": (r.get("FREE_DATE") or "")[:10].replace("-", ""),
                              "code": r.get("SECURITY_CODE"), "name": r.get("SECURITY_NAME_ABBR"),
                              "cap": num(r.get("LIFT_MARKET_CAP")), "ratio": num(r.get("FREE_RATIO")),
                              "kind": r.get("FREE_SHARES_TYPE")}),
        "billboard": (E + "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=80&pageNumber=1"
                      "&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB",
                      lambda r: {"date": (r.get("TRADE_DATE") or "")[:10].replace("-", ""),
                                 "code": r.get("SECURITY_CODE"), "name": r.get("SECURITY_NAME_ABBR"),
                                 "chg": num(r.get("CHANGE_RATE")),
                                 "amt": num(r.get("BILLBOARD_DEAL_AMT")),
                                 "ratio": num(r.get("DEAL_AMOUNT_RATIO")),
                                 "reason": r.get("EXPLANATION") or r.get("EXPLAIN")}),
    }
    today = dt.date.today().isoformat()
    for key, (tpl, fn) in specs.items():
        url = tpl.format(t=today)
        data = ((jget(url, retries=2).get("result") or {}).get("data") or [])
        try:
            out[key] = [fn(r) for r in data][:60]
        except Exception:
            out[key] = []
        print("  事件", key, len(out[key]))
        if not out[key]:
            warn("%s 事件抓取失败" % key)
    return out


# ---------------------------------------------------------------- 跨市场
CROSS_IDS = "100.HSI,100.NDX,100.DJIA,100.SPX,100.UDI,101.GC00Y"


def fetch_cross():
    # 逐个抓，避免某个 secid 让整批请求被代理断连
    def one(sid):
        d = (jget(P + "/ulist.np/get?fltt=2&invt=2&secids=%s&fields=f1,f2,f3,f4,f12,f13,f14"
                  % sid, "https://quote.eastmoney.com/", retries=3).get("data") or {})
        return (d.get("diff") or [{}])[0]
    diffs = []
    sids = CROSS_IDS.split(",")
    with ThreadPoolExecutor(max_workers=3) as ex:
        diffs = list(ex.map(one, sids))
    for _round in range(2):
        miss = [s for s, x in zip(sids, diffs) if not x.get("f12")]
        if not miss:
            break
        with ThreadPoolExecutor(max_workers=2) as ex:
            fixed = dict(zip(miss, ex.map(one, miss)))
        diffs = [fixed.get(s, x) if not x.get("f12") else x for s, x in zip(sids, diffs)]
    quotes = [{"code": x.get("f12"), "name": x.get("f14"), "price": num(x.get("f2")),
               "chg": num(x.get("f3"))} for x in diffs if x.get("f12")]
    fx = {"usdcny": None, "usdcnh": None, "date": ""}
    raw = curl("https://hq.sinajs.cn/list=fx_susdcny,fx_susdcnh",
               "https://finance.sina.com.cn/", retries=2)
    m = re.search(r'fx_susdcny="([^"]+)"', raw or "")
    if m:
        p = m.group(1).split(",")
        fx["usdcny"] = num(p[1] if len(p) > 1 else None)
        fx["date"] = p[-1] if p else ""
    m = re.search(r'fx_susdcnh="([^"]+)"', raw or "")
    if m:
        fx["usdcnh"] = num(m.group(1).split(",")[1])
    cn = ((jget(E + "?reportName=RPTA_WEB_TREASURYYIELD&columns=ALL&pageSize=3&pageNumber=1"
                "&sortColumns=SOLAR_DATE&sortTypes=-1&source=WEB&client=WEB", retries=2)
           .get("result") or {}).get("data") or [])
    bond = {"date": "", "y10": None, "rows": []}
    for r in cn:
        try:
            bond["rows"].append([(r.get("SOLAR_DATE") or "")[:10].replace("-", ""),
                                 num(r.get("EMM00166469"))])
        except Exception:
            pass
    if bond["rows"]:
        bond["date"] = bond["rows"][0][0]
        bond["y10"] = bond["rows"][0][1]
    print("  跨市场:", len(quotes), "个标的 | 汇率", fx["usdcny"], "| 中债10Y", bond["y10"])
    if not quotes:
        warn("外盘行情抓取失败")
    return {"quotes": quotes, "fx": fx, "bond": bond}


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="首次回补 CFFEX 历史")
    ap.add_argument("--days", type=int, default=60, help="--full 回补交易日数")
    ap.add_argument("--date", default="", help="指定 asof（YYYYMMDD）")
    args = ap.parse_args()

    old = jload(OUT, {})
    dates = trade_dates(max(200, args.days + 10))
    if not dates:
        print("workbench.json 缺少交易日历，无法运行")
        sys.exit(1)
    asof = args.date or dates[-1]
    print("asof =", asof, "| 交易日历", dates[0], "~", dates[-1])

    # 1. 现货指数
    print("[1/8] 现货指数日线")
    spot = fetch_spot()

    # 2. CFFEX 日行情（历史回补 + 增量）
    print("[2/8] 中金所日行情")
    want = dates[-args.days:] if args.full else dates[-5:]
    have = set((old.get("derivatives") or {}).get("dates") or [])
    todo = [d for d in want if d not in have]
    raw_by_date = fetch_cffex_history(todo)

    der = old.get("derivatives") or {}
    fseries = der.get("futures") or {p: [] for p in FUT_SPOT}
    oseries = der.get("options") or {p: [] for p in OPT_UNDER}
    dlist = list(der.get("dates") or [])
    for d in todo:
        parsed = parse_cffex_day(raw_by_date.get(d, ""), d, spot)
        if not parsed:
            continue
        fr, op, _delta = parsed
        dlist.append(d)
        for p in FUT_SPOT:
            fseries.setdefault(p, []).append([d] + [fr.get(p, {}).get(k) for k in
                ("close", "spot", "basis", "ann", "oi", "vol", "totalOI", "farClose",
                 "spread", "days", "near", "secondClose", "ann2", "days2", "second")])
        for p in OPT_UNDER:
            o = op.get(p, {})
            oseries.setdefault(p, []).append([d, o.get("cv"), o.get("pv"), o.get("co"),
                                              o.get("po"), o.get("pcrV"), o.get("pcrOI"),
                                              o.get("nC"), o.get("nP")])
    dlist = sorted(set(dlist))
    for p in fseries:
        fseries[p] = [r for r in fseries[p] if r[0] in set(dlist)]
        fseries[p].sort(key=lambda r: r[0])
    for p in oseries:
        oseries[p] = [r for r in oseries[p] if r[0] in set(dlist)]
        oseries[p].sort(key=lambda r: r[0])

    # 当日全合约明细 + delta 分布
    snap = parse_cffex_day(raw_by_date.get(asof, "") or curl(cffex_daily_url(asof), retries=2), asof, spot)
    contracts, delta = {}, {}
    if snap:
        fr, op, delta = snap
        contracts = {p: {"spot": FUT_SPOT[p], "rows": fr.get(p, {}).get("contracts", []),
                         **{k: v for k, v in fr.get(p, {}).items() if k != "contracts"}}
                     for p in FUT_SPOT if p in fr}
        optsnap = {p: dict(v, underlying=OPT_UNDER[p]) for p, v in op.items()}
    else:
        optsnap = {}
        warn("当日 CFFEX 日行情未取到，衍生品快照留空")
    print("[3/8] 中金所会员持仓排名")
    rank = fetch_cffex_rank(asof)

    # 4. 两融
    print("[4/8] 两融全历史")
    margin = fetch_margin() or (old.get("leverage") or {}).get("rows") or []

    # 5. ETF 申赎
    print("[5/8] ETF 申赎")
    _q = fetch_etf_quarterly()
    if not _q:
        fallback("ETF 季度申赎本次抓取失败，沿用上一版季报数据（非当日新数据）")
    quarterly = _q or (old.get("etfFlow") or {}).get("quarterly") or {}
    shares_new = fetch_etf_shares_est(asof)
    etf_old = old.get("etfFlow") or {}
    daily_old = etf_old.get("daily") or []
    prev_same = next((r[1] for r in reversed(daily_old) if r[0] == asof), {}) or {}
    ok_new = sum(1 for v in shares_new.values() if v is not None)
    ok_old = sum(1 for v in prev_same.values() if v is not None)
    # 端点整体限流时绝不写空：成功数跌破上一版一半就沿用，页面读数不会突然归零
    if ok_old > 0 and ok_new < max(1, int(ok_old * 0.5)):
        fallback("ETF 份额估计本次仅 %d 只成功（上一版 %d 只），沿用上一版当日读数（非当日新数据）" % (ok_new, ok_old))
        shares = dict(prev_same)
    else:
        shares = shares_new
    daily = [r for r in daily_old if r[0] != asof] + [[asof, shares]]
    daily.sort(key=lambda r: r[0])

    # 6. 短线情绪
    print("[6/8] 涨跌停 / 炸板")
    sent_new = fetch_sentiment(asof)
    sent_old = old.get("sentiment") or {}
    if not (sent_new["zt"] or sent_new["dt"] or sent_new["zb"]):
        fallback("涨跌停 / 跌停 / 炸板三池本次全空，不写入假的 0 行，沿用上一版历史与快照")
        sent = sent_old.get("snapshot") or sent_new
        srows = list(sent_old.get("rows") or [])
    else:
        sent = sent_new
        srows = [r for r in (sent_old.get("rows") or []) if r[0] != asof]
        prev = next((r for r in reversed(sent_old.get("rows") or []) if r[0] < asof), None)
        promo = None
        # 晋级率＝今日连板(lbc>=2)家数 / 昨日涨停家数；只在已有前一交易日快照时计算
        if prev and prev[1]:
            promo = round(sent["lb2"] / prev[1] * 100, 2)
        srows.append([asof, sent["zt"], sent["dt"], sent["zb"], sent["maxLb"],
                      sent["lb2"], sent["zbRate"], promo])
        srows.sort(key=lambda r: r[0])

    # 7. 估值与事件
    print("[7/8] 行业 PE / 业绩预告 / 事件")
    _ind = fetch_industry()
    if not _ind:
        fallback("行业 PE 本次抓取失败，沿用上一版横截面（非当日新数据）")
    industry = _ind or (old.get("valuation") or {}).get("industry") or []
    pes = sorted(x["pe"] for x in industry if x.get("pe"))
    vstat = None
    if len(pes) >= 20:
        vstat = {"n": len(pes), "min": pes[0], "p25": pes[len(pes) // 4],
                 "median": pes[len(pes) // 2], "p75": pes[len(pes) * 3 // 4],
                 "max": pes[-1], "mean": sum(pes) / len(pes)}
    vdaily_old = (old.get("valuation") or {}).get("daily") or []
    vdaily = [r for r in vdaily_old if r[0] != asof]
    if vstat:
        vdaily.append([asof, vstat["median"], vstat["p25"], vstat["p75"], vstat["n"]])
    else:
        vdaily = list(vdaily_old)      # 取数失败时保留上一版当日行，不写空
    vdaily.sort(key=lambda r: r[0])
    forecast = fetch_forecast() or (old.get("valuation") or {}).get("forecast") or {}
    events = fetch_events() or old.get("events") or {}

    # 8. 跨市场
    print("[8/8] 跨市场与宏观")
    cross = fetch_cross()
    cross_old = old.get("cross") or {}
    if not cross["quotes"] and (cross_old.get("quotes") or []):
        cross["quotes"] = cross_old["quotes"]
        fallback("外盘行情本次全部失败，KPI 沿用上一版读数（非当日新数据）")
    if cross["fx"].get("usdcny") is None and (cross_old.get("fx") or {}).get("usdcny") is not None:
        cross["fx"] = cross_old["fx"]
    if cross["bond"].get("y10") is None and (cross_old.get("bond") or {}).get("y10") is not None:
        cross["bond"] = cross_old["bond"]
    cdaily_old = cross_old.get("daily") or []
    cdaily = [r for r in cdaily_old if r[0] != asof]
    q = {x["code"]: x for x in cross["quotes"]}
    if cross["quotes"] or cross["fx"].get("usdcny") is not None:
        cdaily.append([asof,
                       (q.get("HSI") or {}).get("chg"), (q.get("NDX") or {}).get("chg"),
                       (q.get("SPX") or {}).get("chg"), (q.get("UDI") or {}).get("price"),
                       cross["fx"].get("usdcny"), cross["bond"].get("y10")])
    else:
        cdaily = list(cdaily_old)      # 全部失败时保留上一版，不写全 null 行
    cdaily.sort(key=lambda r: r[0])

    out = {
        "meta": {
            "asof": asof,
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updateNote": "公开源均为盘后 T / T+1 可得，非盘中实时；两融 T+1，中金所行情为当日收盘后公布。",
            "sources": {
                "derivatives": "中金所日行情 XML / 会员持仓排名 XML + 腾讯指数日线",
                "leverage": "东财 RPTA_RZRQ_LSHJ（两融，T+1）",
                "etfQuarterly": "东财基金 F10 gmbd（季度真实申购/赎回/份额）",
                "etfDaily": "东财 f116 总市值 / 收盘价反推的份额估计（非官方申赎）",
                "sentiment": "东财涨停池 / 跌停池 / 炸板池（当日快照）",
                "valuation": "东财行业板块 PE(f9) + 业绩预告 RPT_PUBLIC_OP_NEWPREDICT",
                "events": "东财 RPT_LIFT_STAGE / RPT_DAILYBILLBOARD_DETAILSNEW",
                "cross": "东财外盘行情 + 新浪人民币汇率 + 东财中债国债收益率",
                "history": "backfill_history.py：同花顺涨停/连板池 + 东财 RPT_VALUEINDUSTRY_DET + 腾讯外盘日线 + erp.json 中债10Y",
            },
            "historyBackfill": ("长历史由 backfill_history.py 独立维护在 history.json："
                                "行业 PE 250 日 / 涨停连板晋级率 250 日 / 跨市场 460 日；"
                                "本文件只负责当日快照与逐日累积，不覆盖已回补历史。"),
            "missing": [
                "ETF 交易所公布的日度申赎份额（含历史）：无免费公开源。东财 f116 只有当日快照且无历史接口，腾讯 ETF 日线不含份额/换手率，RPT_FUND_ETFLIST 的 DEC_TOTALSHARE 也只是当前快照。第 07 栏已改用约 700 日真实日度量价作主视觉、季度真实申赎作低频核验，日度份额估计只能逐日累积。",
                "期权隐含波动率 IV / IV skew：中金所 XML 无 IV 字段；本页只给 PCR、持仓与 delta 分布，不推算 IV。",
                "炸板率 / 跌停家数历史：东财三池 date 参数失效、同花顺无对应历史池、逐股缓存无最高价，确实不可回补；涨停家数 / 最高连板 / 连板家数 / 晋级率已用同花顺按日回补 250 日。",
                "TMT 成交占比历史（同口径）：需东财 push2his 板块 K 线的 f57 每日成交额且 11 个板块全齐；实测上游随机限流，每次仅 3~5 个板块返回、6 轮补齐仍不全，用残缺样本加总会系统性低估占比，故不发布。fetch_tmt.py --backfill 带同日一致性校验，覆盖恢复后自动补齐；当前 status=partial。",
                "分析师一致预期修正：无免费源；本页用业绩预告家数与变动幅度作方向代理。",
                "行业 PE 历史横截面：已用东财 RPT_VALUEINDUSTRY_DET 按日回补 250 日（报表口径 127 行业）；当日板块 f9 横截面是另一口径，绝对值不可直接比较。",
                "美元指数 / 人民币汇率日度历史：无免费公开源，跨市场历史列留空，当日 KPI 仍有读数。",
                "Level-2 逐笔委托 / 主动买卖方向 / 封单：无免费源，未接入。",
            ],
            "warnings": WARN,
            "fallbacks": FALLBACK,
            "cffexDays": len(dlist),
        },
        "derivatives": {
            "dates": dlist, "futures": fseries, "options": oseries,
            "futuresFields": ["date", "nearClose", "spot", "basis", "annNear", "nearOI",
                              "nearVol", "totalOI", "farClose", "spread", "daysToExpiry",
                              "nearId", "secondClose", "annSecond", "days2", "secondId"],
            "optionsFields": ["date", "callVol", "putVol", "callOI", "putOI",
                              "pcrVol", "pcrOI", "nCall", "nPut"],
            "snapshot": contracts, "optionSnapshot": optsnap, "delta": delta,
            "rank": rank, "spotNames": SPOT_NAMES,
            "ivNote": "XML 无隐含波动率字段，真实 IV / IV skew 未接入",
            "annualNote": "当月合约临近到期时 365/剩余天数会放大年化，建议同时看次月合约年化",
        },
        "leverage": {"rows": margin, "unit": "元（东财原始口径）"},
        "etfFlow": {"quarterly": quarterly, "daily": daily,
                    "note": "真实申赎为季度频率；日度份额为 f116/收盘价反推的估计值，非官方申赎，不做买方身份归因。"},
        "sentiment": {"fields": ["date", "zt", "dt", "zb", "maxLb", "lb2", "zbRate", "promotion"],
                      "rows": srows, "snapshot": sent},
        "valuation": {"industry": industry, "daily": vdaily, "stat": vstat,
                      "forecast": forecast,
                      "note": "行业 PE 为当日 219 个东财二级行业板块 f9 横截面；中位数/四分位趋势已由 backfill_history.py 用东财 RPT_VALUEINDUSTRY_DET 报表（127 行业）按日回补 250 日，独立存放于 history.json，两套口径样本数不同。"},
        "events": events,
        "cross": {**cross, "daily": cdaily},
    }
    jdump(OUT, out)
    print("\nsaved:", OUT, round(os.path.getsize(OUT) / 1024, 1), "KB")
    print("warnings:", len(WARN))
    for w in WARN:
        print(" -", w)


if __name__ == "__main__":
    main()
