# 数据 Schema 与字段说明

数据均为 UTF-8 JSON，日期字段统一 `yyyymmdd` 字符串。当前 asof 见各文件顶层 `asof` 或 `meta.asof`。

## erp.json — 股债利差（321 KB）

```jsonc
{
  "meta": { "locked": true, "lockedBase": "20261113", "formula": "ERP = 100/沪深300 PE-TTM − 中债10Y", ... },
  "asof": "20261117",
  "series": { "fields": ["date","erp","pe","y10","close","mid","upper","lower"],
              "rows": [["20050429", 3.0815, 13.99, 4.0665, 932.4, null, null, null], ...] },  // 5200+ 行
  "snapshot": { "erp":6.4, "historyPercentile":79.0, "tier":"偏便宜", "peTtm":12.5,
                "earningsYield":8.0, "y10":1.61, "close":3979.8,
                "channelMid":6.18, "channelSigma":0.62, "channelUpper":7.42, "channelLower":4.94,
                "sigmaDev":0.32, "sampleDays":5237,
                "thresholds": [ {"name":"90 分位（低估线）","erp":6.89,"pe":11.76,"point":3744.10}, ... ],
                "condProb": [ {"horizon":20,"samples":1289,"winRate":56.6,"avgRet":1.49}, ... ],
                "event": {"inEvent":false,"runDays":0,"eventCount":34,"toEnterGap":0.50} },
  "lockedSnapshot": { ...首次锁定的全部数字，永不修改... }
}
```
档位规则：≥98 极端低估 / 95-98 深度低估 / 90-95 低估 / 75-90 偏便宜 / 50-75 中性 / 10-50 偏贵 / <10 极贵。

## extras.json — 6 类补充数据（581 KB）

```jsonc
{
  "meta": { "asof":"...", "sources":{...}, "warnings":[], "fallbacks":[], "cffexDays":62, ... },
  "derivatives": { "dates":[[date, 基差, 年化, PCR量, PCR仓, ...], ...],
                   "futures": {"IF":[[date,合约,开,高,低,收,量,仓,...],...]},
                   "options": {"HO":..., "IO":..., "MO":...},
                   "rank": {"IF":[[date, 会员, 持买, 持卖, ...], ...]} },
  "leverage": { "fields":[...], "rows":[["20100331", 融资余额, 融资买入额, 融资净买入, 融券余额, 两融余额, 占流通市值比], ...] },  // 4000+ 行
  "etfFlow":   { "quarterly": {...基金季报申赎...}, "dailyShare": {...反推估计，仅当日...} },
  "sentiment": { "snapshot": { "涨停":37, "跌停":33, "炸板":21, "最高板":7, "炸板率":36.2, "晋级率":... } },
  "valuation": { "industry": [[名称, PE, ...], ...共 219 个板块], "forecast": [...业绩预告 200 条] },
  "events":    { "unlock":[...], "billboard":[...], "predict":[...] },
  "cross":     { "quotes": { "恒生指数":..., "纳斯达克":..., "USDCNY 在岸":... }, "cn10y":... }
}
```

## history.json — 长历史回补（58 KB）

```jsonc
{
  "meta": { "coverage": {"valuationDays":250,"sentimentDays":250,"crossDays":460},
            "unavailable": [ {"key":"etfDailyShares","label":"...","reason":"...","alternative":"..."}, ...共3项 ] },
  "valuation": { "daily": [["20261113", 中位41.80, P25, P75, 127行业], ...] },   // 东财报表口径
  "sentiment": { "rows": [["20261113", 涨停37, 最高板4, 连板7, 晋级率18.92], ...] },  // 同花顺口径
  "cross":     { "daily": [["20250102", 恒指, 纳指, 标普, 中债10Y], ...] }      // 美元/汇率留空
}
```
注意：`valuation.daily` 与 `extras.valuation.industry` 是**两套口径**（127 报表行业 vs 219 板块横截面），样本数不同，绝对值不可直接比，趋势图只用于看方向。

## tmt.json — TMT 成交占比（3 KB）

```jsonc
{
  "asof":"...", "groups": { "电子": {"amount":7425,"share":25.4,"boards":[{code,name},...]},
                            "通信":..., "计算机":..., "传媒":... },   // 11 个东财二级行业板块
  "snapshot": { "ratio": 44.54, "tmtAmount":..., "marketAmount":..., "historyDays": N },
  "history":  [ ["20261113", tmt亿, 两市亿, 占比], ... ]   // 逐日累积，<20 日不出分位
}
```

## flow.json — 市场宽度（555 KB）

```jsonc
{ "asof":"...", "fields":[...], "rows":[ [date, ADL, 麦克莱伦OSC, 麦克莱伦SUM, NH-NL, TRIN, UVDV, OBVA_D, ...], ...600行 ],
  "idx": {"sh000001":[[date,o,h,l,c,v],...2638根], ...} }
```

## workbench.json — 大盘与 ETF（908 KB）

```jsonc
{ "meta":{"asof":"..."},
  "market": [ [date, 成交额元, 成交量, 下跌, 上涨, 平盘, 总数], ...2623行 ],
  "etfs":   { "510300": {"name":"沪深300ETF","cat":"沪深300","core":1,"rows":[[date,close,amount],~700行]}, 共32只 },
  "boards": [...252板块...], "px": {...指数日线...} }
```

## reviews.json — 每日复盘（73 KB）

```jsonc
{ "reviews": [ { "date":"20261117","weekday":"周二","title":"上证... 15栏速览...",
                 "digest":"...","sections":[{...16栏判读...}],
                 "shortTerm":{...触发/证伪...}, "longTerm":{...},
                 "meta":{ "history":{"available":[...],"unavailable":[...]},
                          "caveat":[...], "sources":[...] } }, ... ] }
```
历史日期不回填（避免未来函数），列表按日期排序、最新在最后。

## erp_q90_study.json / erp_q75_study.json — 因果检验（13/20 KB）

三口径（A 全样本含未来函数 / B 扩展窗口 / C 1250 日滚动）+ D 重叠采样 + 稳健性负对照。结构：`{meta, A_fullSampleQ90:{events,forward:{5:{winRate,pOneSided,...}}}, B_..., C_..., D_..., robustness}`。引用时必须带上口径名，不得只报胜率。
