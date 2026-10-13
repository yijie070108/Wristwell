"""从缓存的 Health Auto Export 数据（JSON v2）算出每日笔记 frontmatter 里的数字，以及发给 AI 的指标摘要。"""
from collections import defaultdict

from wristwell.cache_merge import CUMULATIVE, day_of

KJ_PER_KCAL = 4.184
KM_PER = {"km": 1.0, "mi": 1.609344, "m": 0.001, "yd": 0.0009144, "ft": 0.0003048}

# 每日笔记 frontmatter 的自动字段，顺序即写入顺序
FIELDS = [
    "active_kcal", "resting_kcal", "total_kcal", "steps",
    "sleep_hours", "deep_sleep_min", "rem_min", "resting_hr", "hrv",
    "workout_type", "workout_min", "workout_kcal", "workout_avg_hr",
]

# 只有这些指标会发给 Claude。「健康」App 里的其他类别（生理周期、血糖、性行为、用药…）一律不发。
# 想多发什么，在 config.json 的 extra_metrics 里加指标名。
SEND_METRICS = {
    "step_count", "walking_running_distance", "active_energy", "basal_energy_burned",
    "apple_exercise_time", "apple_stand_hour", "apple_stand_time", "flights_climbed",
    "time_in_daylight", "resting_heart_rate", "heart_rate", "heart_rate_variability",
    "walking_heart_rate_average", "blood_oxygen_saturation", "respiratory_rate",
    "sleep_analysis", "vo2_max", "apple_sleeping_wrist_temperature", "breathing_disturbances",
    "weight_body_mass", "body_fat_percentage", "lean_body_mass", "cardio_recovery",
    "physical_effort", "six_minute_walking_test_distance", "walking_speed",
}

# 多天对比用的额外数字（不写进 frontmatter，只给 AI 看）
EXTRA = {
    "respiratory_rate": "resp_rate", "blood_oxygen_saturation": "spo2",
    "apple_sleeping_wrist_temperature": "wrist_temp", "vo2_max": "vo2max",
    "walking_heart_rate_average": "walking_hr",
}


def _by_name(metrics):
    return {m["name"]: m for m in metrics if m.get("data")}


def _qtys(metric):
    return [p["qty"] for p in metric["data"] if isinstance(p.get("qty"), (int, float))]


def _sum(metric):
    return sum(_qtys(metric))


def _mean(metric):
    q = _qtys(metric)
    return sum(q) / len(q) if q else None


def _kcal(metric):
    v = _sum(metric)
    return v / KJ_PER_KCAL if str(metric.get("units", "")).lower() == "kj" else v


def _energy_kcal(q):
    """训练里的 {"qty":..,"units":"kJ"|"kcal"} → 大卡。"""
    if not isinstance(q, dict) or not isinstance(q.get("qty"), (int, float)):
        return 0
    return q["qty"] / KJ_PER_KCAL if str(q.get("units", "")).lower() == "kj" else q["qty"]


def _km(q):
    if not isinstance(q, dict) or not isinstance(q.get("qty"), (int, float)):
        return None
    f = KM_PER.get(str(q.get("units") or "km").lower())
    return q["qty"] * f if f else None


def _sleep_amount(p):
    v = p.get("totalSleep") or p.get("asleep") or 0
    return v if isinstance(v, (int, float)) else 0


def main_sleep(metric):
    """一天可能有好几条睡眠记录（手表 + 手机、午睡、别的睡眠 App）：取睡得最多的那条当「昨晚」。"""
    return max(metric["data"], key=_sleep_amount)


def _round(v):
    return round(v) if isinstance(v, (int, float)) else None


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
        out["resting_hr"] = _round(_mean(m["resting_heart_rate"]))
    if "heart_rate_variability" in m:
        out["hrv"] = _round(_mean(m["heart_rate_variability"]))
    if "sleep_analysis" in m:
        s = main_sleep(m["sleep_analysis"])
        total = _sleep_amount(s)
        if total:
            out["sleep_hours"] = round(total, 1)
            if isinstance(s.get("deep"), (int, float)) and s["deep"]:
                out["deep_sleep_min"] = round(s["deep"] * 60)
            if isinstance(s.get("rem"), (int, float)) and s["rem"]:
                out["rem_min"] = round(s["rem"] * 60)
    return out


def extra_fields(metrics):
    """多天对比用：呼吸频率、血氧、手腕温度、最大摄氧量、步行心率、日照、入睡 / 醒来时间、清醒时长。"""
    m = _by_name(metrics)
    out = {}
    for name, key in EXTRA.items():
        if name in m and _mean(m[name]) is not None:
            out[key] = round(_mean(m[name]), 1)
    if "time_in_daylight" in m:
        out["daylight_min"] = round(_sum(m["time_in_daylight"]))
    if "sleep_analysis" in m:
        s = main_sleep(m["sleep_analysis"])
        for k, key in (("sleepStart", "sleep_start"), ("sleepEnd", "sleep_end")):
            if s.get(k):
                out[key] = str(s[k])[11:16]
        if isinstance(s.get("awake"), (int, float)):
            out["awake_min"] = round(s["awake"] * 60)
    return out


