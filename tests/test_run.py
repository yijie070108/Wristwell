import json

import pytest
import os
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from wristwell import __main__ as cli
from wristwell import report, run

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Shanghai")


class FakeStream:
    def __init__(self, calls, stop="end_turn"):
        self.calls, self.stop = calls, stop

    def __call__(self, **kw):
        self.calls.append(kw)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        text = "## 假报告\n\n## 四、趋势图\n{{趋势图}}" if "周报" in self.calls[-1]["messages"][0]["content"][:10] else "## 假报告"
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))


def fake_client(calls, stop="end_turn"):
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=FakeStream(calls, stop))))


def setup(tmp_path, make_cfg, monkeypatch, calls, stop="end_turn"):
    vault = tmp_path / "vault"
    shutil.copytree(ROOT / "vault-template", vault)
    shutil.copytree(ROOT / "examples" / "demo-vault", vault, dirs_exist_ok=True)
    for kind in ("Health", "Workout"):
        shutil.copytree(ROOT / "examples" / kind, tmp_path / "export" / kind)
        for f in (tmp_path / "export" / kind).glob("*.json"):
            t = datetime(2026, 1, 5, 21, 30, tzinfo=TZ).timestamp() if "01-05" in f.name else datetime(2026, 1, 5, 8, 12, tzinfo=TZ).timestamp()
            os.utime(f, (t, t))
    real = report.generate
    client = fake_client(calls, stop)
    monkeypatch.setattr(report, "generate", lambda cfg, s, u, **kw: real(cfg, s, u, client=client, **{k: v for k, v in kw.items() if k != "client"}))
    return make_cfg(timezone="Asia/Shanghai")


