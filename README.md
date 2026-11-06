# 国家队买卖ETF监控（望京博格版）

> 用 Wind 日频数据估算国家队通过宽基 ETF 一级市场申购的规模：按弹性 b 与 R² 把 12 只候选 ETF 分成可反推的一档和只能看份额的二档，逐日归因净买入金额，产出望京博格式 HTML 看板 + 2160 宽长图。

`v1.0.2` `WorkBuddy Skill` `MIT`

## 功能说明

用 Wind 日频数据估算国家队通过宽基 ETF 一级市场申购的规模：按弹性 b 与 R² 把 12 只候选 ETF 分成可反推的一档和只能看份额的二档，逐日归因净买入金额，产出望京博格式 HTML 看板 + 2160 宽长图。内置历史数据与增量入库流水。当用户提到国家队、汇金、证金、国家队抄底、ETF 放量、宽基 ETF 资金流入时使用。

## 前置依赖

必须已装金融数据 MCP（Wind 系列）。核心能力三条：① 场内基金日 K（取成交额）② ETF 日频「基金资金净流入额」③ 沪深300 日 K。缺任意一条就跑不通，验证方法见 references/data-contract.md

## 安装

把整个仓库克隆或下载后，放到 WorkBuddy 的技能目录下：

```bash
git clone https://github.com/Wangjingboge/wj-nation-team-etf-monitor.git ~/.workbuddy/skills/wj-nation-team-etf-monitor
```

WorkBuddy 启动时会自动加载 `~/.workbuddy/skills/` 下的所有技能。

## 使用

触发方式为自然语言 —— 直接用中文描述需求即可，具体的触发词与执行流程见 [`SKILL.md`](SKILL.md)。

## 目录结构

- `SKILL.md`
  - `assets/inbox.sample.json`
  - `assets/intraday.sample.json`
  - `data/audit.log`
  - `data/daily.json`
  - `data/intraday.json`
  - `data/universe.json`
    - `data/backup/daily.20261106-164229.json`
  - `references/data-contract.md`
  - `references/methodology.md`
  - `scripts/model.py`
  - `scripts/render.py`
  - `scripts/store.py`
  - `scripts/update.py`

## 作者

望京博格（[Wangjingboge](https://github.com/Wangjingboge)）

财经自媒体，前公募指数基金产品总监。写作风格与投研框架见 [wj-burger-write](https://github.com/Wangjingboge/wj-burger-write)。

## License

MIT © 2026 望京博格 (WJ Bogle)


> 本技能仅供研究与学习使用，不构成任何投资建议。所有数据须自行核实，据此操作风险自担。
