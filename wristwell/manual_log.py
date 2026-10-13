#!/usr/bin/env python3
"""维护 Obsidian 库里的「手动记录.md」：保证今天、明天、后天的格子存在，过去的日子挪到「历史」。

命令行用法（只用标准库，可以单独拷走用）: python3 manual_log.py <手动记录.md 路径> <今天 YYYY-MM-DD>
原地改写文件；用户写的内容只会整块移动，不会改动或删除：
- 标题下面你自己加的话会保留
- 日期块里你自己加的小标题（如「## 本周感想」）跟着这一天走
- 同一个日期写了两次，两块会合并
- 写错的日期（如 2026-02-30）不当成日期，原样留在上一块里
改写前会检查：只要有一行非空内容会消失，就不写，报错让人来看。
训练日、InBody 日和训练动作可以在 config.json 里改（见 config.example.json）。
"""
import os
import re
import sys
from datetime import date, timedelta

WEEKDAY = "一二三四五六日"
TRAIN_DAYS = {2, 4, 6}   # 周三、周五、周日（Monday=0）
INBODY_DAY = 0           # 周一

# 动作, 组数, 次数上限, 加重方式（每组都做到上限时，「上次」列提示加重）
DEFAULT_EXERCISES = [
    ["倒蹬", 3, 12, "加 5–10kg"],
    ["坐姿腿弯举", 3, 12, "加一格"],
    ["器械推胸", 3, 12, "加一格"],
    ["坐姿划船", 4, 12, "加一格"],
    ["单手绳索侧平举（每边）", 3, 20, "加一格"],   # 先做到 20 下再加
    ["卷腹器械（可选）", 2, 15, "加一格"],
]
EXERCISES = [tuple(e) for e in DEFAULT_EXERCISES]

HEADER = """# 手动记录

> 只填你自己知道的：体重、多吃 / 少吃、InBody、力量训练。手表的数据不用填。
> 我每天会自动准备好**今天、明天、后天**的格子；过去的日子挪到下面「历史」。
> 体重：起床、上完厕所后称，只写数字。多吃 / 少吃：写大白话就行，比如「多吃 1 个面包」「第二杯没喝」。
> 力量训练：「今天重量」写格子数或 kg（如 `第5格`、`60kg`），「各组次数」用 `/` 隔开（如 `12/11/10`）。
> 没戴表睡觉的话，可以加一行 `- 补录睡眠:: 23:30–07:00，7.5 小时，没戴表`。

"""

HISTORY_RE = re.compile(r"\n*-{3,}\s*\n+# 历史\s*\n(?:\s*（还没有）\s*\n)?", re.M)
DAY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})[^\n]*$", re.M)


def _valid(d):
    try:
        date.fromisoformat(d)
        return True
    except ValueError:
        return False


def split(text):
    """返回 (标题区, {日期: 整块文本})。块从「## 日期」开始，到下一个有效日期标题为止。"""
    ms = [m for m in DAY_RE.finditer(text) if _valid(m.group(1))]
    pre = text[:ms[0].start()] if ms else text
    blocks = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        chunk = HISTORY_RE.sub("\n", text[m.start():end])
        chunk = re.sub(r"^# 历史\s*$", "", chunk, flags=re.M)
        lines = chunk.split("\n")
        while lines and lines[-1].strip() in ("", "---", "（还没有）"):
            lines.pop()
        chunk = "\n".join(lines) + "\n"
        d = m.group(1)
        blocks[d] = blocks[d].rstrip("\n") + "\n\n" + chunk.split("\n", 1)[1] if d in blocks else chunk
    pre = HISTORY_RE.sub("\n", pre)
    return pre, blocks


def parse_blocks(text):
    return split(text)[1]


def field(block, label):
    """读「- 体重:: 79.6」这种行的值；没填返回空字符串。"""
    m = re.search(rf"^- {re.escape(label)}::[ \t]*(.*)$", block, re.M)
    return m.group(1).strip() if m else ""


def number(block, label):
    m = re.match(r"\d+(?:\.\d+)?", field(block, label))
    return float(m.group(0)) if m else None


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


def _content_lines(text):
    """用来做「不丢内容」检查：非空行；训练表行去掉会自动刷新的「上次」列。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s in ("---", "# 历史", "（还没有）"):
            continue
        cells = s.strip("|").split("|")
        if s.startswith("|") and len(cells) == 4:
            s = "|".join([cells[0], cells[2], cells[3]]).replace(" ", "")
        out.append(s)
    return out


def render(text, today_s, train_days=TRAIN_DAYS, inbody_day=INBODY_DAY, exercises=EXERCISES):
    exercises = [tuple(e) for e in exercises]
    train_days = set(train_days)
    today = date.fromisoformat(today_s)
    pre, blocks = split(text)
    past = {k: v for k, v in blocks.items() if date.fromisoformat(k) < today}
    for i in range(3):
        d = today + timedelta(days=i)
        if d.isoformat() not in blocks:
            blocks[d.isoformat()] = new_block(d, past, train_days, inbody_day, exercises)
    future = sorted(k for k in blocks if date.fromisoformat(k) >= today)
    names = {e[0]: e for e in exercises}
    for k in future:   # 「上次」那一列每次都按最新记录刷新（只改这一列）
        earlier = {d: b for d, b in blocks.items() if d < k}
        out_lines = []
        for line in blocks[k].split("\n"):
            cells = line.strip().strip("|").split("|")
            if line.startswith("|") and len(cells) == 4 and cells[0].strip() in names:
                cells[1] = " " + last_text(*names[cells[0].strip()], earlier) + " "
                line = "|" + "|".join(cells) + "|"
            out_lines.append(line)
        blocks[k] = "\n".join(out_lines)
    hist = sorted(past, reverse=True)
    pre = pre.rstrip("\n") + "\n\n" if pre.strip() else HEADER
    out = pre + "\n".join(blocks[k] for k in future)
    out += "\n---\n\n# 历史\n\n" + ("\n".join(blocks[k] for k in hist) if hist else "（还没有）\n")
    return out


def maintain(path, today_s, train_days=TRAIN_DAYS, inbody_day=INBODY_DAY, exercises=EXERCISES):
    """整理手动记录；返回 True 表示文件有变化。会丢内容时抛 ValueError，不写文件。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    out = render(text, today_s, train_days, inbody_day, exercises)
    if out == text:
        return False
    missing = [s for s in _content_lines(text) if s not in set(_content_lines(out))]
    if missing:
        raise ValueError(f"整理手动记录会丢掉这些内容，已停止，没有改文件：{missing[:3]}")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, path)
    return True


def main(path, today_s):
    print("changed" if maintain(path, today_s) else "unchanged")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
