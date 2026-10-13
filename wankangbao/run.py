"""一次完整运行：整理手动记录 → 读新导出并合并进缓存 → 重写有新数据的每日笔记 → 周一写周报。"""
import json
import os
import platform
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

from wankangbao import cache_merge, manual_log, metrics, note, report
from wankangbao.cache_merge import read_json, write_json

STALE_HOURS = 24          # 最新导出超过这么久没变，提醒用户手动导出
FINAL_FALLBACK_HOURS = 24  # 过完一天后这么久还没有新导出，也按定稿写
MAX_FAILURES = 2           # 同一份数据连续失败几次后，等新数据或 --date 再试


def _tz(cfg):
    from zoneinfo import ZoneInfo
    if cfg.timezone:
        return ZoneInfo(cfg.timezone)
    try:   # macOS / Linux：/etc/localtime → .../zoneinfo/Asia/Shanghai，带夏令时规则
        return ZoneInfo(os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1])
    except Exception:
        return datetime.now().astimezone().tzinfo


def _end_of_day(day, tz):
    return datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min, tz)


def is_final(day, data_mtime, latest_export, now, tz):
    """过了那天的 24 点之后又来过任何一次导出，或者已经过了 24 点很久，就算定稿。"""
    end = _end_of_day(day, tz)
    return (datetime.fromtimestamp(max(data_mtime, latest_export or 0), tz) >= end
            or now >= end + timedelta(hours=FINAL_FALLBACK_HOURS))


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _request_icloud_download(folder, log):
    """没下载到本地的 iCloud 文件在 macOS 上是 .xxx.json.icloud 占位符，请系统下载，下次运行再读。"""
    pending = list(folder.glob(".*.json.icloud"))
    if not pending:
        return
    log(f"{folder.name}/ 里有 {len(pending)} 个文件还在 iCloud 云端，已请求下载，下次运行再读")
    if platform.system() == "Darwin":
        for p in pending:
            subprocess.run(["brctl", "download", str(p.with_name(p.name[1:-len(".icloud")]))], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ingest(cfg, state, log=print, save=lambda: None, now_ts=None):
    """把新的或 mtime 变过的导出文件合并进缓存。更新 state["days"][日期] = 该日最新数据的 mtime（秒）。"""
    files = state.setdefault("files", {})
    days = state.setdefault("days", {})
    cache = cfg.data_dir / "cache"
    for kind in ("Health", "Workout"):
        folder = cfg.export_dir / kind
        try:
            entries = sorted(folder.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name))
            os.listdir(folder)
        except FileNotFoundError:
            log(f"找不到 {folder}：检查 config.json 的 export_dir 和 Health Auto Export 的自动化")
            continue
        except PermissionError as e:
            log(f"没有权限读取 {folder}（{e}）：定时运行需要给 Python「完全磁盘访问权限」，见 README")
            continue
        _request_icloud_download(folder, log)
        for f in entries:
            try:
                mtime = f.stat().st_mtime
                state["latest_export"] = max(state.get("latest_export", 0), mtime)
                if files.get(str(f)) == mtime:
                    continue
                first_seen = str(f) not in files
                export = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(export, dict) or not isinstance(export.get("data"), dict):
                    raise ValueError("不是 Health Auto Export 的 JSON v2")
                if kind == "Health":
                    for day, ms in cache_merge.split_by_day(export).items():
                        changed = cache_merge.merge_into_cache(ms, cache / "health" / f"{day}.json")
                        if changed or first_seen:
                            days[day] = max(days.get(day, 0), mtime)
                        if changed:
                            log(f"{day} 健康数据更新：{len(changed)} 项")
                else:
                    for day, ws in metrics.split_workouts(export).items():
                        path = cache / "workout" / f"{day}.json"
                        old = {w.get("id") or w.get("start"): w for w in read_json(path, [])}
                        new = {w.get("id") or w.get("start"): w for w in ws}
                        if any(old.get(k) != v for k, v in new.items()):
                            old.update(new)
                            write_json(path, list(old.values()))
                            log(f"{day} 训练记录更新：{len(new)} 条")
                            days[day] = max(days.get(day, 0), mtime)
                        elif first_seen:
                            days[day] = max(days.get(day, 0), mtime)
                files[str(f)] = mtime
                save()
            except (OSError, ValueError) as e:      # 还没下载完 / 写到一半 / 格式不对：跳过，下次再试
                log(f"跳过 {f.name}：{e}")
    latest = state.get("latest_export")
    if latest and (now_ts or datetime.now().timestamp()) - latest > STALE_HOURS * 3600:
        log(f"最新的导出已经超过 {STALE_HOURS} 小时没更新：打开 Health Auto Export 手动导出一次")


