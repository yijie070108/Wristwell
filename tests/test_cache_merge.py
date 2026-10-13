from wristwell.cache_merge import merge_into_cache, merge_metrics, split_by_day


def m(name, qty, day="2026-01-05"):
    return {"name": name, "units": "count", "data": [{"qty": qty, "date": f"{day} 00:00:00 +0800"}]}


def test_cumulative_keeps_larger_total():
    merged, changed = merge_metrics([m("step_count", 9000)], [m("step_count", 3000)])
    assert merged[0]["data"][0]["qty"] == 9000 and changed == []


def test_partial_export_does_not_drop_metrics(tmp_path):
    cache = tmp_path / "c.json"
    merge_into_cache([m("step_count", 9000), m("resting_heart_rate", 60)], cache)
    changed = merge_into_cache([m("resting_heart_rate", 58)], cache)   # 残缺版导出：没有步数
    import json
    names = {x["name"]: x["data"][0]["qty"] for x in json.loads(cache.read_text())["data"]["metrics"]}
    assert names == {"step_count": 9000, "resting_heart_rate": 58}
    assert changed == ["resting_heart_rate"]


def test_split_week_export_by_day():
    export = {"data": {"metrics": [{"name": "step_count", "units": "count", "data": [
        {"qty": 1, "date": "2026-01-05 00:00:00 +0800"}, {"qty": 2, "date": "2026-01-06 00:00:00 +0800"}]}]}}
    days = split_by_day(export)
    assert set(days) == {"2026-01-05", "2026-01-06"}
    assert days["2026-01-06"][0]["data"][0]["qty"] == 2


# 全部为虚构的睡眠记录；日期/来源相同不代表午睡就是整晚的新版本。
def sleep_point(hours, start="2026-01-04 23:00:00 +0800", end="2026-01-05 07:00:00 +0800", source="Watch"):
    from datetime import datetime
    span = ((datetime.strptime(end, "%Y-%m-%d %H:%M:%S %z") -
             datetime.strptime(start, "%Y-%m-%d %H:%M:%S %z")).total_seconds() / 3600) if start else 8
    point = {"date": "2026-01-05 00:00:00 +0800", "source": source,
             "totalSleep": hours, "core": max(0, hours - 1), "deep": min(hours, 1), "rem": 0, "awake": max(0, span - hours),
             "asleep": 0, "inBed": 0}
    if start is not None:
        point.update(sleepStart=start, sleepEnd=end)
    return point


def sleep_metric(*points):
    return {"name": "sleep_analysis", "units": "hr", "data": list(points)}


def sleep_fields(metric):
    from wristwell.metrics import daily_fields
    return daily_fields([metric])


def test_partial_sleep_fragment_keeps_complete_night():
    old = sleep_metric(sleep_point(7.5))
    fragment = sleep_metric(sleep_point(.5, "2026-01-05 06:30:00 +0800", "2026-01-05 07:00:00 +0800"))
    merged, changed = merge_metrics([old], [fragment])
    assert sleep_fields(merged[0])["sleep_hours"] == 7.5
    assert changed == []


def test_nap_only_export_keeps_complete_night_and_adds_nap():
    old = sleep_metric(sleep_point(7.5))
    nap = sleep_point(.5, "2026-01-05 14:00:00 +0800", "2026-01-05 14:30:00 +0800")
    merged, changed = merge_metrics([old], [sleep_metric(nap)])
    assert sleep_fields(merged[0])["sleep_hours"] == 7.5
    assert len(merged[0]["data"]) == 2 and nap in merged[0]["data"]
    assert changed == ["sleep_analysis"]
    assert merge_metrics(merged, [sleep_metric(nap)])[1] == []


def test_complete_same_session_can_correct_sleep_downward():
    old = sleep_metric(sleep_point(7.5))
    corrected = sleep_metric(sleep_point(6.5))
    merged, changed = merge_metrics([old], [corrected])
    assert merged == [corrected]
    assert sleep_fields(merged[0])["sleep_hours"] == 6.5
    assert changed == ["sleep_analysis"]