def test_end_to_end(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    now = datetime(2026, 1, 5, 22, 0, tzinfo=TZ)
    result = run.run_once(cfg, today="2026-01-05", now=now, log=lambda *_: None)
    assert result["written"] == ["2026-01-04", "2026-01-05"] and not result["errors"]
    assert result["finalized"] == ["2026-01-04"]                      # 01-05 早上有过导出 → 定稿
    state = json.loads((cfg.data_dir / "state.json").read_text())
    assert state["reports"]["2026-01-05"]["status"] == "进行中 · 截至 21:30"
    note = (cfg.vault_dir / "每日/2026-01-05.md").read_text(encoding="utf-8")
    assert "## 假报告" in note and "steps: 6340" in note
    assert calls[0]["model"] == "claude-opus-5-5" and calls[0]["output_config"]["effort"] == "max"
    material = calls[1]["messages"][0]["content"]
    assert "晚上同事聚餐" in material                                  # 今天的手动记录
    assert "下午一杯珍珠奶茶" in material                              # 昨天的手动记录（解释今天早上的睡眠）
    assert "近 7 天均值" in material and "| 指标 | 天数 | 中位数" in material
    assert result["weekly"] == "2026-W01"
    weekly = (cfg.vault_dir / "每周/2026-W01.md").read_text(encoding="utf-8")
    assert "{{趋势图}}" not in weekly
    # 第二次运行：数据没变，不再调用
    n = len(calls)
    assert run.run_once(cfg, today="2026-01-05", now=now, log=lambda *_: None)["written"] == []
    assert len(calls) == n
    # 第二天早上来了新导出：01-05 变成定稿并重写
    later = datetime(2026, 1, 6, 9, 0, tzinfo=TZ)
    f = tmp_path / "export/Health/HealthAutoExport-2026-01-05.json"
    os.utime(f, (later.timestamp(), later.timestamp()))
    r = run.run_once(cfg, today="2026-01-06", now=later, log=lambda *_: None)
    assert r["finalized"] == ["2026-01-05"]


def test_truncated_reports_keep_numbers_and_back_off(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls, stop="max_tokens")
    now = datetime(2026, 1, 5, 22, 0, tzinfo=TZ)
    r = run.run_once(cfg, today="2026-01-05", now=now, log=lambda *_: None)
    assert r["written"] == [] and len(r["errors"]) == 3   # 两天 + 周报
    assert "steps: 6340" in (cfg.vault_dir / "每日/2026-01-05.md").read_text(encoding="utf-8")   # 数字照样写了
    run.run_once(cfg, today="2026-01-05", now=now, log=lambda *_: None)
    n = len(calls)
    run.run_once(cfg, today="2026-01-05", now=now, log=lambda *_: None)   # 连续失败 2 次后不再重试
    assert len(calls) == n


def test_bad_export_files_are_skipped(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    (tmp_path / "export/Health/HealthAutoExport-2026-01-06.json").write_bytes('{"data": {"metrics": [{"name": "示'.encode()[:-1])
    (tmp_path / "export/Health/HealthAutoExport-2026-02.json").write_text('{"data": null}')
    r = run.run_once(cfg, today="2026-01-05", now=datetime(2026, 1, 5, 22, 0, tzinfo=TZ), log=lambda *_: None)
    assert r["written"] == ["2026-01-04", "2026-01-05"]


def test_demo_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(report, "has_credentials", lambda: False)
    cli.main(["--demo"])
    out = capsys.readouterr().out
    assert "2026-01-04（定稿）" in out and "2026-01-05（进行中 · 截至 21:30）" in out
    assert (ROOT / "workspace/demo-vault/每日/2026-01-05.md").exists()


@pytest.mark.parametrize("change", ["manual", "previous_manual", "notes", "profile", "plan"])
def test_changed_report_inputs_update_without_new_export(tmp_path, make_cfg, monkeypatch, change):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    run.run_once(cfg, now=now, log=lambda *_: None)
    note_path = cfg.vault_dir / "每日/2026-01-05.md"
    marker = "新增记录：今天需要重新分析"
    if change in ("manual", "previous_manual"):
        path = cfg.vault_dir / "手动记录.md"
        day = "2026-01-05" if change == "manual" else "2026-01-04"
        text = path.read_text()
        blocks = run.manual_log.parse_blocks(text)
        text = text.replace(blocks[day], blocks[day] + "\n- 多吃:: " + marker + "\n")
    elif change == "notes":
        path = note_path
        text = path.read_text().replace("## 备注", "## 备注\n" + marker)
    else:
        path = cfg.vault_dir / ("个人档案.md" if change == "profile" else "训练计划.md")
        text = path.read_text() + "\n" + marker
    path.write_text(text)
    before = len(calls)
    updated = run.run_once(cfg, now=now, log=lambda *_: None)
    expected_day = "2026-01-04" if change == "plan" else "2026-01-05"
    assert expected_day in updated["written"]
    assert any(marker in c["messages"][0]["content"] for c in calls[before:])
    assert marker in path.read_text()   # 更新不删除用户输入。
    before = len(calls)
    assert run.run_once(cfg, now=now, log=lambda *_: None)["written"] == []
    assert len(calls) == before        # 内容未变不反复花钱。


def test_late_workout_with_older_file_time_updates_report(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    late = cfg.export_dir / "Workout/HealthAutoExport-2026-01-05.json"
    data = late.read_text()
    late.unlink()
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    run.run_once(cfg, now=now, log=lambda *_: None)
    path = cfg.vault_dir / "每日/2026-01-05.md"
    assert "workout_min: 32" not in path.read_text()
    late.write_text(data)
    stamp = datetime(2026, 1, 5, 19, tzinfo=TZ).timestamp()
    os.utime(late, (stamp, stamp))
    before = len(calls)
    assert "2026-01-05" in run.run_once(cfg, now=now, log=lambda *_: None)["written"]
    assert "workout_min: 32" in path.read_text()
    assert len(calls) > before


def test_workout_content_change_with_same_file_metadata_is_ingested(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    run.run_once(cfg, now=now, log=lambda *_: None)
    f = cfg.export_dir / "Workout/HealthAutoExport-2026-01-05.json"
    stat = f.stat()
    data = json.loads(f.read_text())
    w = data["data"]["workouts"][0]
    old = w["duration"]
    text = f.read_text().replace(str(old), str(old + 60), 1)
    assert len(text.encode()) == stat.st_size
    f.write_text(text)
    os.utime(f, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert "2026-01-05" in run.run_once(cfg, now=now, log=lambda *_: None)["written"]
    cached = json.loads((cfg.data_dir / "cache/workout/2026-01-05.json").read_text())
    assert cached[0]["duration"] == old + 60


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_manual_correction_unlocks_failed_or_refused_report(tmp_path, make_cfg, monkeypatch, stop):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls, stop=stop)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    run.run_once(cfg, now=now, log=lambda *_: None)
    run.run_once(cfg, now=now, log=lambda *_: None)
    path = cfg.vault_dir / "每日/2026-01-05.md"
    path.write_text(path.read_text().replace("## 备注", "## 备注\n已补充准确记录"))
    monkeypatch.setattr(report, "generate", lambda *a, **kw: "## 成功报告")
    assert "2026-01-05" in run.run_once(cfg, now=now, log=lambda *_: None)["written"]
    assert "成功报告" in path.read_text() and "已补充准确记录" in path.read_text()


def test_notes_added_during_generation_are_preserved_and_reanalyzed(tmp_path, make_cfg, monkeypatch):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    path = cfg.vault_dir / "每日/2026-01-05.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(run.note.render(None, None, {}, "旧报告"))
    marker = "生成期间补写的备注"
    def generating(*args, **kwargs):
        path.write_text(path.read_text().replace("## 备注", "## 备注\n" + marker).replace("---\n", "---\nmood: 用户字段\n", 1))
        return "## 新报告"
    monkeypatch.setattr(report, "generate", generating)
    run.run_once(cfg, now=now, log=lambda *_: None)
    assert marker in path.read_text() and "mood: 用户字段" in path.read_text()
    monkeypatch.setattr(report, "generate", lambda cfg, sys, user, **kw: "## 已读新备注" if marker in user else "## 其他日")
    assert "2026-01-05" in run.run_once(cfg, now=now, log=lambda *_: None)["written"]
    assert "已读新备注" in path.read_text()


def test_missing_export_alert_is_deduplicated_and_rearms_after_recovery(tmp_path, make_cfg):
    cfg = make_cfg(timezone="Asia/Shanghai")
    cfg.vault_dir.mkdir()
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    first = run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)
    assert first["errors"] and first["alerts"]
    assert run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)["alerts"] == []
    for kind in ("Health", "Workout"):
        (cfg.export_dir / kind).mkdir(parents=True)
    run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)
    (cfg.export_dir / "Health").rmdir()
    assert run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)["alerts"]


