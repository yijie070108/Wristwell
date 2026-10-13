#!/usr/bin/env python3
"""把 Health Auto Export 的导出合并进按天的缓存，防止后台导出用「残缺版」覆盖完整数据。

背景：iOS 后台导出偶尔只带一部分指标（比如步数、消耗、心率都没了），
直接读导出文件会把之前的好数据冲掉。所以写报告只读缓存，不直接读导出。

规则（逐项指标）：
- 缓存里没有 → 加进去
- 累加型（步数、消耗、距离、站立、锻炼时间…）→ 取全天合计更大的那份（一天的合计只会变大）
- 睡眠 → 按来源、睡眠时段保留完整记录；相同时段的完整新记录可纠错
- 其他（HRV、静息心率、血氧…）→ 新导出里有就用新的，没有就保留旧的

命令行用法（只用标准库，可以单独拷走用）:
    python3 cache_merge.py <导出.json> <缓存目录>     # 按天拆开，写成 缓存目录/YYYY-MM-DD.json
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
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


def _sleep_tz(points):
    """混有无时区时间时，用同一份睡眠数据里的明确时区；全无时区则保持同一时钟。"""
    for p in points:
        for key in ("sleepStart", "sleepEnd", "startDate", "endDate"):
            t = _time(p.get(key))
            if t is not None and t.tzinfo is not None:
                return t.tzinfo
    return timezone.utc


def _sleep_window(point, tz):
    start = _time(point.get("sleepStart") or point.get("startDate"))
    end = _time(point.get("sleepEnd") or point.get("endDate"))
    if start is None or end is None:
        return None
    start = start if start.tzinfo is not None else start.replace(tzinfo=tz)
    end = end if end.tzinfo is not None else end.replace(tzinfo=tz)
    return (start, end) if end > start else None


def _sleep_source(point):
    return json.dumps(point.get("source", ""), ensure_ascii=False, sort_keys=True)


def _aggregate_sleep(segments):
    # 相同来源的每段时间只算一次；已分期的睡眠优先于笼统的 Asleep。
    # InBed 是包含睡眠的外层区间，因此单独计算，不加进 totalSleep。
    keys = ("deep", "rem", "core", "awake", "asleep", "inBed")
    events = defaultdict(lambda: defaultdict(int))
    for start, end, key, _ in segments:
        events[start][key] += 1
        events[end][key] -= 1
    agg = dict.fromkeys(keys, 0.0)
    active, prev = defaultdict(int), None
    for moment in sorted(events):
        if prev is not None:
            hours = (moment - prev).total_seconds() / 3600
            stage = next((key for key in keys[:-1] if active[key] > 0), None)
            if stage:
                agg[stage] += hours
            if active["inBed"] > 0:
                agg["inBed"] += hours
        for key, delta in events[moment].items():
            active[key] += delta
        prev = moment
    start = min(s[0] for s in segments)
    end = max(s[1] for s in segments)
    agg["totalSleep"] = sum(agg[key] for key in ("core", "deep", "rem", "asleep"))
    agg.update(sleepStart=start.strftime("%Y-%m-%d %H:%M:%S %z"),
               sleepEnd=end.strftime("%Y-%m-%d %H:%M:%S %z"),
               date=end.strftime("%Y-%m-%d 00:00:00 %z"), source=segments[0][3].get("source", ""))
    return agg


def _sleep_sessions(points):
    """原始睡眠分段按来源及睡眠时段汇总；重叠的设备记录不相加。"""
    sources = defaultdict(list)
    tz = _sleep_tz(points)
    for point in points:
        window = _sleep_window(point, tz)
        key = SLEEP_STAGE.get(str(point.get("value", "")).strip().lower())
        if window and key:
            sources[_sleep_source(point)].append((*window, key, point))
    out = []
    for source in sorted(sources):
        cur, latest_end = [], None
        for segment in sorted(sources[source], key=lambda s: (s[0], s[1])):
            if cur and (segment[0] - latest_end).total_seconds() > 2 * 3600:
                out.append(_aggregate_sleep(cur))
                cur, latest_end = [], None
            cur.append(segment)
            latest_end = max(latest_end, segment[1]) if latest_end is not None else segment[1]
        if cur:
            out.append(_aggregate_sleep(cur))
    return out


def _sleep_amount(point):
    amount = point.get("totalSleep") or point.get("asleep") or 0
    return amount if isinstance(amount, (int, float)) else 0


def _sleep_complete(old, new):
    """不能用缺少已知分期的记录下修完整睡眠；0 也是有效的纠错值。"""
    return all(isinstance(new.get(key), (int, float))
               for key in ("core", "deep", "rem", "awake", "asleep", "inBed")
               if isinstance(old.get(key), (int, float)) and old[key] > 0)


def _merge_sleep(old, new):
    """按来源/时段更新睡眠，不因这次只导出午睡而丢掉昨晚。

    同一时段或覆盖旧时段且分期完整的新记录可以向下纠错。仅有一段
    较窄片段不替换整晚。没有起止时刻时无法分辨残缺与真实下修，
    保守保留较完整的旧总量；这类下修需要完整时段数据或人工清缓存。
    """
    rows = list(old["data"])
    tz = _sleep_tz(rows + new["data"])
    for point in new["data"]:
        source = _sleep_source(point)
        window = _sleep_window(point, tz)
        peers = [i for i, p in enumerate(rows) if _sleep_source(p) == source]
        matches = []
        for i in peers:
            previous = _sleep_window(rows[i], tz)
            if window and previous:
                if window[0] < previous[1] and previous[0] < window[1]:
                    matches.append(i)
            elif previous is None or window is None:
                matches.append(i)
        if not matches:
            rows.append(point)
            continue
        replace = True
        for i in matches:
            previous = _sleep_window(rows[i], tz)
            if window and previous:
                covers = window[0] <= previous[0] and window[1] >= previous[1]
                replace = replace and covers and _sleep_complete(rows[i], point)
            else:
                # 没有时段身份时仅接受不下降、分期完整的更新。
                replace = replace and _sleep_amount(point) >= _sleep_amount(rows[i]) and _sleep_complete(rows[i], point)
        if replace:
            first = matches[0]
            rows[first] = point
            rows = [p for i, p in enumerate(rows) if i == first or i not in matches]
    return {**new, "data": rows}


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
        if name == "sleep_analysis" and cur is not None:
            m = _merge_sleep(cur, m)
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
