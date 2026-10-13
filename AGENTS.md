# AGENTS.md

给在这个仓库里工作的 AI 编程助手（Claude Code、Cursor、Codex…）看的说明。

## 这是什么

腕康宝：把 Health Auto Export 导出的 Apple Watch 数据（JSON v2）变成 Obsidian 里的每日 / 每周 AI 健康报告。纯 Python，唯一依赖是 `anthropic`。

## 结构

- `wankangbao/run.py`：一次运行的主流程（整理手动记录 → 合并导出进缓存 → 重写有新数据的每日笔记 → 周一写周报）
- `wankangbao/cache_merge.py`：导出 → 按天缓存。**写报告只读缓存，不直接读导出**（后台导出偶尔是残缺版）
- `wankangbao/metrics.py`：缓存 → frontmatter 数字（kJ→大卡、睡眠、多次训练合并、忘了结束的训练）
- `wankangbao/manual_log.py`：维护「手动记录.md」（今天 / 明天 / 后天的格子、历史、训练表「上次」列）
- `wankangbao/note.py`：只改 frontmatter 自动字段和 `<!-- AI报告开始 -->` … `<!-- AI报告结束 -->` 之间，其他内容原样保留
- `wankangbao/report.py` + `prompts/*.md`：拼材料、调用 Claude
- `vault-template/`：Obsidian 库模板；`examples/`：虚构的导出数据

## 规则

- 用户写的内容（「备注」、手动记录里填过的值、frontmatter 里不认识的键）**绝不能改**。
- 不要往仓库里放任何真实健康数据；示例数据一律虚构。
- 改了报告格式就改 `prompts/daily_report.md`，同时更新 `vault-template/每日/2026-01-04.md` 示例。
- 提交前跑：`python -m pytest -q` 和 `python start.py --demo --dry-run`。
