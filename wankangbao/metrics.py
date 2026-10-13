"""从缓存的 Health Auto Export 数据（JSON v2）算出每日笔记 frontmatter 里的数字。"""
from collections import defaultdict

KJ_PER_KCAL = 4.184

# 每日笔记 frontmatter 的自动字段，顺序即写入顺序
FIELDS = [
    "active_kcal", "resting_kcal", "total_kcal", "steps",
    "sleep_hours", "deep_sleep_min", "rem_min", "resting_hr", "hrv",
    "workout_type", "workout_min", "workout_kcal", "workout_avg_hr",
]


def _by_name(metrics):
    return {m["name"]: m for m in metrics if m.get("data")}


def _sum(metric):
    return sum(p.get("qty", 0) or 0 for p in metric["data"])


def _last(metric):
    return metric["data"][-1].get("qty")


def _kcal(metric):
    v = _sum(metric)
    return v / KJ_PER_KCAL if str(metric.get("units", "")).lower() == "kj" else v


def _energy_kcal(q):
    """训练里的 {"qty":..,"units":"kJ"|"kcal"} → 大卡。"""
    if not q:
        return 0
    v = q.get("qty", 0) or 0
    return v / KJ_PER_KCAL if str(q.get("units", "")).lower() == "kj" else v


def daily_fields(metrics):
    """metrics: 某一天的指标列表（缓存里的 data.metrics）。返回 {字段: 数值 或 None}。"""
    m = _by_name(metrics)
    out = dict.fromkeys(FIELDS)
    if "active_energy" in m:
        out["active_kcal"] = round(_kcal(m["active_energy"]))
    if "basal_energy_burned" in m:
        out["resting_kcal"] = round(_kcal(m["basal_energy_burned"]))
    if out["active_kcal"] is not None and out["resting_kcal"] is not None:
        out["total_kcal"] = out["active_kcal"] + out["resting_kcal"]
    if "step_count" in m:
        out["steps"] = round(_sum(m["step_count"]))
    if "resting_heart_rate" in m:
        out["resting_hr"] = round(_last(m["resting_heart_rate"]))
    if "heart_rate_variability" in m:
        out["hrv"] = round(_last(m["heart_rate_variability"]))
    if "sleep_analysis" in m:
        s = m["sleep_analysis"]["data"][-1]
        total = s.get("totalSleep") or s.get("asleep") or 0
        if total:
            out["sleep_hours"] = round(total, 1)
            if s.get("deep"):
                out["deep_sleep_min"] = round(s["deep"] * 60)
            if s.get("rem"):
                out["rem_min"] = round(s["rem"] * 60)
    return out


def looks_unfinished(w):
    """忘了点结束的记录：骑行 / 跑步 / 步行超过 30 分钟，平均速度却低于 3 km/h。"""
    minutes = (w.get("duration") or 0) / 60
    km = (w.get("distance") or {}).get("qty")
    name = str(w.get("name", ""))
    moving = any(k in name for k in ("骑行", "跑", "步行", "Cycling", "Run", "Walk"))
    return bool(moving and km is not None and minutes > 30 and km / (minutes / 60) < 3)


def split_workouts(export):
    """训练导出（按天或按周）→ {YYYY-MM-DD: [workout, ...]}，按开始时间分日。"""
    days = defaultdict(list)
    for w in export.get("data", {}).get("workouts", []):
        days[str(w.get("start", ""))[:10]].append(w)
    return dict(days)


def workout_summary(workouts):
    """一天多次训练：类型按开始时间去重拼接，时长和消耗求和，心率按时长加权平均。
    返回 (frontmatter 字段, 给 AI 看的逐条明细)。"""
    workouts = sorted(workouts, key=lambda w: w.get("start", ""))
    detail, counted = [], []
    for w in workouts:
        hr = (w.get("heartRate") or {}).get("avg") or w.get("avgHeartRate") or {}
        item = {
            "type": w.get("name", "训练"),
            "start": w.get("start"), "end": w.get("end"),
            "minutes": round((w.get("duration") or 0) / 60),
            "kcal": round(_energy_kcal(w.get("activeEnergyBurned"))),
            "avg_hr": round(hr["qty"]) if hr.get("qty") else None,
            "distance_km": round(w["distance"]["qty"], 2) if (w.get("distance") or {}).get("qty") else None,
            "suspicious": looks_unfinished(w),
        }
        detail.append(item)
        if not item["suspicious"]:
            counted.append(item)
    fields = {}
    if counted:
        types = list(dict.fromkeys(i["type"] for i in counted))
        mins = sum(i["minutes"] for i in counted)
        hr_w = [(i["avg_hr"], i["minutes"]) for i in counted if i["avg_hr"]]
        fields = {
            "workout_type": ", ".join(types),
            "workout_min": mins,
            "workout_kcal": sum(i["kcal"] for i in counted),
            "workout_avg_hr": round(sum(h * t for h, t in hr_w) / sum(t for _, t in hr_w)) if hr_w and sum(t for _, t in hr_w) else None,
        }
    return fields, detail


def compact(metrics):
    """把当天所有指标压成「名字: 数值 单位」的短列表，给 AI 当原始材料。"""
    lines = []
    for m in metrics:
        if not m.get("data"):
            continue
        p = m["data"][-1]
        name, units = m["name"], m.get("units", "")
        if name == "sleep_analysis":
            keys = ("totalSleep", "deep", "rem", "core", "awake", "sleepStart", "sleepEnd")
            val = ", ".join(f"{k}={p[k]:.2f}" if isinstance(p.get(k), float) else f"{k}={p.get(k)}" for k in keys if k in p)
        elif "Avg" in p:
            val = f"avg={p['Avg']:.0f} min={p.get('Min')} max={p.get('Max')}"
        elif name in ("active_energy", "basal_energy_burned") or len(m["data"]) > 1:
            val = f"{_sum(m):.1f}"
        else:
            q = p.get("qty")
            val = f"{q:.2f}" if isinstance(q, float) else str(q)
        lines.append(f"- {name}: {val} {units}".rstrip())
    return "\n".join(lines)
