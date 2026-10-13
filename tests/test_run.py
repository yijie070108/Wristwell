import json
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