def history(cfg, blocks, until, window=35):
    """最近 window 天每天的数字：缓存里能算的就重算，否则用每日笔记 frontmatter；再加上手动记录的体重。"""
    d = date.fromisoformat(until)
    out = {}
    for i in range(window):
        k = (d - timedelta(days=i)).isoformat()
        cache = read_json(cfg.data_dir / "cache" / "health" / f"{k}.json", None)
        row = {}
        if cache:
            ms = cache["data"]["metrics"]
            row = {**metrics.daily_fields(ms), **metrics.extra_fields(ms)}
            row.update(metrics.workout_summary(read_json(cfg.data_dir / "cache" / "workout" / f"{k}.json", []))[0])
        else:
            p = cfg.vault_dir / "每日" / f"{k}.md"
            if p.exists():
                row = note.read_frontmatter(p.read_text(encoding="utf-8"))
        w = manual_log.number(blocks.get(k, ""), "体重")
        if w is not None:
            row["weight"] = w
        if row:
            out[k] = row
    return out


def write_day(cfg, day, data_mtime, final, tz, blocks, dry_run=False, log=print, cache_ttl=None):
    vault = cfg.vault_dir
    as_of_dt = datetime.fromtimestamp(data_mtime, tz)
    status = "定稿" if final else f"进行中 · 截至 {as_of_dt:%H:%M}"
    day_metrics = read_json(cfg.data_dir / "cache" / "health" / f"{day}.json", {"data": {"metrics": []}})["data"]["metrics"]
    fields = metrics.daily_fields(day_metrics)
    w_fields, w_detail = metrics.workout_summary(read_json(cfg.data_dir / "cache" / "workout" / f"{day}.json", []))
    fields.update(w_fields)

    note_path = vault / "每日" / f"{day}.md"
    template_path = vault / "模板" / "每日模板.md"
    existing = note_path.read_text(encoding="utf-8") if note_path.exists() else None
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else None
    read = lambda p: p.read_text(encoding="utf-8") if p.exists() else ""
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    user = report.build_user_message(
        day=day, status=status, as_of=f"{as_of_dt:%m-%d %H:%M}", fields=fields, workouts=w_detail,
        raw_metrics=metrics.compact(day_metrics, metrics.SEND_METRICS | set(cfg.extra_metrics)),
        manual_block=blocks.get(day, ""), prev_manual_block=blocks.get(yesterday, ""),
        user_notes=note.user_notes(existing or ""), profile=read(vault / "个人档案.md"),
        plan=read(vault / "训练计划.md"), history=history(cfg, blocks, day), blocks=blocks)
    ai = None
    if dry_run:
        log(f"===== {day}（{status}）发给 Claude 的材料 =====\n{user}\n")
    else:
        effort = cfg.effort if final else (cfg.effort_in_progress or cfg.effort)
        try:
            ai = report.generate(cfg, report.system_prompt(), user, effort=effort, cache_ttl=cache_ttl, log=log)
        except Exception:
            _atomic_write(note_path, note.render(existing, template, fields, None))   # 数字照样更新，AI 部分保留旧版
            raise
    _atomic_write(note_path, note.render(existing, template, fields, ai))
    log(f"已写 每日/{day}.md（{status}）")
    return status


def write_week(cfg, today, now, tz, state, blocks, dry_run=False, log=print):
    """每周一（上周日定稿后，最晚周一中午）写上周的周报：每周/YYYY-Www.md。已存在就不写。"""
    last_monday = today - timedelta(days=today.weekday() + 7)
    sunday = (last_monday + timedelta(days=6)).isoformat()
    iso = last_monday.isocalendar()
    label = f"{iso[0]}-W{iso[1]:02d}"
    path = cfg.vault_dir / "每周" / f"{label}.md"
    reports = state.get("reports", {})
    week_days = [(last_monday + timedelta(days=i)).isoformat() for i in range(7)]
    if path.exists() or not any(d in reports or (cfg.vault_dir / "每日" / f"{d}.md").exists() for d in week_days):
        return None
    wf = state.setdefault("weekly_failures", {})
    if wf.get(label, 0) >= MAX_FAILURES:
        return None
    if reports.get(sunday, {}).get("status") != "定稿" and now < datetime.combine(today - timedelta(days=today.weekday()), time(12), tz):
        return None     # 等上周日定稿
    read = lambda p: p.read_text(encoding="utf-8") if p.exists() else ""
    user = report.build_weekly_message(
        label=label, monday=last_monday, history=history(cfg, blocks, sunday, window=28),
        statuses={d: reports.get(d, {}).get("status", "没有报告") for d in week_days},
        blocks=blocks, profile=read(cfg.vault_dir / "个人档案.md"), train_days=cfg.train_days)
    charts = report.weekly_charts(history(cfg, blocks, sunday, window=28), last_monday)
    if dry_run:
        log(f"===== 周报 {label} 发给 Claude 的材料 =====\n{user}\n")
        return label
    try:
        text = report.generate(cfg, report.weekly_system_prompt(), user, log=log)
    except report.ReportError:
        wf[label] = wf.get(label, 0) + 1
        raise
    text = text.replace("{{趋势图}}", charts) if "{{趋势图}}" in text else text + "\n\n## 趋势图\n\n" + charts
    _atomic_write(path, text + "\n")
    log(f"已写 每周/{label}.md")
    return label


