# 不写代码的玩法：Claude 项目 + 定时任务

腕康宝最早不是脚本，而是一段给 Claude 的指令：在 Claude 的项目里建一个**每小时运行**的定时任务，让 Claude 通过 Claude 桌面版的文件夹访问，直接读 Mac 上的导出文件、写 Obsidian 库。

这个模式不用 API Key、不用装 Python，但需要：

- Claude 的付费订阅，能用项目和定时任务
- Mac 一直开着、不睡眠，并且开着 Claude 桌面版（屏幕黑着没关系）
- 在 Claude 里允许访问两个文件夹：Health Auto Export 的 iCloud 文件夹、你的 Obsidian 库

## 怎么用

1. 把 `vault-template/` 复制成你的 Obsidian 库，按 [数据导出设置](数据导出设置.md) 设好 Health Auto Export。
2. 把 `wankangbao/cache_merge.py`、`wankangbao/manual_log.py` 放进项目文件。
3. 新建一个对话，把下面的指令发给 Claude，并让它每小时运行一次。

```text
你负责每小时更新我的健康日报。每次运行：

0. 整理手动记录：重新读取 Obsidian 库里的「手动记录.md」，运行 manual_log.py <文件> <今天日期>，
   内容有变化才写回（我可能正在填，写回前确认文件没被我改过）。
1. 看 Health Auto Export 的 Health/ 和 Workout/ 文件夹里有没有新的或更新过的 JSON。
   有就先用 cache_merge.py 合并进缓存（每天一个文件）。写报告只读缓存，不直接读导出文件：
   后台导出偶尔是只有部分指标的残缺版，直接读会把好数据冲掉。
2. 需要写的日期 = 最近 7 天（含今天）里，没有报告、或者数据比上次写报告时新的日子。
   导出时间早于当天 23:59 → 标「进行中 · 截至 HH:MM」，按到目前为止写；晚于当天结束 → 标「定稿」。
3. 每日笔记写在 每日/YYYY-MM-DD.md，新文件按 模板/每日模板.md 建。
   frontmatter 填自动字段（kJ ÷ 4.184 换算大卡）；一天多次训练要合并（类型去重拼接，时长消耗求和，心率按时长加权）；
   明显忘了结束的记录不计入。只替换「AI报告开始」到「AI报告结束」之间，「备注」和我写的内容原样保留。
4. 报告正文的格式和原则，严格按仓库里 wankangbao/prompts/daily_report.md。
   个人情况读「个人档案.md」，当天的体重、多吃少吃、训练表读「手动记录.md」，基线用之前 14–28 天每日笔记的平均。
5. 每周一第一次运行，按 wankangbao/prompts/weekly_report.md 写上周的周报到 每周/YYYY-Www.md。
6. 只在某天第一次定稿时发一句话通知我；进行中的更新不要发消息。
7. 最新数据超过 24 小时没变，提醒我一次去 Health Auto Export 手动导出。
   Mac 不在线或读写失败：不打扰我，下次再试；连续一天都失败才告诉我。
```

## 踩过的坑

- iCloud 里的文件经常是「只在云端」，用 Claude 的 shell 直接读会卡住；让 Claude 先把文件拉到它自己的工作区再读。
- 写回 Obsidian 后让 Claude 再读一遍核对：偶尔会写进旧版本。
- 用户可能没戴表睡觉。没有睡眠数据时不要断定熬夜；可以在手动记录里加一行 `- 补录睡眠:: 13:00–19:00，6 小时，没戴表`。
