"""一次完整运行：整理手动记录 → 读新导出并合并进缓存 → 重写有新数据的每日笔记。"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from wankangbao import cache_merge, manual_log, metrics, note, report


def _tz(cfg):
    if cfg.timezone:
        from zoneinfo import ZoneInfo
        return ZoneInfo(cfg.timezone)
    return datetime.now().astimezone().tzinfo


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def ingest(cfg, state, log=print):
    """把 mtime 变过的导出文件合并进缓存。更新 state["days"][日期] = 该日最新数据的 mtime（秒）。"""
    files = state.setdefault("files", {})
    days = state.setdefault("days", {})
    cache = cfg.data_dir / "cache"
    for kind in ("Health", "Workout"):
        for f in sorted((cfg.export_dir / kind).glob("*.json")):
            mtime = f.stat().st_mtime
            if files.get(str(f)) == mtime:
                continue
            try:
                export = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log(f"跳过 {f.name}：{e}")      # iCloud 还没下载完，下次再试
                continue
            if kind == "Health":
                for day, ms in cache_merge.split_by_day(export).items():
                    changed = cache_merge.merge_into_cache(ms, cache / "health" / f"{day}.json")
                    if changed:
                        days[day] = max(days.get(day, 0), mtime)
                        log(f"{day} 健康数据更新：{', '.join(changed)}")
            else:
                for day, ws in metrics.split_workouts(export).items():
                    path = cache / "workout" / f"{day}.json"
                    old = {w.get("id") or w.get("start"): w for w in _load_json(path, [])}
                    new = {w.get("id") or w.get("start"): w for w in ws}
                    if any(old.get(k) != v for k, v in new.items()):
                        old.update(new)
                        _save_json(path, list(old.values()))
                        days[day] = max(days.get(day, 0), mtime)
                        log(f"{day} 训练记录更新：{len(new)} 条")
            files[str(f)] = mtime


def notes_frontmatter(vault):
    out = {}
    for p in (vault / "每日").glob("????-??-??.md"):
        out[p.stem] = note.read_frontmatter(p.read_text(encoding="utf-8"))
    return out


def write_day(cfg, day, data_mtime, tz, dry_run=False, log=print):
    vault = cfg.vault_dir
    as_of_dt = datetime.fromtimestamp(data_mtime, tz)
    end_of_day = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), tz)
    status = "定稿" if as_of_dt >= end_of_day else f"进行中 · 截至 {as_of_dt:%H:%M}"

    day_metrics = _load_json(cfg.data_dir / "cache" / "health" / f"{day}.json", {"data": {"metrics": []}})["data"]["metrics"]
    fields = metrics.daily_fields(day_metrics)
    w_fields, w_detail = metrics.workout_summary(_load_json(cfg.data_dir / "cache" / "workout" / f"{day}.json", []))
    fields.update(w_fields)

    manual_path = vault / "手动记录.md"
    manual_text = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
    manual_block = manual_log.parse_blocks(manual_text).get(day, "")
    profile_path = vault / "个人档案.md"
    profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    base = report.baseline(notes_frontmatter(vault), day)

    user = report.build_user_message(day, status, f"{as_of_dt:%m-%d %H:%M}", fields, w_detail,
                                     metrics.compact(day_metrics), manual_block, profile, base)
    if dry_run:
        log(f"===== {day}（{status}）发给 Claude 的材料 =====\n{user}\n")
        ai = None
    else:
        ai = report.generate(cfg, report.system_prompt(), user)

    note_path = vault / "每日" / f"{day}.md"
    template_path = vault / "模板" / "每日模板.md"
    existing = note_path.read_text(encoding="utf-8") if note_path.exists() else None
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else None
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(note.render(existing, template, fields, ai), encoding="utf-8")
    log(f"已写 {note_path.relative_to(vault)}（{status}）")
    return status


def write_week(cfg, today, dry_run=False, log=print):
    """每周一（或之后第一次运行）写上周的周报：每周/YYYY-Www.md。已存在就不写。"""
    last_monday = today - timedelta(days=today.weekday() + 7)
    iso = last_monday.isocalendar()
    label = f"{iso[0]}-W{iso[1]:02d}"
    path = cfg.vault_dir / "每周" / f"{label}.md"
    vault = cfg.vault_dir
    week_days = {(last_monday + timedelta(days=i)).isoformat() for i in range(7)}
    if path.exists() or not week_days & set(notes_frontmatter(vault)):
        return None
    manual = vault / "手动记录.md"
    profile = vault / "个人档案.md"
    user = report.build_weekly_message(
        label, last_monday, notes_frontmatter(vault),
        manual.read_text(encoding="utf-8") if manual.exists() else "",
        profile.read_text(encoding="utf-8") if profile.exists() else "")
    if dry_run:
        log(f"===== 周报 {label} 发给 Claude 的材料 =====\n{user}\n")
        return label
    text = report.generate(cfg, report.weekly_system_prompt(), user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    log(f"已写 每周/{label}.md")
    return label


def run_once(cfg, today=None, force_days=(), dry_run=False, log=print):
    tz = _tz(cfg)
    today = today or datetime.now(tz).date().isoformat()
    state_path = cfg.data_dir / "state.json"
    state = _load_json(state_path, {})

    manual_path = cfg.vault_dir / "手动记录.md"
    if manual_log.maintain(manual_path, today, cfg.train_days, cfg.inbody_day, cfg.exercises):
        log("手动记录已整理")

    ingest(cfg, state, log)

    reports = state.setdefault("reports", {})
    first = date.fromisoformat(today) - timedelta(days=cfg.lookback_days - 1)
    todo = [d for d, m in state.get("days", {}).items()
            if first <= date.fromisoformat(d) <= date.fromisoformat(today)
            and (reports.get(d, {}).get("data_mtime") != m
                 or not (cfg.vault_dir / "每日" / f"{d}.md").exists())]
    todo = sorted(set(todo) | set(force_days))
    if not todo:
        log("没有新数据")
    finalized = []
    for d in todo:
        m = state.get("days", {}).get(d)
        if m is None:
            log(f"{d} 没有缓存数据，跳过")
            continue
        try:
            status = write_day(cfg, d, m, tz, dry_run, log)
        except Exception as e:          # 一天失败不影响其他天，下次运行再试
            log(f"{d} 写报告失败：{e}")
            continue
        if not dry_run:
            if status == "定稿" and reports.get(d, {}).get("status") != "定稿":
                finalized.append(d)
            reports[d] = {"data_mtime": m, "status": status,
                          "generated_at": datetime.now(tz).isoformat(timespec="seconds")}
    weekly = None
    try:
        weekly = write_week(cfg, date.fromisoformat(today), dry_run, log)
    except Exception as e:
        log(f"周报失败：{e}")
    _save_json(state_path, state)
    return {"written": todo, "finalized": finalized, "weekly": weekly}
