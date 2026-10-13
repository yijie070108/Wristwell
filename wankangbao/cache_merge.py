#!/usr/bin/env python3
"""把 Health Auto Export 的导出合并进按天的缓存，防止后台导出用「残缺版」覆盖完整数据。

背景：iOS 后台导出偶尔只带一部分指标（比如步数、消耗、心率都没了），
直接读导出文件会把之前的好数据冲掉。所以写报告只读缓存，不直接读导出。

规则（逐项指标）：
- 缓存里没有 → 加进去
- 累加型（步数、消耗、距离、站立、锻炼时间…）→ 取全天合计更大的那份（一天的合计只会变大）
- 其他（HRV、静息心率、睡眠、血氧…）→ 新导出里有就用新的，没有就保留旧的

命令行用法（只用标准库，可以单独拷走用）:
    python3 cache_merge.py <导出.json> <缓存目录>     # 按天拆开，写成 缓存目录/YYYY-MM-DD.json
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CUMULATIVE = {
    "step_count", "walking_running_distance", "active_energy", "basal_energy_burned",
    "apple_stand_hour", "apple_stand_time", "apple_exercise_time", "flights_climbed",
    "time_in_daylight", "cycling_distance", "swimming_distance", "dietary_energy",
}
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SLEEP_STAGE = {"core": "core", "deep": "deep", "rem": "rem", "awake": "awake",
               "asleep": "asleep", "in bed": "inBed", "inbed": "inBed"}


def day_of(value):
    """'2026-01-05 00:00:00 +0800' → '2026-01-05'；不像日期就返回 None（也防止拿来拼路径）。"""
    s = str(value or "")[:10]
    return s if DAY_RE.fullmatch(s) else None


def _time(s):
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(s), fmt)
        except ValueError:
            pass
    return None


def total(metric):
    return sum(x.get("qty", 0) or 0 for x in metric.get("data", []) if isinstance(x.get("qty", 0), (int, float)))


def _sleep_sessions(points):
    """未汇总的睡眠分段（startDate/endDate/value）→ 汇总格式，按醒来那天记日期。"""
    segs = sorted((p for p in points if _time(p.get("startDate")) and _time(p.get("endDate"))),
                  key=lambda p: _time(p["startDate"]))
    sessions, cur = [], []
    for p in segs:
        if cur and (_time(p["startDate"]) - max(_time(x["endDate"]) for x in cur)).total_seconds() > 2 * 3600:
            sessions.append(cur)
            cur = []
        cur.append(p)
    if cur:
        sessions.append(cur)
    out = []
    for s in sessions:
        agg = {"core": 0.0, "deep": 0.0, "rem": 0.0, "awake": 0.0, "asleep": 0.0, "inBed": 0.0}
        for p in s:
            key = SLEEP_STAGE.get(str(p.get("value", "")).strip().lower())
            if key:
                agg[key] += (_time(p["endDate"]) - _time(p["startDate"])).total_seconds() / 3600
        start = min(_time(p["startDate"]) for p in s)
        end = max(_time(p["endDate"]) for p in s)
        agg["totalSleep"] = agg["core"] + agg["deep"] + agg["rem"] + agg["asleep"]
        agg.update(sleepStart=start.strftime("%Y-%m-%d %H:%M:%S %z"), sleepEnd=end.strftime("%Y-%m-%d %H:%M:%S %z"),
                   date=end.strftime("%Y-%m-%d 00:00:00 %z").strip(), source=s[0].get("source", ""))
        out.append(agg)
    return out


def split_by_day(export):
    """一个导出文件可能是一天，也可能是一周：按日期拆成 {YYYY-MM-DD: [metric, ...]}。"""
    days = defaultdict(dict)
    data = export.get("data") if isinstance(export, dict) else None
    metrics = data.get("metrics") if isinstance(data, dict) else None
    for m in metrics if isinstance(metrics, list) else []:
        if not isinstance(m, dict) or not m.get("name") or not isinstance(m.get("data"), list):
            continue
        points = [p for p in m["data"] if isinstance(p, dict)]
        if m["name"] == "sleep_analysis" and points and all("date" not in p for p in points):
            points = _sleep_sessions(points)
        for point in points:
            day = day_of(point.get("date"))
            if not day:
                continue
            cur = days[day].setdefault(m["name"], {k: v for k, v in m.items() if k != "data"} | {"data": []})
            cur["data"].append(point)
    return {d: list(ms.values()) for d, ms in days.items()}


def merge_metrics(old_metrics, new_metrics):
    """返回 (合并后的指标列表, 变化了的指标名)。"""
    merged = {m["name"]: m for m in old_metrics if m.get("data")}
    changed = []
    for m in new_metrics:
        if not m.get("data"):
            continue
        name = m["name"]
        cur = merged.get(name)
        if cur is None or (total(m) >= total(cur) if name in CUMULATIVE else m != cur):
            if cur != m:
                changed.append(name)
            merged[name] = m
    return list(merged.values()), changed


def write_json(path, obj, indent=None):
    """先写临时文件再替换：写到一半被打断也不会留下坏文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default):
    """读不到返回 default；文件坏了就改名成 .corrupt 再返回 default，不让后面每次都崩。"""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:
        path.replace(path.with_name(path.name + ".corrupt"))
        return default


def merge_into_cache(day_metrics, cache_path):
    old = read_json(cache_path, None)
    if not isinstance(old, dict) or not isinstance(old.get("data"), dict):
        old = {"data": {"metrics": []}}
    old["data"]["metrics"], changed = merge_metrics(old["data"].get("metrics") or [], day_metrics)
    write_json(cache_path, old)
    return changed


def main(src, cache_dir):
    export = json.loads(Path(src).read_text(encoding="utf-8"))
    for day, ms in sorted(split_by_day(export).items()):
        changed = merge_into_cache(ms, Path(cache_dir) / f"{day}.json")
        print(f"{day} changed:", ", ".join(changed) if changed else "none")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
