---
name: edsonstock-data
version: 1.0.0
description: EdsonStock A股结构数据查询。从 GitHub 公开仓库拉取每交易日盘后自动更新的 16 类 A 股结构化 JSON 数据（ERP 股债利差 5200+ 日、两融 2010 年至今全历史、行业 PE 横截面、涨停连板晋级率、TMT 成交占比、市场宽度指标、期指基差与会员净多空、解禁龙虎榜、跨市场环境、每日复盘），或用内置取数脚本直接从新浪/腾讯/东财/中金所/同花顺公开接口自取。每个数据集都带口径标注：真实公开数据 / 量价代理 / 确认不可得。零依赖（Python 3.8+ 标准库），不局限于任何特定 Agent——WorkBuddy、Claude Code、Cursor、自建 Agent 或直接命令行均可使用。当用户需要查询 A 股宏观结构数据、股债利差 ERP、两融余额、行业估值、涨停连板、TMT 拥挤度、市场宽度、衍生品持仓、复盘历史时使用。触发词：A股数据、股债利差、ERP、两融、行业PE、涨停、连板、晋级率、TMT占比、市场宽度、TRIN、期指基差、会员净多空、解禁、龙虎榜、A股复盘。
argument-hint: edsonstock-data --what list
allowed-tools: Bash, Read, Write, WebSearch
homepage: https://github.com/vika520/edsonstock-data
repository: https://github.com/vika520/edsonstock-data
author: vika520
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: 📊
    requires:
      env: []
      optionalEnv:
      - EDSONSTOCK_CACHE
      bins:
      - python3
      files:
      - scripts/*
      - references/*
    tags:
    - a-stock
    - market-data
    - erp
    - margin-trading
    - valuation
    - sentiment
    - derivatives
    - workbench
display_name: EdsonStock A股结构数据
display_name_en: EdsonStock A-Share Structure Data
description_zh: 查询 EdsonStock 公开 A 股结构数据：ERP/两融/行业PE/涨停连板/TMT占比/市场宽度/衍生品/复盘，盘后自动更新，口径全标注
description_en: Query EdsonStock public A-share structure data with per-dataset provenance labels, auto-updated after each trading day.
category: finance
---
# EdsonStock A股结构数据

## 📝 简介

查询 EdsonStock（A股市场结构工作台）沉淀的 15 类结构化市场数据。数据每交易日 16:00 盘后自动更新并推送到 GitHub 公开仓库，本 skill 直接拉取最新版本，无需安装任何依赖（Python 3.8+ 标准库即可）。

**通用性**：本 skill 不绑定任何特定 Agent 平台。WorkBuddy 放入 `~/.workbuddy/skills/`、Claude Code / Cursor 等放入各自的 skills/commands 目录、或任何能执行 Python 的 agent 直接调用 `scripts/query.py` 均可使用——核心是一个零依赖 CLI，输出人类可读文本，LLM agent 天然适配。

**核心特色：每个数据集都标注了口径**——是真实公开数据、量价代理推算、还是确认拿不到。绝不用代理数据冒充真实资金流。

## 🚀 快速使用

```bash
# 列出全部可用数据
python scripts/query.py --what list

# 查 ERP 股债利差（当前 6.4 / 偏便宜 / 79 分位；5200+ 日全历史）
python scripts/query.py --what erp
python scripts/query.py --what erp --history 30      # 最近 30 日序列

# 查两融（2010-03-31 至今全历史，T+1 披露）
python scripts/query.py --what leverage --tail 5

# 查涨停 / 连板 / 晋级率（历史 250 日已回补）
python scripts/query.py --what sentiment
python scripts/query.py --what history --tail 3      # 长历史三类

# 查 TMT 成交占比、行业 PE、解禁、最新复盘
python scripts/query.py --what tmt
python scripts/query.py --what valuation --top 10
python scripts/query.py --what unlock --top 5
python scripts/query.py --what review --tail 2
```

数据源默认从 GitHub raw 拉取（1 小时缓存），可用环境变量 `EDSONSTOCK_CACHE` 指定缓存目录。

## 📊 数据集一览

| what | 文件 | 内容与覆盖 |
|------|------|-----------|
| erp | erp.json | ERP 股债利差：2005-04 起 5200+ 日，1250 日 ±2σ 通道、全样本分位、90 分位区间事件 |
| leverage | extras.json | 两融余额/融资买入额 2010 至今全历史（真实披露，T+1）+ ETF 季度申赎 |
| derivatives | extras.json | 期指基差/年化贴水/PCR/前 20 会员净多空（中金所 XML，真实） |
| sentiment | extras.json | 涨停/跌停/炸板池当日快照（东财，真实） |
| valuation | extras.json | 行业 PE-TTM 当日横截面（219 板块）+ 业绩预告方向代理 |
| events | extras.json | 限售解禁 / 龙虎榜 / 业绩预告（真实披露事件） |
| cross | extras.json | 外盘 6 标的 / USDCNY / 中债 10Y（跨时区，非同步快照） |
| tmt | tmt.json | TMT 四大行业成交占两市比（电子/通信/计算机/传媒，11 板块精确匹配） |
| flow | flow.json | 市场宽度：ADL/麦克莱伦/NH-NL/TRIN/UV-DV/OBV，600 日逐股聚合 |
| sth | sth.json | STH 成本基础：筹码衰减短持成本 + z 通道 + 因果检验（模型推算口径） |
| vix | vix.json | EdsonVIX：自编 A 股恐慌指数（中金所 IO 期权结算价，CBOE 方差互换法自编，非官方复刻）+ Skew + HV30 对照 + 因果检验结论 |
| sth | sth.json | STH 成本基础：筹码衰减短持成本 + z 通道 + 因果检验（模型推算口径） |
| vix | vix.json | EdsonVIX：自编 A 股恐慌指数（中金所 IO 期权结算价，CBOE 方差互换法自编，非官方复刻）+ Skew + HV30 对照 + 因果检验结论 |
| workbench | workbench.json | 两市成交额/涨跌家数（新浪全 A 快照）+ 32 只 ETF 日线 + 252 板块 |
| history | history.json | 长历史回补：行业 PE 中位数 250 日 / 涨停连板 250 日 / 外部环境 460 日 |
| review | reviews.json | 每日复盘文章：标题/快照/15 栏判读/条件化建议 |

## ⚠️ 口径红线（使用前必读）

数据分三档，引用时必须说明档位（详见 references/caveats.md）：

1. **真实公开数据**：两融、中金所行情与会员排名、指数/ETF 日线、解禁、龙虎榜——但均为盘后 T 或 T+1，不是盘中实时；
2. **量价代理**：「净流入」、方向代理、TMT 成交占比、拥挤度综合分——是量价推算，**不是真实资金流**；
3. **确认不可得**：ETF 日度官方份额、期权 IV/skew、Level-2 逐笔、北向日度净买入（2024-08-19 起停披）、炸板率/跌停家数历史——拿不到就明说，不伪造。

另有 4 个信号经严格因果检验**均不显著**，不得说成有效策略：双冰点次日胜率、威科夫 Phase C 兑现率、ERP≥90 分位后上涨（严格因果口径）、ERP≥75 分位反弹（对样本起点敏感）。详见 references/caveats.md。

## 🔧 进阶：直接运行取数脚本

`scripts/` 内含工作台生产环境的全套取数脚本（纯标准库、幂等、失败不写脏数据），可脱离仓库自取数据：

| 脚本 | 数据 | 主要来源 |
|------|------|---------|
| fetch_erp.py | ERP 全序列 | 乐咕（月末官方锚点 + md5 token）+ 东财中债 10Y |
| fetch_extras.py | 6 类补充数据 | 中金所 XML / 东财报表接口 / 腾讯日线 |
| fetch_flow.py | 市场宽度聚合 | 腾讯逐股日线（5578 只全 A） |
| fetch_tmt.py | TMT 成交占比 | 东财二级行业板块（注意：需 curl 子进程，urllib 被代理断连） |
| fetch_workbench.py | 大盘/ETF/板块 | 新浪快照 + 腾讯日线 |
| fetch_wyckoff.py | 威科夫多周期 K 线 | 腾讯日线/周线/分钟线 |
| backfill_history.py | 长历史回补 | 东财估值报表 + 同花顺涨停池 + 腾讯外盘 |

脚本坑点速查（完整版见 references/sources.md）：
- 东财 `push2*` 域名对部分网络环境限流，Python urllib 可能被断连 → 用 curl 子进程；
- 同花顺涨停池 `limit=500` 会返回空，必须 `limit=200`；
- 乐咕 PE 接口需要 `token=md5(当日日期)` + 页面 CSRF 头；
- 东财涨跌停池 `date` 参数失效（请求历史日仍返回当日），历史不可回补；
- TMT 板块历史 K 线随机限流，11 个板块全齐才能加总，否则系统性低估。

## 📁 目录结构

```
edsonstock-data/
├── SKILL.md                  # 本文件
├── scripts/
│   ├── query.py              # 一键查询（推荐入口）
│   ├── fetch_*.py            # 生产级取数脚本（可独立运行）
│   └── backfill_history.py
└── references/
    ├── sources.md            # 每个数据源的 URL / 参数 / 限流坑
    ├── schema.md             # 各 JSON 的字段结构与示例
    └── caveats.md            # 三档口径 + 不显著结论 + 使用红线
```

## 🔗 相关

- 主仓库（工作台 + 数据）：https://github.com/vika520/Edsonstock
- NAS 站点（内网）：由本地 hosts 配置驱动，不随 skill 分发

## ⚖️ 免责

所有数据为公开接口采集的统计信息，不构成投资建议。数据口径与边界以 references/caveats.md 为准。