def test_missing_known_sleep_stages_cannot_overwrite_complete_night():
    old = sleep_metric(sleep_point(7.5))
    partial = sleep_point(.5)
    del partial["deep"]
    merged, changed = merge_metrics([old], [sleep_metric(partial)])
    assert merged == [old] and changed == []


def test_untimed_sleep_decline_is_preserved_without_session_identity():
    old = sleep_metric(sleep_point(7.5, None))
    merged, changed = merge_metrics([old], [sleep_metric(sleep_point(.5, None))])
    assert merged == [old] and changed == []
    complete = sleep_metric(sleep_point(8, None))
    assert merge_metrics([old], [complete])[0] == [complete]


def raw_sleep(*points):
    return split_by_day({"data": {"metrics": [sleep_metric(*points)]}})["2026-01-05"][0]


def segment(start, end, stage="Core", source="Watch"):
    return {"startDate": start, "endDate": end, "value": stage, "source": source}


def test_raw_sleep_overlapping_sources_are_not_added_together():
    start, end = "2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800"
    metric = raw_sleep(segment(start, end), segment(start, end, "Asleep", "Phone"))
    assert len(metric["data"]) == 2
    assert sleep_fields(metric)["sleep_hours"] == 7
    assert {p["totalSleep"] for p in metric["data"]} == {7}


def test_raw_sleep_same_source_duplicates_and_stage_overlap_count_once():
    start, end = "2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800"
    core = segment(start, end)
    metric = raw_sleep(core, core.copy(), segment(start, end, "Asleep"),
                       segment("2026-01-05 01:00:00 +0800", "2026-01-05 02:00:00 +0800", "Deep"),
                       segment(start, end, "In Bed"))
    point = metric["data"][0]
    assert sleep_fields(metric)["sleep_hours"] == 7
    assert point["core"] == 6 and point["deep"] == 1 and point["asleep"] == 0
    assert point["inBed"] == 7


def test_raw_sleep_awake_overrides_generic_asleep():
    metric = raw_sleep(segment("2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800", "Asleep"),
                       segment("2026-01-05 01:00:00 +0800", "2026-01-05 01:30:00 +0800", "Awake"))
    assert sleep_fields(metric)["sleep_hours"] == 6.5
    assert metric["data"][0]["awake"] == .5


def test_raw_sleep_invalid_intervals_are_skipped_and_mixed_offsets_work():
    start, end = "2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800"
    metric = raw_sleep(segment(start, end), segment("2026-01-04 23:00:00", "2026-01-05 06:00:00", "Asleep"),
                       segment(end, start, "Deep"), segment(end, end, "Deep"))
    assert sleep_fields(metric)["sleep_hours"] == 7
    assert metric["data"][0]["deep"] == 0


def test_raw_sleep_night_and_nap_stay_separate():
    metric = raw_sleep(segment("2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800"),
                       segment("2026-01-05 14:00:00 +0800", "2026-01-05 14:30:00 +0800", "Asleep"))
    assert len(metric["data"]) == 2
    assert sleep_fields(metric)["sleep_hours"] == 7



def test_existing_double_counted_sleep_cache_is_corrected_from_raw_export(tmp_path):
    start, end = "2026-01-04 23:00:00 +0800", "2026-01-05 06:00:00 +0800"
    doubled = sleep_point(14, start, end)
    doubled.update(core=7, deep=0, asleep=7)
    path = tmp_path / "sleep.json"
    merge_into_cache([sleep_metric(doubled)], path)
    corrected = raw_sleep(segment(start, end), segment(start, end, "Asleep", "Phone"))
    assert merge_into_cache([corrected], path) == ["sleep_analysis"]
    import json
    stored = json.loads(path.read_text())["data"]["metrics"][0]
    assert sleep_fields(stored)["sleep_hours"] == 7
    assert merge_into_cache([corrected], path) == []


def test_complete_larger_window_can_correct_sleep_downward():
    old = sleep_metric(sleep_point(7.5))
    corrected = sleep_metric(sleep_point(6.5, "2026-01-04 22:30:00 +0800", "2026-01-05 07:30:00 +0800"))
    assert merge_metrics([old], [corrected])[0] == [corrected]
