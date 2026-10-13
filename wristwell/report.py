"""拼给 Claude 的材料（基线、趋势、训练历史都在代码里算好），调用 Claude API 写报告正文。"""
import json
import statistics
from datetime import date, timedelta
from pathlib import Path

from wristwell import manual_log

PROMPT_DIR = Path(__file__).parent / "prompts"
WEEKDAY = "一二三四五六日"
MAX_TOKENS = 128000          # Opus 5.5 的输出上限；思考也算在里面，effort=max 时要留足。用流式请求，不怕超时
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

# 基线 / 多天表里的指标：(键, 中文名, 小数位)
METRICS = [
    ("sleep_hours", "睡眠（小时）", 1), ("deep_sleep_min", "深睡（分钟）", 0), ("rem_min", "REM（分钟）", 0),
    ("resting_hr", "静息心率", 0), ("hrv", "HRV（SDNN, ms）", 0), ("steps", "步数", 0),
    ("active_kcal", "活动消耗（大卡）", 0), ("total_kcal", "总消耗（大卡）", 0),
    ("resp_rate", "呼吸频率", 1), ("spo2", "血氧 %", 1), ("wrist_temp", "睡眠手腕温度 ℃", 2),
    ("vo2max", "最大摄氧量", 1), ("daylight_min", "日照（分钟）", 0), ("weight", "体重 kg", 1),
]


class ReportError(RuntimeError):
    """这一天先不写，下次再试。"""


class Refused(ReportError):
    def __init__(self, msg, retry_later=False):
        super().__init__(msg)
        self.retry_later = retry_later


class NoCredentials(RuntimeError):
    pass


def _read(name):
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def system_prompt():
    return _read("daily_report.md")


def weekly_system_prompt():
    return _read("weekly_report.md")


# ---------- 统计 ----------

def stats(vals):
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        return None
    out = {"n": len(vals), "median": statistics.median(vals), "mean": statistics.fmean(vals)}
    if len(vals) >= 4:
        q = statistics.quantiles(vals, n=4, method="inclusive")
        out["p25"], out["p75"] = q[0], q[2]
    if len(vals) >= 5:
        out["sd"] = statistics.stdev(vals)
    return out


def _f(v, nd):
    return "—" if v is None else (f"{v:.{nd}f}" if nd else f"{round(v):,}")


def baseline(history, day, window=28):
    """history: {YYYY-MM-DD: {指标: 值}}。取 day 之前最近 window 天，每个指标算 n / 中位数 / P25–P75 / 均值 / 标准差。"""
    d = date.fromisoformat(day)
    past = [history[k] for k in history if d - timedelta(days=window) <= date.fromisoformat(k) < d]
    return {key: s for key, _, _ in METRICS if (s := stats([h.get(key) for h in past]))}


def baseline_table(base):
    if not base:
        return "（还没有之前的数据，今天没有个人基线）"
    rows = ["| 指标 | 天数 | 中位数 | P25–P75 | 均值 | 标准差 |", "|---|---|---|---|---|---|"]
    for key, label, nd in METRICS:
        s = base.get(key)
        if s:
            iqr = f"{_f(s['p25'], nd)}–{_f(s['p75'], nd)}" if "p25" in s else "—"
            rows.append(f"| {label} | {s['n']} | {_f(s['median'], nd)} | {iqr} | {_f(s['mean'], nd)} | {_f(s.get('sd'), nd)} |")
    return "\n".join(rows)


