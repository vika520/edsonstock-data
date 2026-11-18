# edsonstock-data · A股结构数据 Skill

> EdsonStock（A股市场结构工作台）的数据查询技能：15 类盘后自动更新的结构化 A 股数据，一键查询，口径全标注，零依赖。

[![data](https://img.shields.io/badge/data-daily%20updated-green)](https://github.com/vika520/Edsonstock)
[![deps](https://img.shields.io/badge/dependencies-python%203.8%2B%20stdlib-orange)](#)
[![license](https://img.shields.io/badge/license-MIT-blue)](#)

## 这是什么

`edsonstock` 主仓库（16 栏 A 股市场结构工作台）每交易日盘后自动采集并更新一批结构化 JSON 数据。本仓库把这些数据的**查询能力**与**口径知识**打包成 [WorkBuddy Skill](https://www.workbuddy.cn)，安装后可直接对 AI 助手说「查一下最新 ERP」「看两融历史」，无需任何配置。

## 数据一览

| 数据 | 覆盖 | 口径 |
|------|------|------|
| ERP 股债利差 | 2005-04 起 5200+ 日 | 锁定口径：100/PE-TTM − 中债10Y，±2σ 通道 |
| 两融余额 | 2010-03 至今 4000+ 日 | 真实披露（T+1） |
| 期指基差/PCR/会员净多空 | 近 60 日 + 增量 | 中金所官方 XML |
| 涨停/连板/晋级率 | 当日快照 + 250 日历史 | 东财池（当日）+ 同花顺池（历史） |
| 行业 PE 横截面 | 219 板块当日 | 东财 f9（真实） |
| TMT 成交占比 | 当日锁定 + 逐日累积 | 量价代理（交易集中度） |
| 市场宽度 ADL/TRIN/NH-NL 等 | 600 日逐股聚合 | 公式化计算 |
| 长历史：行业PE中位数/外盘 | 250 / 460 日按日回补 | 东财报表 + 腾讯日线 |
| 每日复盘 | 全部交易日 | 固定规则生成 |

每个数据集都标注三档口径之一：**真实公开数据 / 量价代理 / 确认不可得**——并附 4 个经严格因果检验**不显著**的信号结论（双冰点、威科夫 Phase C、ERP 90/75 分位反弹），杜绝用未来函数美化胜率。

## 安装（WorkBuddy）

把本仓库放入 `~/.workbuddy/skills/edsonstock-data/`，即可在工作台对话中使用；触发词：`A股数据`、`ERP`、`两融`、`行业PE`、`涨停连板`、`TMT占比`、`市场宽度`、`A股复盘` 等。

## 直接使用（不装 Skill 也行）

核心是一个零依赖脚本：

```bash
python scripts/query.py --what list          # 列出全部数据
python scripts/query.py --what erp           # ERP 当前 + 通道 + 分位
python scripts/query.py --what leverage --tail 5
python scripts/query.py --what sentiment     # 涨跌停/炸板/晋级率
python scripts/query.py --what valuation --top 10
python scripts/query.py --what review --tail 1
```

数据从 `vika520/Edsonstock` 的 GitHub raw 拉取（1 小时本地缓存），也可以用 `scripts/fetch_*.py` 直接从新浪/腾讯/东财/中金所/同花顺公开接口自取——这些是主仓库生产环境同款脚本，踩坑经验都沉淀在 `references/sources.md`。

## 目录

```
├── SKILL.md              # Skill 入口（触发词 / 用法 / 口径红线）
├── scripts/
│   ├── query.py          # 一键查询（推荐入口）
│   └── fetch_*.py        # 生产级取数脚本（可独立运行）
└── references/
    ├── sources.md        # 数据源 URL / 参数 / 限流坑速查
    ├── schema.md         # 各 JSON 字段结构与示例
    └── caveats.md        # 三档口径 + 不显著结论 + 使用红线
```

## 数据更新机制

数据由主仓库的盘后流水线维护：交易日 16:00 抓取 → 幂等合并 → 构建部署 → 提交推送。本仓库不含数据，`query.py` 实时拉最新；因此本仓库无需每日更新，只有口径/脚本变化时才更新。

## 相关

- 主仓库：https://github.com/vika520/Edsonstock
- 数据直接下载：`https://raw.githubusercontent.com/vika520/Edsonstock/main/wolf-cycle/data/<name>.json`

## 免责

所有数据为公开接口采集的统计信息，不构成投资建议。口径与边界以 `references/caveats.md` 为准。
