import shutil
from pathlib import Path
from types import SimpleNamespace

from wankangbao import config, report, run

ROOT = Path(__file__).resolve().parent.parent


def demo_cfg(tmp_path):
    shutil.copytree(ROOT / "vault-template", tmp_path / "vault")
    cfg = config.load(tmp_path / "missing.json")
    cfg.export_dir, cfg.vault_dir, cfg.data_dir = ROOT / "examples", tmp_path / "vault", tmp_path / "data"
    cfg.timezone = "Asia/Shanghai"
    return cfg


class FakeStream:
    def __init__(self, calls):
        self.calls = calls

    def __call__(self, **kw):
        self.calls.append(kw)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="## 假报告")])


def test_end_to_end_with_fake_claude(tmp_path, monkeypatch):
    calls = []
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=FakeStream(calls))))
    real = report.generate
    monkeypatch.setattr(report, "generate", lambda cfg, s, u: real(cfg, s, u, client=fake))
    cfg = demo_cfg(tmp_path)
    result = run.run_once(cfg, today="2026-01-05", log=lambda *_: None)
    assert result["written"] == ["2026-01-04", "2026-01-05"]
    note = (cfg.vault_dir / "每日/2026-01-05.md").read_text(encoding="utf-8")
    assert "## 假报告" in note and "steps: 6340" in note
    assert calls[0]["model"] == "claude-opus-5-5" and calls[0]["fallbacks"] == "default"
    assert "晚上同事聚餐" in calls[1]["messages"][0]["content"]   # 手动记录进了材料
    assert result["weekly"] == "2026-W01"
    # 第二次运行：数据没变，不再调用
    n = len(calls)
    assert run.run_once(cfg, today="2026-01-05", log=lambda *_: None)["written"] == []
    assert len(calls) == n