def _lock(cfg):
    try:
        import fcntl
    except ImportError:          # Windows：不加锁
        return True
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    fh = open(cfg.data_dir / ".lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def _fatal_errors():
    import anthropic
    return (report.NoCredentials, anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.NotFoundError, anthropic.BadRequestError)


def run_once(cfg, today=None, now=None, force_days=(), dry_run=False, log=print):
    import anthropic

    tz = _tz(cfg)
    now = now or datetime.now(tz)
    today = today or now.date().isoformat()
    result = {"written": [], "finalized": [], "weekly": None, "errors": [], "fatal": None}
    lock = _lock(cfg)
    if lock is None:
        log("另一个腕康宝正在运行，这次跳过")
        return result
    state_path = cfg.data_dir / "state.json"
    state = read_json(state_path, {})
    save = lambda: write_json(state_path, state, indent=1)

    manual_path = cfg.vault_dir / "手动记录.md"
    try:
        if manual_log.maintain(manual_path, today, cfg.train_days, cfg.inbody_day, cfg.exercises):
            log("手动记录已整理")
    except Exception as e:          # 整理失败不影响写报告
        log(f"整理手动记录失败（不影响报告）：{e}")
        result["errors"].append(str(e))
    blocks = manual_log.parse_blocks(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else {}

    ingest(cfg, state, log, save, now.timestamp())
    save()

    reports = state.setdefault("reports", {})
    fails = state.setdefault("failures", {})
    latest = state.get("latest_export", 0)
    first = date.fromisoformat(today) - timedelta(days=cfg.lookback_days - 1)
    todo = []
    for d, m in state.get("days", {}).items():
        if not first <= date.fromisoformat(d) <= date.fromisoformat(today):
            continue
        r = reports.get(d, {})
        final = is_final(d, m, latest, now, tz)
        stale = (r.get("data_mtime") != m or not (cfg.vault_dir / "每日" / f"{d}.md").exists()
                 or (final and r.get("status") != "定稿"))
        if stale and r.get("refused_mtime") != m:
            todo.append(d)
    todo = sorted(set(todo) | set(force_days))
    if not todo:
        log("没有要更新的报告")
    cache_ttl = "1h" if len(todo) >= 3 else None
    for d in todo:
        m = state.get("days", {}).get(d)
        if m is None:
            log(f"{d} 没有缓存数据，跳过")
            continue
        f = fails.get(d)
        if f and f.get("data_mtime") == m and f.get("count", 0) >= MAX_FAILURES and d not in force_days:
            log(f"{d} 已连续失败 {f['count']} 次，等新数据或用 --date 重试：{f.get('error')}")
            continue
        final = is_final(d, m, latest, now, tz)
        try:
            status = write_day(cfg, d, m, final, tz, blocks, dry_run, log, cache_ttl)
        except report.Refused as e:
            log(f"{d} {e}")
            if not e.retry_later:
                reports.setdefault(d, {})["refused_mtime"] = m
            save()
            continue
        except _fatal_errors() as e:     # 每天都会一样失败：停下来告诉用户
            result["fatal"] = f"Claude API 出错，本次停止：{type(e).__name__}：{e}"
            log(result["fatal"])
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "type", None) == "billing_error":
                result["fatal"] = f"Claude API 账户余额或账单有问题：{e}"
                log(result["fatal"])
                break
            log(f"{d} Claude API 暂时不可用（{e.status_code}），剩下的下个整点再写")
            break
        except anthropic.APIConnectionError as e:
            log(f"{d} 连不上 Claude API（{e}），下个整点再写")
            break
        except Exception as e:           # 一天失败不影响其他天
            f = fails.setdefault(d, {"data_mtime": m, "count": 0})
            if f.get("data_mtime") != m:
                f.update(data_mtime=m, count=0)
            f["count"] += 1
            f["error"] = str(e)[:300]
            log(f"{d} 写报告失败：{e}")
            result["errors"].append(f"{d}: {e}")
            save()
            continue
        result["written"].append(d)
        if not dry_run:
            fails.pop(d, None)
            if status == "定稿" and reports.get(d, {}).get("status") != "定稿":
                result["finalized"].append(d)
            reports[d] = {"data_mtime": m, "status": status, "generated_at": now.isoformat(timespec="seconds")}
            save()      # effort=max 一份报告要好几分钟，被打断也不用重来
    if not result["fatal"]:
        try:
            result["weekly"] = write_week(cfg, date.fromisoformat(today), now, tz, state, blocks, dry_run, log)
        except Exception as e:
            log(f"周报失败：{e}")
            result["errors"].append(f"周报: {e}")
    save()
    return result
