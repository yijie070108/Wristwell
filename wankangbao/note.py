"""读写 Obsidian 每日笔记：只改 frontmatter 的自动字段和 AI 报告那一段，其他内容原样保留。"""
import re

from wankangbao.metrics import FIELDS

AI_START = "<!-- AI报告开始"
AI_END = "<!-- AI报告结束 -->"
AI_START_LINE = "<!-- AI报告开始：这一段由 AI 自动生成，会被覆盖，请把自己的内容写在上面的「备注」里 -->"

DEFAULT_TEMPLATE = "---\n" + "".join(f"{f}:\n" for f in FIELDS) + "---\n\n## 备注\n\n\n" + AI_START_LINE + "\n" + AI_END + "\n"

FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)


def read_frontmatter(text):
    """简单的 key: value 解析，够读回我们自己写的数字。"""
    m = FM_RE.match(text)
    out = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line[:1].isspace():
            k, v = line.split(":", 1)
            v = v.strip()
            if v:
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    pass
            out[k.strip()] = v if v != "" else None
    return out


def set_frontmatter(text, fields):
    """更新已有的键、补上缺的键；不认识的键（用户自己加的）原样保留。"""
    m = FM_RE.match(text)
    lines = m.group(1).splitlines() if m else []
    body = text[m.end():] if m else text
    seen = set()
    for i, line in enumerate(lines):
        if line[:1].isspace():
            continue
        k = line.split(":", 1)[0].strip()
        if k in fields:
            v = fields[k]
            lines[i] = f"{k}: {'' if v is None else v}".rstrip()
            seen.add(k)
    for k, v in fields.items():
        if k not in seen:
            lines.append(f"{k}: {'' if v is None else v}".rstrip())
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def set_ai_section(text, markdown):
    markdown = markdown.replace(AI_END, "").replace(AI_START, "")
    e = text.find(AI_END)
    s = text.rfind(AI_START, 0, e) if e != -1 else -1   # 结束标记配离它最近的开始标记，别吞掉用户的字
    block = AI_START_LINE + "\n" + markdown.strip() + "\n" + AI_END
    if s == -1:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    return text[:s] + block + text[e + len(AI_END):]


def user_notes(text):
    """用户在笔记里自己写的部分（frontmatter 之后、AI 报告之前），去掉「## 备注」标题。"""
    m = FM_RE.match(text or "")
    body = (text or "")[m.end():] if m else (text or "")
    s = body.find(AI_START)
    body = body[:s] if s != -1 else body
    body = re.sub(r"^## 备注\s*$", "", body, flags=re.M)
    return body.strip()


def render(existing, template, fields, ai_markdown=None):
    text = existing if existing is not None else (template or DEFAULT_TEMPLATE)
    text = set_frontmatter(text, fields)
    if ai_markdown is not None:
        text = set_ai_section(text, ai_markdown)
    return text
