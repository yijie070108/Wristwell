#!/usr/bin/env python3
"""维护 Obsidian 库里的「手动记录.md」：保证今天、明天、后天的格子存在，过去的日子挪到「历史」。

命令行用法: python3 -m wankangbao.manual_log <手动记录.md 路径> <今天 YYYY-MM-DD>
原地改写文件；用户填过的内容只会整块移动，不会改动。
训练日、InBody 日和训练动作可以在 config.json 里改（见 config.example.json）。
"""
import re
import sys
from datetime import date, timedelta

from wankangbao.config import DEFAULT_EXERCISES

WEEKDAY = "一二三四五六日"
TRAIN_DAYS = {2, 4, 6}   # 周三、周五、周日（Monday=0）
INBODY_DAY = 0           # 周一

# 动作, 组数, 次数上限, 加重方式
EXERCISES = [tuple(e) for e in DEFAULT_EXERCISES]

HEADER = """# 手动记录

> 只填你自己知道的：体重、多吃 / 少吃、InBody、力量训练。手表的数据不用填。
> 我每天会自动准备好**今天、明天、后天**的格子；过去的日子挪到下面「历史」。
> 体重：起床、上完厕所后称，只写数字。多吃 / 少吃：写大白话就行，比如「多吃 1 个面包」「第二杯没喝」。
> 力量训练：「今天重量」写格子数或 kg（如 `第5格`、`60kg`），「各组次数」用 `/` 隔开（如 `12/11/10`）。

"""

DAY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})[^\n]*$", re.M)


def parse_blocks(text):
    """返回 {日期: 整块文本}；块从 '## 日期' 开始，到下一个 '## ' 或 '# ' 为止。"""
    blocks = {}
    ms = list(DAY_RE.finditer(text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        chunk = text[m.start():end]
        cut = re.search(r"^# ", chunk[3:], re.M)          # 一级标题以后不属于这一块
        if cut:
            chunk = chunk[:cut.start() + 3]
        sub = re.search(r"^## (?!\d{4}-)", chunk[3:], re.M)  # 「## 历史」之类
        if sub:
            chunk = chunk[:sub.start() + 3]
        lines = chunk.split("\n")
        while lines and lines[-1].strip() in ("", "---", "（还没有）"):  # 去掉空行和分隔线，保留行尾空格
            lines.pop()
        blocks[m.group(1)] = "\n".join(lines) + "\n"
    return blocks


def training_rows(block):
    """从一个训练日块里读出 {动作: (重量, [次数...])}，只要填过的。"""
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in ("动作", "") or set(cells[0]) <= set("-: "):
            continue
        name, _, weight, reps = cells
        nums = [int(x) for x in re.findall(r"\d+", reps)]
        if weight and nums:
            rows[name] = (weight, nums)
    return rows


def last_text(name, sets, top, how, history):
    for d in sorted(history, reverse=True):
        r = training_rows(history[d]).get(name)
        if r:
            weight, reps = r
            s = f"{weight} × {'/'.join(map(str, reps))}"
            if len(reps) >= sets and all(x >= top for x in reps[:sets]):
                s += f" → {how}"
            return s
    return "—"


def new_block(d, history, train_days=TRAIN_DAYS, inbody_day=INBODY_DAY, exercises=EXERCISES):
    lines = [f"## {d.isoformat()} 周{WEEKDAY[d.weekday()]}", "",
             "- 体重:: ", "- 多吃:: ", "- 少吃:: "]
    if d.weekday() == inbody_day:
        lines += ["", "**InBody**", "", "- 体脂率:: ", "- 肌肉量:: ", "- 内脏脂肪:: "]
    if d.weekday() in train_days:
        lines += ["", "**力量训练**", "", "| 动作 | 上次 | 今天重量 | 各组次数 |", "|---|---|---|---|"]
        for name, sets, top, how in exercises:
            lines.append(f"| {name} | {last_text(name, sets, top, how, history)} |  |  |")
    return "\n".join(lines) + "\n"


def maintain(path, today_s, train_days=TRAIN_DAYS, inbody_day=INBODY_DAY, exercises=EXERCISES):
    """整理手动记录；返回 True 表示文件有变化。"""
    exercises = [tuple(e) for e in exercises]
    today = date.fromisoformat(today_s)
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        text = ""
    blocks = parse_blocks(text)
    upcoming = [today + timedelta(days=i) for i in range(3)]
    past = {k: v for k, v in blocks.items() if date.fromisoformat(k) < today}
    for d in upcoming:
        if d.isoformat() not in blocks:
            blocks[d.isoformat()] = new_block(d, past, set(train_days), inbody_day, exercises)
    future = sorted(k for k in blocks if date.fromisoformat(k) >= today)
    for k in future:   # 「上次」那一列每次都按最新记录刷新（只改这一列）
        earlier = {d: b for d, b in blocks.items() if d < k}
        names = {e[0]: e for e in exercises}
        out_lines = []
        for line in blocks[k].split("\n"):
            cells = line.strip().strip("|").split("|")
            if line.startswith("|") and len(cells) == 4 and cells[0].strip() in names:
                cells[1] = " " + last_text(*names[cells[0].strip()], earlier) + " "
                line = "|" + "|".join(cells) + "|"
            out_lines.append(line)
        blocks[k] = "\n".join(out_lines)
    hist = sorted(past, reverse=True)
    out = HEADER + "\n".join(blocks[k] for k in future)
    out += "\n---\n\n# 历史\n\n" + ("\n".join(blocks[k] for k in hist) if hist else "（还没有）\n")
    if out == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return True


def main(path, today_s):
    print("changed" if maintain(path, today_s) else "unchanged")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
