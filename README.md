<div align="center">

<br />

# ⌚ 腕康宝 (WanKangBao) v0.1.0

### Apple Watch 数据全自动变成 Obsidian 里的「医生级」每日健康报告

[![GitHub Repository](https://img.shields.io/badge/GitHub-yijie070108%2Fwankangbao-blue?logo=github&style=for-the-badge)](https://github.com/yijie070108/wankangbao)
[![Version](https://img.shields.io/badge/Version-v0.1.0-8b5cf6?style=for-the-badge)](https://github.com/yijie070108/wankangbao/releases)
[![License](https://img.shields.io/badge/License-MIT-emerald?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white&style=for-the-badge)](https://www.python.org)
[![Obsidian](https://img.shields.io/badge/Obsidian-笔记库-7C3AED?logo=obsidian&logoColor=white&style=for-the-badge)](https://obsidian.md)
[![Claude](https://img.shields.io/badge/Claude-Opus_5.5-D97757?logo=anthropic&logoColor=white&style=for-the-badge)](https://www.anthropic.com/claude)

[⚡ AI Agent 一键部署](#-ai-agent-一键部署-recommended) • [📖 快速开始](#-快速开始) • [✨ 核心特性](#-核心特性) • [🏗️ 系统架构](#️-系统架构) • [📝 报告长什么样](#-报告长什么样) • [⚙️ 配置说明](#️-配置说明)

</div>

---

> [!IMPORTANT]
> **腕康宝 (WanKangBao)** 是一套给减脂 / 增肌 / 想看懂自己身体数据的人用的个人健康系统。Apple Watch 记录 → 手机每小时自动导出到 iCloud → Mac 上的腕康宝每个整点读取，**合并数据、对照你自己的基线、结合你手填的体重 / 饮食 / 力量训练**，用 Claude 写一份「发现 → 解读 → 怎么做」的每日报告，直接写进你的 Obsidian 库；每周一再写一份带趋势图的周报。

> [!CAUTION]
> 报告由 AI 根据手表数据生成，仅供参考，**不能代替医生诊断**。

---

## ⚡ AI Agent 一键部署 (Recommended)

> **给用户的提示**：如果你在用 **Claude Code、Cursor、Codex、Windsurf** 或任意 AI 编程 Agent，复制下面这段发给它，它会帮你在 Mac 上装好并跑通示例。

```text
请按照以下步骤为我安装并试运行【腕康宝 WanKangBao】Apple Watch 健康日报系统：

1. 克隆仓库：
   - git clone https://github.com/yijie070108/wankangbao.git && cd wankangbao

2. 安装依赖：
   - 若不存在 .venv，运行 python3 -m venv .venv
   - . .venv/bin/activate && pip install -r requirements-dev.txt

3. 用虚构数据自检：
   - 运行 python -m pytest -q，确认全部通过
   - 运行 python start.py --demo --dry-run，确认 workspace/demo-vault/每日/ 下生成了 2026-01-05.md

4. 配置我的真实环境：
   - cp config.example.json config.json
   - 问我 Obsidian 库的路径，填进 vault_dir；确认 export_dir 下有 Health/ 和 Workout/ 两个文件夹（Health Auto Export 的导出）
   - 把 vault-template/ 里我还没有的文件（个人档案.md、手动记录.md、模板/每日模板.md）复制进我的库，提醒我改「个人档案.md」
   - cp .env.example .env，让我自己填 ANTHROPIC_API_KEY（不要替我编造）

5. 跑一次真的：
   - python start.py --dry-run 看材料是否正常，再运行 python start.py
   - 按 scripts/com.wankangbao.hourly.plist 里的说明帮我装好每小时自动运行，并告诉我日志在哪
```

---

## ✨ 核心特性

<div justify="center">

| 特性分类 | 核心亮点 |
| :--- | :--- |
| **⏱️ 每小时自动更新** | 今天的报告标「进行中 · 截至 HH:MM」，每小时有新数据就重写；过完一天自动「定稿」，Mac 弹通知 |
| **🩺 医生式问题清单** | 每条问题都写成「发现（数字）→ 解读（有证据才点名研究 / 指南）→ 怎么做（今天就能做）」，只写 2–4 条真正重要的 |
| **📏 和你自己比** | 状态总览（睡眠 / 恢复 / 活动 / 能量 / 体成分 🟢🟡🔴⚪）全部对照你最近 14–28 天的个人基线，不和人群平均硬比 |
| **🛡️ 残缺导出防护** | iOS 后台导出偶尔只带一部分指标；腕康宝先合并进按天缓存（累加型取大、其他取新），好数据不会被冲掉 |
| **✍️ 手动记录页** | 自动准备今天 / 明天 / 后天的格子：体重、多吃 / 少吃（写大白话，AI 估热量）、InBody、力量训练表（「上次」列自动填，做满了提示「→ 加一格」） |
| **🏋️ 训练识别** | 一天多次训练自动合并；忘了点结束的记录（比如 58 分钟只走了 1 km）不计入，并在报告里说明 |
| **📈 周报 + 趋势图** | 每周一写上周周报：体重变化率、恢复趋势、训练完成度、按你的调整规则给结论，附 mermaid 趋势图（Obsidian 原生渲染） |
| **🔒 只动该动的地方** | 只改 frontmatter 数字和 AI 报告那一段；你写的「备注」、填的值、自己加的字段原样保留 |

</div>

---

## 🏗️ 系统架构

```mermaid
flowchart TD
    W["Apple Watch"] -->|实时| H["iPhone「健康」App"]
    H -->|Health Auto Export 每小时后台导出 JSON v2| I["iCloud 云盘 / HealthExport / Health + Workout"]
    I -->|自动同步| M["Mac"]
    M --> R["腕康宝 start.py（每个整点 launchd 唤醒）"]
    R --> C["按天缓存 workspace/cache/（残缺导出防护）"]
    V1["Obsidian：个人档案.md + 手动记录.md"] --> R
    C --> P["算指标 + 个人基线"]
    P -->|材料| A["Claude API（Opus 5.5，自适应思考）"]
    A --> D["Obsidian：每日/YYYY-MM-DD.md"]
    A --> K["Obsidian：每周/YYYY-Www.md"]
```

每次运行做的事：

1. 整理 `手动记录.md`：补齐今天 / 明天 / 后天的格子，过去的日子挪到「历史」，刷新训练表的「上次」列
2. 读 mtime 变过的导出文件，合并进按天缓存（按天、按周导出都能处理）
3. 最近 7 天里有新数据的日子：算 frontmatter 数字 → 拼材料 → Claude 写报告 → 写回每日笔记
4. 周一（或之后第一次运行）写上周周报
5. 没有新数据就什么都不做，**不花 API 费用**

---

## 🚀 快速开始

### 你需要

- iPhone + Apple Watch
- **Health Auto Export**（App Store 付费 App），按 [docs/数据导出设置.md](docs/数据导出设置.md) 建两个每小时的 iCloud 自动化
- 一台常开的 Mac（报告由 Mac 上的脚本写；屏幕黑着没关系）
- [Obsidian](https://obsidian.md)，库建议放在 iCloud，手机上也能看
- Claude API Key（[console.anthropic.com](https://console.anthropic.com/)）
- Python 3.10+

> 💡 不想写代码、已经在用 Claude 的付费订阅？也可以让 Claude 项目的定时任务直接读写你的文件，见 [docs/Claude项目模式.md](docs/Claude项目模式.md)。

### 1. 安装

```bash
git clone https://github.com/yijie070108/wankangbao.git
cd wankangbao
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. 用虚构数据试一下

```bash
python start.py --demo --dry-run   # 不调用 Claude，只填数字、打印发给 Claude 的材料
python start.py --demo             # 填了 ANTHROPIC_API_KEY 后：真的写一份报告
```

结果在 `workspace/demo-vault/`，可以直接用 Obsidian 打开看。

### 3. 接上你自己的数据

```bash
cp config.example.json config.json   # 改 vault_dir、export_dir、timezone、训练日
cp .env.example .env                 # 填 ANTHROPIC_API_KEY
```

把 `vault-template/` 里的 `个人档案.md`、`手动记录.md`、`模板/每日模板.md` 复制进你的 Obsidian 库，**先把「个人档案」改成你自己的情况**（目标、食谱热量、训练日、调整规则），AI 每次写报告都会读它。

```bash
python start.py --dry-run   # 先看看材料对不对
python start.py             # 跑一次真的
```

### 4. 每小时自动运行

**方式一（推荐）**：macOS launchd，按 [scripts/com.wankangbao.hourly.plist](scripts/com.wankangbao.hourly.plist) 里的三步装好。

**方式二**：开一个终端一直跑：

```bash
python start.py --loop
```

### 常用命令

| 命令 | 作用 |
| :--- | :--- |
| `python start.py` | 跑一次（没有新数据就直接结束） |
| `python start.py --loop` | 每个整点过 1 分钟跑一次 |
| `python start.py --date 2026-01-05` | 强制重写某一天 |
| `python start.py --dry-run` | 不调用 Claude，打印材料 |
| `python start.py --demo` | 用 `examples/` 的虚构数据跑一遍 |

---

## 📝 报告长什么样

完整示例见 [vault-template/每日/2026-01-04.md](vault-template/每日/2026-01-04.md)（数据虚构）。节选：

```markdown
## 每日健康报告 · 2026-01-04（周日）· 定稿

**数据截至 01-05 08:12** · 计划第 4 天

> [!summary] 结论
> **训练完成得很好，四个动作有进步；恢复和睡眠都正常。** 唯一扣分的是下午那杯全糖奶茶，把热量缺口吃掉了一半。

| 维度 | 状态 | 依据 |
|---|---|---|
| 睡眠 | 🟢 | 7.3 小时，深睡 66 分钟、REM 96 分钟，和基线（7.1 小时）持平 |
| 恢复 | 🟢 | HRV 52（基线 50）；静息心率 58（基线 59） |
| 能量 | 🟡 | 估算缺口约 150–300 大卡，计划是 500 左右 |

**1. 一杯奶茶吃掉了一半缺口**
- **发现**：手表全天消耗约 2,316 大卡；食谱基数 1,900 + 中杯全糖珍珠奶茶约 350–450 大卡
- **解读**：手表的静息消耗通常比公式高约 15%，真实缺口可能接近 0……
- **怎么做**：想喝就点无糖或三分糖、去掉珍珠，热量能少一半以上
```

每份报告固定包含：结论 → 状态总览 → 问题清单 → 其他指标 → 今天可以做得更好 → 明天的处方 → 什么时候该看医生。格式和原则都写在 [wankangbao/prompts/daily_report.md](wankangbao/prompts/daily_report.md)，想改风格直接改这个文件。

---

## 📂 目录结构

```
wankangbao/
├── start.py                    # 一键运行入口
├── config.example.json         # 配置示例 → 复制成 config.json
├── wankangbao/
│   ├── run.py                  # 主流程
│   ├── cache_merge.py          # 导出 → 按天缓存（残缺导出防护）
│   ├── metrics.py              # 缓存 → frontmatter 数字、训练合并
│   ├── manual_log.py           # 维护「手动记录.md」
│   ├── note.py                 # 安全地改写每日笔记
│   ├── report.py               # 拼材料、调用 Claude
│   └── prompts/                # 日报 / 周报的指令（报告格式在这里改）
├── vault-template/             # Obsidian 库模板 + 示例报告
├── examples/                   # 虚构的 Health Auto Export 导出
├── docs/                       # 导出设置、Claude 项目模式
├── scripts/                    # macOS launchd 定时任务
└── tests/
```

---

## ⚙️ 配置说明

`config.json`（不存在时全部用默认值）：

| 字段 | 默认值 | 作用 |
| :--- | :---: | :--- |
| `export_dir` | `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents` | Health Auto Export 的导出目录，里面要有 `Health/`、`Workout/` |
| `vault_dir` | `vault-template/` | 你的 Obsidian 库根目录 |
| `data_dir` | `workspace/` | 缓存和运行状态 |
| `timezone` | 系统时区 | 例如 `Asia/Shanghai`，决定「今天」和「定稿」的分界 |
| `model` | `claude-opus-5-5` | 写报告用的 Claude 模型 |
| `effort` | `medium` | 思考力度：`low` / `medium` / `high` / `xhigh` / `max`，越高越细、越贵 |
| `lookback_days` | `7` | 最近几天的数据有更新时会重写报告 |
| `train_days` | `[2, 4, 6]` | 力量训练日（周一 = 0），决定手动记录里哪天出训练表 |
| `inbody_day` | `0` | 哪天出 InBody 格子 |
| `exercises` | 6 个器械动作 | `[动作, 组数, 次数上限, 加重方式]`，「上次」列每组都做到上限时提示加重 |

环境变量（`.env`）：

| 变量 | 作用 |
| :--- | :--- |
| `ANTHROPIC_API_KEY` | Claude API Key；也可以用 `ant auth login` 登录代替 |
| `WKB_CONFIG` | 配置文件路径（默认仓库根目录的 `config.json`） |

> 💰 **费用**：只有数据真的变了才调用 Claude。手表一天导出十几次，今天的报告就会重写十几次；嫌贵可以把 `effort` 调成 `low`，或者把 launchd 改成每 3 小时一次。

---

## 🔒 隐私

1. **数据只在你自己的设备之间流动**：iPhone → 你的 iCloud → 你的 Mac → 你的 Obsidian 库。
2. **唯一的外发**是写报告时发给 Claude API 的当天数字和手动记录（不含原始心率曲线、GPS 路线）。可以用 `--dry-run` 先看清楚发了什么。
3. 缓存和状态都在本地 `workspace/`，已在 `.gitignore` 里；仓库里的示例数据全部虚构。

---

## 🗺️ 路线图

- [ ] 周报里的力量训练趋势表
- [ ] 支持 Health Auto Export 的 REST API 推送（不经过 iCloud）
- [ ] 英文报告

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。欢迎提交 Issue 与 Pull Request！

---

<div align="center">

**🌟 如果腕康宝帮你看懂了自己的身体数据，欢迎点个 Star！**

[https://github.com/yijie070108/wankangbao](https://github.com/yijie070108/wankangbao)

</div>
