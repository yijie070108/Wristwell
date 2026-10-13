"""拼给 Claude 的材料，调用 Claude API 写报告正文。"""
import json
from datetime import date, timedelta
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
WEEKDAY = "一二三四五六日"
BASELINE_KEYS = ["resting_hr", "hrv", "sleep_hours", "deep_sleep_min", "steps", "active_kcal", "total_kcal"]


def system_prompt():
    return (PROMPT_DIR / "daily_report.md").read_text(encoding="utf-8")


def baseline(notes_by_day, day, window=28):
    """notes_by_day: {YYYY-MM-DD: frontmatter dict}。取 day 之前最近 window 天的平均。"""
    d = date.fromisoformat(day)
    past = [notes_by_day[k] for k in sorted(notes_by_day)
            if d - timedelta(days=window) <= date.fromisoformat(k) < d]
    out = {"天数": len(past)}
    for key in BASELINE_KEYS:
        vals = [fm[key] for fm in past if isinstance(fm.get(key), (int, float))]
        if vals:
            out[key] = round(sum(vals) / len(vals), 1)
    return out


def build_user_message(day, status, as_of, fields, workouts, raw_metrics, manual_block, profile, base):
    wd = WEEKDAY[date.fromisoformat(day).weekday()]
    parts = [
        f"日期：{day}（周{wd}）",
        f"报告状态：{status}",
        f"数据截至：{as_of}",
        "",
        "## 个人档案",
        profile.strip() or "（用户没有填个人档案）",
        "",
        "## 当天自动字段（已换算成大卡、分钟）",
        "```json", json.dumps(fields, ensure_ascii=False, indent=1), "```",
        "",
        "## 当天训练明细（suspicious=true 的那条疑似忘了结束，已不计入合计）",
        "```json", json.dumps(workouts, ensure_ascii=False, indent=1) if workouts else "[]", "```",
        "",
        "## 当天全部指标（原始单位）",
        raw_metrics or "（没有）",
        "",
        "## 当天手动记录",
        manual_block.strip() if manual_block else "（这一天没有手动记录）",
        "",
        f"## 个人基线（之前 {base.get('天数', 0)} 天的平均）",
        "```json", json.dumps(base, ensure_ascii=False), "```",
    ]
    return "\n".join(parts)


def generate(cfg, system, user, client=None):
    """调用 Claude 写报告。返回 Markdown 文本。"""
    import anthropic

    client = client or anthropic.Anthropic()
    with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.effort},
        # 安全分类器误拦时，服务端自动换一个模型重跑
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude 拒绝了这次请求，这一天的报告先跳过")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"Claude 没有返回正文（stop_reason={msg.stop_reason}）")
    return text


def weekly_system_prompt():
    return (PROMPT_DIR / "weekly_report.md").read_text(encoding="utf-8")


def build_weekly_message(week_label, monday, notes_by_day, manual_text, profile):
    """monday: 上周一（date）。给 Claude 最近 4 周的每日数字 + 手动记录全文。"""
    start = monday - timedelta(days=21)
    rows = {k: v for k, v in sorted(notes_by_day.items())
            if start <= date.fromisoformat(k) <= monday + timedelta(days=6)}
    return "\n".join([
        f"周报：{week_label}（{monday:%m-%d} ~ {monday + timedelta(days=6):%m-%d}）",
        "", "## 个人档案", profile.strip() or "（用户没有填个人档案）",
        "", "## 最近 4 周每日数字（来自每日笔记 frontmatter）",
        "```json", json.dumps(rows, ensure_ascii=False, indent=1), "```",
        "", "## 手动记录（体重、InBody、力量训练）", manual_text.strip() or "（没有）",
    ])