def looks_unfinished(w):
    """忘了点结束的记录：骑行 / 跑步 / 步行超过 30 分钟，平均速度却低于 3 km/h。"""
    minutes = (w.get("duration") or 0) / 60 if isinstance(w.get("duration"), (int, float)) else 0
    km = _km(w.get("distance"))
    name = str(w.get("name", "")).lower().replace(" ", "")
    moving = any(k in name for k in ("骑", "跑", "步行", "健走", "cycl", "bike", "run", "walk"))
    return bool(moving and km is not None and minutes > 30 and km / (minutes / 60) < 3)


SLIM_DROP = {"route", "heartRateData", "heartRateRecovery", "activeEnergy", "basalEnergy",
             "stepCount", "cyclingDistance", "walkingAndRunningDistance", "segments", "splits", "events"}


def slim(w):
    """缓存训练记录时去掉 GPS 路线和逐秒数据：用不上，也不该留着。"""
    return {k: v for k, v in w.items() if k not in SLIM_DROP}


def split_workouts(export):
    """训练导出（按天或按周）→ {YYYY-MM-DD: [workout, ...]}，按开始时间分日。"""
    days = defaultdict(list)
    data = export.get("data") if isinstance(export, dict) else None
    ws = data.get("workouts") if isinstance(data, dict) else None
    for w in ws if isinstance(ws, list) else []:
        day = day_of(w.get("start")) if isinstance(w, dict) else None
        if day:
            days[day].append(slim(w))
    return dict(days)


def workout_summary(workouts):
    """一天多次训练：类型按开始时间去重拼接，时长和消耗求和，心率按时长加权平均。
    返回 (frontmatter 字段, 给 AI 看的逐条明细)。"""
    workouts = sorted(workouts, key=lambda w: str(w.get("start", "")))
    detail, counted = [], []
    for w in workouts:
        hr = (w.get("heartRate") or {}).get("avg") or w.get("avgHeartRate") or {}
        km = _km(w.get("distance"))
        dur = w.get("duration") if isinstance(w.get("duration"), (int, float)) else 0
        item = {
            "type": str(w.get("name") or "训练").strip(),
            "start": w.get("start"), "end": w.get("end"),
            "minutes": round(dur / 60),
            "kcal": round(_energy_kcal(w.get("activeEnergyBurned"))),
            "avg_hr": _round(hr.get("qty")) if isinstance(hr, dict) else None,
            "distance_km": round(km, 2) if km else None,
            "suspicious": looks_unfinished(w),
        }
        detail.append(item)
        if not item["suspicious"]:
            counted.append(item)
    fields = {}
    if counted:
        hr_w = [(i["avg_hr"], i["minutes"]) for i in counted if i["avg_hr"] and i["minutes"]]
        fields = {
            "workout_type": ", ".join(dict.fromkeys(i["type"] for i in counted)),
            "workout_min": sum(i["minutes"] for i in counted),
            "workout_kcal": sum(i["kcal"] for i in counted),
            "workout_avg_hr": round(sum(h * t for h, t in hr_w) / sum(t for _, t in hr_w)) if hr_w else None,
        }
    return fields, detail


def _fmt(v):
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def compact(metrics, allow=SEND_METRICS):
    """把当天指标压成「名字: 数值 单位」的短列表，给 AI 当原始材料。只发 allow 里的指标。"""
    lines = []
    for m in metrics:
        name, units = m.get("name"), m.get("units", "")
        if not m.get("data") or name not in allow:
            continue
        pts = m["data"]
        if name == "sleep_analysis":
            keys = ("totalSleep", "asleep", "deep", "rem", "core", "awake", "inBed", "sleepStart", "sleepEnd", "source")
            main = main_sleep(m)
            ordered = [main] + [p for p in pts if p is not main]
            val = " ｜ ".join(", ".join(f"{k}={_fmt(p[k])}" for k in keys if p.get(k) not in (None, "", 0))
                             for p in ordered)
        elif any("Avg" in p for p in pts):
            avgs = [p["Avg"] for p in pts if isinstance(p.get("Avg"), (int, float))]
            mins = [p["Min"] for p in pts if isinstance(p.get("Min"), (int, float))]
            maxs = [p["Max"] for p in pts if isinstance(p.get("Max"), (int, float))]
            val = f"avg={sum(avgs) / len(avgs):.0f} min={min(mins) if mins else '?'} max={max(maxs) if maxs else '?'}" if avgs else "?"
        elif any("systolic" in p for p in pts):
            val = "; ".join(f"{p.get('systolic')}/{p.get('diastolic')}" for p in pts)
        elif name in CUMULATIVE:
            val = f"{_sum(m):.1f}"
        else:
            q = _qtys(m)
            if not q:
                continue
            val = _fmt(q[0]) if len(q) == 1 else f"avg={sum(q) / len(q):.1f} min={min(q):.1f} max={max(q):.1f}（{len(q)} 个读数）"
        lines.append(f"- {name}: {val} {units}".rstrip())
    return "\n".join(lines)