def test_empty_export_alert_and_stale_alert_do_not_repeat(tmp_path, make_cfg):
    cfg = make_cfg(timezone="Asia/Shanghai")
    cfg.vault_dir.mkdir()
    for kind in ("Health", "Workout"):
        (cfg.export_dir / kind).mkdir(parents=True)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    assert "尚未收到" in run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)["alerts"][0]
    assert run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)["alerts"] == []
    f = cfg.export_dir / "Health/data.json"
    f.write_text(json.dumps({"data": {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": 1000, "date": "2026-01-04 00:00:00 +0800"}]}]}}))
    stamp = datetime(2026, 1, 4, 12, tzinfo=TZ).timestamp()
    os.utime(f, (stamp, stamp))
    r = run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)
    assert any("24 小时" in msg for msg in r["alerts"])
    assert run.run_once(cfg, now=now, dry_run=True, log=lambda *_: None)["alerts"] == []


@pytest.mark.parametrize("bad", ['{"data":', '{"data": {}}', '{"data": {"metrics": []}}'])
def test_bad_or_empty_export_does_not_advance_finalization(tmp_path, make_cfg, monkeypatch, bad):
    calls = []
    cfg = setup(tmp_path, make_cfg, monkeypatch, calls)
    now = datetime(2026, 1, 5, 22, tzinfo=TZ)
    run.run_once(cfg, now=now, log=lambda *_: None)
    previous = json.loads((cfg.data_dir / "state.json").read_text())["latest_export"]
    f = cfg.export_dir / "Health/new.json"
    f.write_text(bad)
    stamp = datetime(2026, 1, 6, 9, tzinfo=TZ).timestamp()
    os.utime(f, (stamp, stamp))
    r = run.run_once(cfg, now=datetime(2026, 1, 6, 10, tzinfo=TZ), log=lambda *_: None)
    assert "2026-01-05" not in r["finalized"]
    state = json.loads((cfg.data_dir / "state.json").read_text())
    assert state["latest_export"] == previous


def test_workout_only_day_is_present_in_history(tmp_path, make_cfg):
    cfg = make_cfg()
    cfg.vault_dir.mkdir()
    run.write_json(cfg.data_dir / "cache/workout/2026-01-05.json", [{"name": "Outdoor Cycle", "start": "2026-01-05 18:00:00 +0800", "duration": 3600, "distance": {"qty": 20, "units": "km"}}])
    assert run.history(cfg, {}, "2026-01-05")["2026-01-05"]["workout_min"] == 60