def recent_table(history, day, n=7):
    """最近 n 天（含今天）每天一行，看连续几天的走向。"""
    d = date.fromisoformat(day)
    cols = [("sleep_hours", "睡眠h", 1), ("sleep_start", "入睡", None), ("sleep_end", "醒来", None),
            ("deep_sleep_min", "深睡", 0), ("resting_hr", "静息心率", 0), ("hrv", "HRV", 0),
            ("steps", "步数", 0), ("active_kcal", "活动大卡", 0), ("workout_type", "训练", None), ("weight", "体重", 1)]
    rows = ["| 日期 | " + " | ".join(c[1] for c in cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i in range(n - 1, -1, -1):
        k = (d - timedelta(days=i)).isoformat()
        h = history.get(k, {})
        cells = [str(h.get(key) or "—") if nd is None else _f(h.get(key), nd) for key, _, nd in cols]
        rows.append(f"| {k[5:]} 周{WEEKDAY[(d - timedelta(days=i)).weekday()]} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def weight_trend(weights, day):
    """weights: {日期: kg}。近 7 天均值 vs 前 7 天均值，算周变化。"""
    d = date.fromisoformat(day)
    win = lambda a, b: [w for k, w in weights.items() if d - timedelta(days=a) < date.fromisoformat(k) <= d - timedelta(days=b)]
    last, prev = win(7, 0), win(14, 7)
    recent = sorted((k, w) for k, w in weights.items() if d - timedelta(days=14) < date.fromisoformat(k) <= d)
    if not recent:
        return "（最近 14 天没有称体重）"
    lines = ["最近 14 天称重：" + "、".join(f"{k[5:]} {w:.1f}" for k, w in recent)]
    if last:
        lines.append(f"近 7 天均值 {statistics.fmean(last):.2f} kg（称了 {len(last)} 次）")
    if prev:
        lines.append(f"前 7 天均值 {statistics.fmean(prev):.2f} kg（称了 {len(prev)} 次）")
    if last and prev:
        ch = statistics.fmean(last) - statistics.fmean(prev)
        lines.append(f"周变化 {ch:+.2f} kg（{ch / statistics.fmean(prev) * 100:+.2f}%）"
                     + ("；称重次数少于 4 次，周变化不可靠" if min(len(last), len(prev)) < 4 else ""))
    return "\n".join(lines)


def inbody_history(blocks, day, n=4):
    rows = []
    for k in sorted(blocks, reverse=True):
        if k > day:
            continue
        vals = {lab: manual_log.number(blocks[k], lab) for lab in ("体脂率", "肌肉量", "内脏脂肪")}
        if any(v is not None for v in vals.values()):
            rows.append(f"- {k}：" + "，".join(f"{lab} {v:g}" for lab, v in vals.items() if v is not None))
        if len(rows) >= n:
            break
    return "\n".join(rows) or "（还没有 InBody 记录）"


def training_history(blocks, day, sessions=4):
    """day 之前每个动作最近几次的记录（从旧到新），看是不是卡住了。"""
    hist = {}
    for k in sorted(blocks, reverse=True):
        if k >= day:
            continue
        for name, (weight, reps) in manual_log.training_rows(blocks[k]).items():
            if len(hist.setdefault(name, [])) < sessions:
                hist[name].append(f"{k[5:]} {weight} × {'/'.join(map(str, reps))}")
    return "\n".join(f"- {name}：" + " → ".join(reversed(v)) for name, v in hist.items()) or "（之前没有力量训练记录）"


# ---------- 材料 ----------

def build_user_message(*, day, status, as_of, fields, workouts, raw_metrics, manual_block, prev_manual_block,
                       user_notes, profile, plan, history, blocks):
    d = date.fromisoformat(day)
    yesterday = (d - timedelta(days=1)).isoformat()
    wt = str(fields.get("workout_type") or "")
    training_day = bool(manual_log.training_rows(manual_block)) or "力量" in wt or "strength" in wt.lower()
    weights = {k: v["weight"] for k, v in history.items() if v.get("weight") is not None}
    parts = [
        f"日期：{day}（周{WEEKDAY[d.weekday()]}）",
        f"报告状态：{status}",
        f"数据截至：{as_of}",
        "时间对应：sleep_* 是前一晚入睡到今天早上醒来；今天的 HRV、静息心率主要反映前一晚的恢复。",
        "", "## 个人档案", profile.strip() or "（用户没有填个人档案）",
    ]
    if training_day and plan.strip():
        parts += ["", "## 训练计划（含进阶规则）", plan.strip()]
    parts += [
        "", "## 今天的自动字段（已换算成大卡、分钟）",
        "```json", json.dumps(fields, ensure_ascii=False, indent=1), "```",
        "", "## 今天的训练明细（suspicious=true 的那条疑似忘了结束，已不计入合计）",
        "```json", json.dumps(workouts, ensure_ascii=False, indent=1), "```",
        "", "## 今天的手表指标（原始单位）", raw_metrics or "（没有）",
        "", "## 今天的手动记录", manual_block.strip() or "（这一天没有手动记录）",
        "", f"## 昨天（{yesterday}）的手动记录：解释今天的睡眠 / HRV / 静息心率时看这里",
        prev_manual_block.strip() or "（没有）",
        "", "## 用户在今天笔记「备注」里写的（症状、感受等）", user_notes.strip() or "（没写）",
        "", "## 最近 7 天", recent_table(history, day),
        "", "## 个人基线（今天之前最多 28 天）", baseline_table(baseline(history, day)),
        "", "## 体重趋势（手动记录）", weight_trend(weights, day),
        "", "## 最近几次 InBody", inbody_history(blocks, day),
    ]
    if training_day:
        parts += ["", "## 每个动作最近几次的记录（今天之前）", training_history(blocks, day)]
    return "\n".join(parts)


def _week_rows(history, monday, weeks, train_days, blocks):
    rows = ["| 周 | 体重均值（次数） | 静息心率 | HRV | 睡眠 h | 步数 | 力量训练 完成/计划 |", "|---|---|---|---|---|---|---|"]
    for w in range(weeks - 1, -1, -1):
        start = monday - timedelta(days=7 * w)
        days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        col = lambda key: [history[k][key] for k in days if isinstance(history.get(k, {}).get(key), (int, float))]
        mean = lambda key, nd: _f(statistics.fmean(col(key)), nd) if col(key) else "—"
        done = sum(1 for k in days if manual_log.training_rows(blocks.get(k, ""))
                   or "力量" in str(history.get(k, {}).get("workout_type") or "")
                   or "strength" in str(history.get(k, {}).get("workout_type") or "").lower())
        iso = start.isocalendar()
        rows.append(f"| {iso[0]}-W{iso[1]:02d} | {mean('weight', 2)}（{len(col('weight'))}） | {mean('resting_hr', 1)} | "
                    f"{mean('hrv', 1)} | {mean('sleep_hours', 2)} | {mean('steps', 0)} | {done}/{len(train_days)} |")
    return "\n".join(rows)


def weekly_charts(history, monday, weeks=4):
    """最近 4 周按天画的 mermaid 趋势图（Obsidian 原生渲染）。数字由代码填，不让 AI 抄。缺数据的日子不画。"""
    start = monday - timedelta(days=7 * (weeks - 1))
    days = [(start + timedelta(days=i)).isoformat() for i in range(7 * weeks)]
    out = []
    for key, title, unit in (("weight", "体重", "kg"), ("resting_hr", "静息心率", "次/分"),
                             ("hrv", "HRV（SDNN）", "ms"), ("sleep_hours", "睡眠时长", "小时")):
        pts = [(k[5:], history[k][key]) for k in days if isinstance(history.get(k, {}).get(key), (int, float))]
        if len(pts) < 2:
            out.append(f"**{title}**：数据不足，不画图。")
            continue
        lo, hi = min(v for _, v in pts), max(v for _, v in pts)
        pad = max((hi - lo) * 0.15, 0.5)
        out += ["```mermaid", "xychart-beta", f'    title "{title}（{unit}）"',
                "    x-axis [" + ", ".join(f'"{k}"' for k, _ in pts) + "]",
                f'    y-axis "{unit}" {lo - pad:.1f} --> {hi + pad:.1f}',
                "    line [" + ", ".join(f"{v:g}" for _, v in pts) + "]", "```"]
        missing = len(days) - len(pts)
        if missing:
            out.append(f"（4 周里有 {missing} 天没有{title}数据，图上跳过了这些天）")
    return "\n".join(out)


def build_weekly_message(*, label, monday, history, statuses, blocks, profile, train_days):
    """monday: 上周一。表格和趋势都在代码里算好；手动记录只发最近 5 周。"""
    sunday = monday + timedelta(days=6)
    first = (monday - timedelta(days=28)).isoformat()
    recent_blocks = "\n".join(blocks[k] for k in sorted(blocks) if first <= k <= sunday.isoformat())
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    return "\n".join([
        f"周报：{label}（{monday:%m-%d} ~ {sunday:%m-%d}）",
        "", "## 个人档案", profile.strip() or "（用户没有填个人档案）",
        "", "## 最近 4 周汇总（代码算好的，直接用）", _week_rows(history, monday, 4, train_days, blocks),
        "", "## 上周每天", recent_table(history, sunday.isoformat()),
        "每天报告状态：" + "，".join(f"{k[5:]} {statuses.get(k, '没有报告')}" for k in days),
        "", "## 体重趋势（截至上周日）", weight_trend({k: v["weight"] for k, v in history.items() if v.get("weight") is not None}, sunday.isoformat()),
        "", "## 最近几次 InBody", inbody_history(blocks, sunday.isoformat()),
        "", "## 最近 5 周的手动记录", recent_blocks or "（没有）",
    ])


# ---------- 调用 Claude ----------

def has_credentials():
    import anthropic
    c = anthropic.Anthropic()
    return bool(c.api_key or c.auth_token or getattr(c, "credentials", None))


_client = None


def _default_client():
    global _client
    if _client is None:
        import anthropic
        if not has_credentials():
            raise NoCredentials("没有找到 Claude 登录信息：在 .env 填 ANTHROPIC_API_KEY，或先运行 `ant auth login`")
        _client = anthropic.Anthropic(max_retries=4)   # 无人值守：429 / 529 / 5xx 多退避几次
    return _client


def generate(cfg, system, user, client=None, effort=None, cache_ttl=None, log=print):
    """调用 Claude 写报告，返回 Markdown。只接受正常写完（end_turn）的结果，截断或拒绝都抛 ReportError。"""
    client = client or _default_client()
    sys_block = {"type": "text", "text": system}
    if cache_ttl:      # 一次要写好几天时，把不变的指令缓存起来
        sys_block["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}
    extra = {}
    if cfg.fallbacks and cfg.model in FALLBACK_MODELS:
        # 安全分类器误拦时，服务端按拒绝类别自动换一个模型重跑
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=MAX_TOKENS,
        system=[sys_block],
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort or cfg.effort},
        **extra,
    ) as stream:
        msg = stream.get_final_message()
    u = msg.usage
    log(f"  Claude：输入 {u.input_tokens} / 输出 {u.output_tokens} tokens"
        + (f"，缓存命中 {u.cache_read_input_tokens}" if getattr(u, "cache_read_input_tokens", 0) else ""))
    if msg.stop_reason == "refusal":
        sd = getattr(msg, "stop_details", None)
        cat = getattr(sd, "category", None) if sd else None
        raise Refused(f"Claude 拒绝了这次请求（类别：{cat or '未知'}），这一天先跳过",
                      retry_later=bool(getattr(sd, "recommended_model", None)) if sd else False)
    if msg.stop_reason == "max_tokens":
        raise ReportError(f"报告写到 max_tokens={MAX_TOKENS} 被截断，这次不写，保留旧报告")
    if msg.stop_reason not in ("end_turn", "stop_sequence"):
        raise ReportError(f"Claude 没有正常写完（stop_reason={msg.stop_reason}）")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise ReportError("Claude 没有返回正文")
    return text
