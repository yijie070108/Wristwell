import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def make_cfg(tmp_path):
    """写一个临时 config.json 并加载。"""
    def _make(**kw):
        from wankangbao import config
        kw.setdefault("vault_dir", str(tmp_path / "vault"))
        kw.setdefault("data_dir", str(tmp_path / "data"))
        kw.setdefault("export_dir", str(tmp_path / "export"))
        p = tmp_path / "config.json"
        p.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
        return config.load(p)
    return _make
