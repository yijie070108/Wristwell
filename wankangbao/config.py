"""读取 config.json（不存在就用默认值）。API Key 只从环境变量 / .env 读。"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXERCISES = [
    # 动作, 组数, 次数上限, 加重方式
    ["倒蹬", 3, 12, "加 5–10kg"],
    ["坐姿腿弯举", 3, 12, "加一格"],
    ["器械推胸", 3, 12, "加一格"],
    ["坐姿划船", 4, 12, "加一格"],
    ["单手绳索侧平举（每边）", 3, 20, "加一格"],
    ["卷腹器械（可选）", 2, 15, "加一格"],
]


@dataclass
class Config:
    export_dir: Path                      # Health Auto Export 的 Documents 目录（里面有 Health/ 和 Workout/）
    vault_dir: Path                       # Obsidian 库根目录
    data_dir: Path                        # 缓存和运行状态
    timezone: str = ""                    # 空 = 用系统时区
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    lookback_days: int = 7
    train_days: list = field(default_factory=lambda: [2, 4, 6])   # 周一=0
    inbody_day: int = 0
    exercises: list = field(default_factory=lambda: [list(e) for e in DEFAULT_EXERCISES])


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load(path=None):
    _load_dotenv(ROOT / ".env")
    path = Path(path or os.environ.get("WKB_CONFIG") or ROOT / "config.json")
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    home = Path.home()
    export_dir = raw.pop("export_dir", "") or (
        home / "Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents")
    vault_dir = raw.pop("vault_dir", "") or ROOT / "vault-template"
    data_dir = raw.pop("data_dir", "") or ROOT / "workspace"
    return Config(
        export_dir=Path(export_dir).expanduser(),
        vault_dir=Path(vault_dir).expanduser(),
        data_dir=Path(data_dir).expanduser(),
        **raw,
    )
