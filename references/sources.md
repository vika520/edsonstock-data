# 数据源清单与坑点速查

全部为公开 HTTP 接口，无需任何 API Key。脚本均为 Python 标准库实现。

## 源列表

### 腾讯（行情日线，稳定）
```
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},
```
- 指数/ETF/个股日线、周线（`day`→`week`）、分钟线（`m60`/`m30`/`m15`）
- 个股用 `sh600xxx`/`sz000xxx`；美股带点格式如 `us.DJI`
- 多域名轮换：`web.ifzq.gtimg.cn` / `ifzq.gtimg.cn`

### 新浪（快照与家数）
```
https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1&node=hs_a
https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php        # 行业板块
https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class # 概念板块
https://hq.sinajs.cn/list=fx_susdcny                              # 汇率（需 Referer）
```
- 全 A 快照分页拉取约 5578 只，统计涨跌家数与成交额，与 880005 口径实测几乎一致

### 东财（板块行情 / 报表 / 涨跌停池）
```
https://push2.eastmoney.com/api/qt/ulist.np/get    # 板块当日行情（f6 成交额 f9 PE）
https://push2his.eastmoney.com/api/qt/stock/kline/get  # 板块历史 K 线
https://datacenter-web.eastmoney.com/api/data/v1/get  # 报表类（解禁/龙虎榜/业绩预告/两融/国债）
https://push2ex.eastmoney.com/getTopicZTPool           # 涨停池（ZTPool 同理 DT/炸板）
```
报表接口通用参数：`type=RPT_XXX&sty=ALL&st=字段&sr=-1&token=894050c76af8597a853f5b408b759f5d&p=1&ps=N`
常用报表：`RPT_LIFT_STAGE`（解禁）、`RPT_DAILYBILLBOARD_DETAILSNEW`（龙虎榜）、`RPT_PUBLIC_OP_NEWPREDICT`（业绩预告）、`RPTA_RZRQ_LSHJ`（两融）、`RPTA_WEB_TREASURYYIELD`（国债）、`RPT_VALUEINDUSTRY_DET`（行业估值，含历史）

### 中金所（衍生品，真实）
```
http://www.cffex.com.cn/sj/hqsj/rtj/{yyyymmdd}/{index1|index2}.xml   # 日行情
http://www.cffex.com.cn/sj/ccpm/{yyyymmdd}/                          # 会员持仓排名
```
- 17:20 后当日数据完整；XML 无隐含波动率字段

### 同花顺（涨停池历史）
```
https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?date={yyyymmdd}&limit=200
https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up?date={yyyymmdd}&limit=200
```
- date 参数真实生效，最早可回溯约 1 年

### 乐咕（沪深300 官方 PE-TTM 月末锚点）
```
https://legulegu.com/api/stockdata/index-basic-pe?token={md5(当日ISO日期)}&indexCode=000300.SH
```
- 需 `X-CSRF-Token`（取自页面 meta）+ `X-Requested-With: XMLHttpRequest`

## 坑点速查（踩过的）

| 现象 | 原因 | 解法 |
|------|------|------|
| Python urllib 请求东财被断连 | 本机代理对 `push2*` 整体干扰 | **用 curl 子进程**（fetch_tmt.py 的做法） |
| 同花顺涨停池返回全空 | `limit=500` 超上限 | 必须 `limit=200` |
| 乐咕 403 | token/CSRF 缺失 | token=md5(当日 ISO 日期) + 页面 CSRF 头 |
| 东财涨跌停池请求历史日返回当日数据 | date 参数失效 | 该池只有当日快照，历史不可回补 |
| TMT 板块历史 K 线时有时无 | 上游对板块代码随机限流 | 多轮补齐（6 轮实测仍 4/11）；**11 板块全齐才可加总** |
| push2his 403 | UA 校验 | 带浏览器 User-Agent |
| hq.sinajs.cn 空 | 反盗链 | 带 Referer 头 |

## 幂等与安全约定

- 所有 fetch 脚本按日期幂等：同日重跑只覆盖不重复追加；
- 失败不写半截数据：关键校验不过就抛错，保留上一版文件；
- 只追加当日及之前的已收盘日线，无未来函数；
- 增量优先：逐股缓存 `flow_state.json`（22MB，本地）只取最近 6 根。
