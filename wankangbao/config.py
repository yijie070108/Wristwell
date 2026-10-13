"""读取 config.json。API Key 只从环境变量 / .env 读。"""
import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from wankangbao.manual_log import DEFAULT_EXERCISES

ROOT = Path(__file__).resolve().parent.parent
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EXPORT = "~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents"


@dataclass
class Config:
    export_dir: Path                      # Health Auto Export 的 Documents 目录（里面有 Health/ 和 Workout/）
    vault_dir: Path | None                # Obsidian 库根目录；不配置就不运行（--demo 除外）
    data_dir: Path                        # 缓存和运行状态
    timezone: str = ""                    # 空 = 用系统时区
    model: str = "claude-opus-5-5"
    effort: str = "max"                   # 思考力度：low / medium / high / xhigh / max
    effort_in_progress: str = ""          # 「进行中」报告单独用的力度；空 = 和 effort 一样
    fallbacks: bool = True                # 安全分类器误拦时，服务端自动换模型重跑
    lookback_days: int = 7
    train_days: list = field(default_factory=lambda: [2, 4, 6])   # 周一=0
    inbody_day: int = 0
    exercises: list = field(default_factory=lambda: [list(e) for e in DEFAULT_EXERCISES])
    extra_metrics: list = field(default_factory=list)              # 额外允许发给 Claude 的指标名
    notify: bool = True                   # 定稿时 Mac 弹通知


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v and not v.startswith("your_"):     # 空值和示例占位符会盖掉 `ant auth login`，跳过
                os.environ.setdefault(k.strip(), v)


def load(path=None):
    _load_dotenv(ROOT / ".env")
    explicit = path or os.environ.get("WKB_CONFIG")
    path = Path(explicit).expanduser() if explicit else ROOT / "config.json"
    if explicit and not path.exists():
        raise SystemExit(f"找不到配置文件 {path}")
    raw = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path} 第 {e.lineno} 行格式不对：{e.msg}")
    known = {f.name for f in dataclasses.fields(Config)}
    unknown = sorted(k for k in raw if k not in known and not k.startswith("_"))
    if unknown:
        print(f"config.json 里这些字段不认识，已忽略：{', '.join(unknown)}")
    raw = {k: v for k, v in raw.items() if k in known}

    def _path(key, default):
        v = raw.pop(key, "") or default
        if v is None:
            return None
        p = Path(v).expanduser()
        return p if p.is_absolute() else (path.parent / p).resolve()

    cfg = Config(
        export_dir=_path("export_dir", DEFAULT_EXPORT),
        vault_dir=_path("vault_dir", None),
        data_dir=_path("data_dir", ROOT / "workspace"),
        **raw,
    )
    for name in ("effort", "effort_in_progress"):
        v = getattr(cfg, name)
        if v and v not in EFFORTS:
            raise SystemExit(f"{name} 只能是 {' / '.join(EFFORTS)}，现在是 {v!r}")
    return cfg
