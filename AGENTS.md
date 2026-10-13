# AGENTS.md

给在这个仓库里工作的 AI 编程助手（Claude Code、Cursor、Codex…）看的说明。

## 这是什么

腕康宝：把 Health Auto Export 导出的 Apple Watch 数据（JSON v2）变成 Obsidian 里的每日 / 每周 AI 健康报告。纯 Python，唯一依赖是 `anthropic`。

## 结构

- `wristwell/run.py`：一次运行的主流程（整理手动记录 → 合并导出进缓存 → 重写有新数据的每日笔记 → 周一写周报）
- `wristwell/cache_merge.py`：导出 → 按天缓存。**写报告只读缓存，不直接读导出**（后台导出偶尔是残缺版）
- `wristwell/metrics.py`：缓存 → frontmatter 数字（kJ→大卡、睡眠、多次训练合并、忘了结束的训练）
- `wristwell/manual_log.py`：维护「手动记录.md」（今天 / 明天 / 后天的格子、历史、训练表「上次」列）
- `wristwell/note.py`：只改 frontmatter 自动字段和 `<!-- AI报告开始 -->` … `<!-- AI报告结束 -->` 之间，其他内容原样保留
- `wristwell/report.py` + `prompts/*.md`：拼材料、调用 Claude
- `vault-template/`：干净的 Obsidian 库模板；`examples/`：虚构的导出数据和 `--demo` 用的示例库

## 规则

- 用户写的内容（「备注」、手动记录里填过的值、frontmatter 里不认识的键）**绝不能改**。
- 不要往仓库里放任何真实健康数据；示例数据一律虚构。
- 改了报告格式就改 `prompts/daily_report.md`，同时更新 `examples/demo-vault/每日/2026-01-04.md` 示例和 README 的节选。
- 平均值、基线、趋势、周汇总、趋势图都在代码里算好再给 Claude，不要让模型自己算或转抄数字。
- 证据只许引用 `prompts/daily_report.md` 证据表里的条目；加新条目前先核实出处。

## 容易踩的坑

- **定稿**：一天的报告在「那天 24 点之后又来过任何一次导出」时定稿（`run.is_final`），不是看那天自己的文件。
- **残缺导出**：写报告只读 `workspace/cache/`，不直接读导出文件。
- **iCloud**：没下载的文件是 `.xxx.json.icloud` 占位符；launchd 运行要给 Python 完全磁盘访问权限，否则读目录会 PermissionError。
- **Claude API**：默认 `claude-opus-5-5` + `effort: max` + 自适应思考 + `max_tokens=128000`（思考也算在内）；只接受 `stop_reason=end_turn`，截断和拒绝都不能覆盖旧报告。Opus 5.5 不能关思考。
- **省钱**：同一份数据连续失败 2 次就停（`state.json` 的 `failures`），新数据或 `--date` 才重试；`--loop` 时 `--date` 只在第一次生效。
- `manual_log.py` 和 `cache_merge.py` 只能用标准库（无代码模式会把它们单独拷走用）。
- 提交前跑：`python -m pytest -q` 和 `python start.py --demo --dry-run`。
