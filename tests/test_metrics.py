import json
from pathlib import Path

from wankangbao.cache_merge import split_by_day
from wankangbao.metrics import daily_fields, split_workouts, workout_summary

EX = Path(__file__).resolve().parent.parent / "examples"


def test_daily_fields_from_example():
    export = json.loads((EX / "Health/HealthAutoExport-2026-01-04.json").read_text(encoding="utf-8"))
    f = daily_fields(split_by_day(export)["2026-01-04"])
    assert f["active_kcal"] == 576 and f["resting_kcal"] == 1740 and f["total_kcal"] == 2316
    assert f["sleep_hours"] == 7.3 and f["deep_sleep_min"] == 66 and f["rem_min"] == 96
    assert f["resting_hr"] == 58 and f["hrv"] == 52 and f["steps"] == 9120


def test_unfinished_workout_is_not_counted():
    export = json.loads((EX / "Workout/HealthAutoExport-2026-01-05.json").read_text(encoding="utf-8"))
    fields, detail = workout_summary(split_workouts(export)["2026-01-05"])
    assert [d["suspicious"] for d in detail] == [False, True]
    assert fields["workout_min"] == 32 and fields["workout_type"] == "户外步行"


def test_multiple_workouts_merge():
    ws = [
        {"name": "传统力量训练", "start": "2026-01-04 18:00:00 +0800", "duration": 3600,
         "activeEnergyBurned": {"qty": 300, "units": "kcal"}, "heartRate": {"avg": {"qty": 120}}},
        {"name": "户外步行", "start": "2026-01-04 08:00:00 +0800", "duration": 1800,
         "activeEnergyBurned": {"qty": 418.4, "units": "kJ"}, "avgHeartRate": {"qty": 90},
         "distance": {"qty": 2.5, "units": "km"}},
    ]
    fields, _ = workout_summary(ws)
    assert fields == {"workout_type": "户外步行, 传统力量训练", "workout_min": 90,
                      "workout_kcal": 400, "workout_avg_hr": 110}
