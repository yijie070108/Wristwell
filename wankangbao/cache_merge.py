#!/usr/bin/env python3
"""把 Health Auto Export 的导出合并进按天的缓存，防止后台导出用「残缺版」覆盖完整数据。

背景：iOS 后台导出偶尔只带一部分指标（比如步数、消耗、心率都没了），
直接读导出文件会把之前的好数据冲掉。所以写报告只读缓存，不直接读导出。

规则（逐项指标）：
- 缓存里没有 → 加进去
- 累加型（步数、消耗、距离、站立、锻炼时间…）→ 取全天合计更大的那份（一天的合计只会变大）
- 其他（HRV、静息心率、睡眠、血氧…）→ 新导出里有就用新的，没有就保留旧的

命令行用法: python3 -m wankangbao.cache_merge <导出.json> <缓存/YYYY-MM-DD.json>
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

CUMULATIVE = {
    "step_count", "walking_running_distance", "active_energy", "basal_energy_burned",
    "apple_stand_hour", "apple_stand_time", "apple_exercise_time", "flights_climbed",
    "time_in_daylight", "cycling_distance", "swimming_distance", "dietary_energy",
}


def total(metric):
    return sum(x.get("qty", 0) or 0 for x in metric.get("data", []))


def split_by_day(export):
    """一个导出文件可能是一天，也可能是一周：按 data[].date 拆成 {YYYY-MM-DD: [metric, ...]}。"""
    days = defaultdict(dict)
    for m in export.get("data", {}).get("metrics", []):
        for point in m.get("data", []):
            day = str(point.get("date", ""))[:10]
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


def merge_into_cache(day_metrics, cache_path):
    cache_path = Path(cache_path)
    try:
        old = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        old = {"data": {"metrics": []}}
    old["data"]["metrics"], changed = merge_metrics(old["data"]["metrics"], day_metrics)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    return changed


def main(src, dst):
    export = json.loads(Path(src).read_text(encoding="utf-8"))
    metrics = export.get("data", {}).get("metrics", [])
    changed = merge_into_cache(metrics, dst)
    print("changed:", ", ".join(changed) if changed else "none")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
