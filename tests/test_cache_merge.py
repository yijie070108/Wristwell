from wankangbao.cache_merge import merge_into_cache, merge_metrics, split_by_day


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
