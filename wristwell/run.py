"""一次完整运行：整理手动记录 → 读新导出并合并进缓存 → 重写有新数据的每日笔记 → 周一写周报。"""
import hashlib
import json
import os
import platform
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

from wristwell import cache_merge, manual_log, metrics, note, report
from wristwell.cache_merge import read_json, write_json

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
    """合并有效导出；返回需要用户处理的读取/数据故障。文件内容变了也要重新合并。"""
    files = state.setdefault("files", {})
    days = state.setdefault("days", {})
    cache = cfg.data_dir / "cache"
    issues = {}
    for kind in ("Health", "Workout"):
        folder = cfg.export_dir / kind
        try:
            entries = sorted(folder.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name))
            os.listdir(folder)
        except FileNotFoundError:
            msg = f"找不到 {folder}：请检查 config.json 的 export_dir，并在 Health Auto Export 导出 Health 和 Workout"
            log(msg)
            issues[f"export:{kind}"] = msg
            continue
        except OSError as e:
            msg = f"无法读取 {folder}（{e}）：请检查目录；权限被拒时给 Python 开启「完全磁盘访问权限」"
            log(msg)
            issues[f"export:{kind}"] = msg
            continue
        _request_icloud_download(folder, log)
        for f in entries:
            try:
                stat = f.stat()
                content = f.read_bytes()
                version = {"mtime_ns": stat.st_mtime_ns, "size": len(content),
                           "sha256": hashlib.sha256(content).hexdigest()}
                if files.get(str(f)) == version:
                    continue
                first_seen = str(f) not in files
                export = json.loads(content)
                data = export.get("data") if isinstance(export, dict) else None
                key = "metrics" if kind == "Health" else "workouts"
                if not isinstance(data, dict) or not isinstance(data.get(key), list):
                    raise ValueError(f"不是 Health Auto Export 的 JSON v2 {kind} 导出")
                mtime = stat.st_mtime
                day_exports = cache_merge.split_by_day(export) if kind == "Health" else metrics.split_workouts(export)
                if not day_exports:
                    files[str(f)] = version
                    save()
                    continue   # 空壳导出没有实际日期/数据，不能推进定稿时间。
                if kind == "Health":
                    for day, ms in day_exports.items():
                        changed = cache_merge.merge_into_cache(ms, cache / "health" / f"{day}.json")
                        if changed or first_seen:
                            days[day] = max(days.get(day, 0), mtime)
                        if changed:
                            log(f"{day} 健康数据更新：{len(changed)} 项")
                else:
                    for day, ws in day_exports.items():
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
                # 格式正确且成功合并后才刷新有效导出时间；坏文件不能让旧日报提前定稿。
                state["latest_export"] = max(state.get("latest_export", 0), mtime)
                files[str(f)] = version
                save()
            except (OSError, ValueError) as e:
                log(f"跳过 {f.name}：{e}")
    latest = state.get("latest_export")
    if not issues and not days:
        issues["no-data"] = "尚未收到可用健康或训练数据：请在 Health Auto Export 手动导出一次，并检查 Health/Workout 的 JSON v2 文件是否已到 Mac"
    elif latest and (now_ts or datetime.now().timestamp()) - latest > STALE_HOURS * 3600:
        issues["stale-data"] = f"有效导出已超过 {STALE_HOURS} 小时没更新：请打开 Health Auto Export 手动导出，并检查 iCloud 同步"
    for key, msg in issues.items():
        if not key.startswith("export:"):
            log(msg)
    return issues


def _sync_alerts(state, issues, result):
    """同一故障持续时只提醒一次；恢复后再次发生可以再提醒。"""
    previous = state.get("active_alerts", {})
    result["alerts"] = [msg for key, msg in sorted(issues.items())
                        if key != "api-fatal" and previous.get(key) != msg]
    result["notify_fatal"] = bool(result["fatal"] and previous.get("api-fatal") != result["fatal"])
    state["active_alerts"] = issues


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
        else:
            p = cfg.vault_dir / "每日" / f"{k}.md"
            if p.exists():
                row = note.read_frontmatter(p.read_text(encoding="utf-8"))
        row.update(metrics.workout_summary(read_json(cfg.data_dir / "cache" / "workout" / f"{k}.json", []))[0])
        w = manual_log.number(blocks.get(k, ""), "体重")
        if w is not None:
            row["weight"] = w
        if row:
            out[k] = row
    return out


def prepare_day(cfg, day, data_mtime, final, tz, blocks):
    """使用实际发给 AI 的材料判断是否更新，涵盖手填记录、备注、历史和迟到训练。"""
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
    system = report.system_prompt()
    effort = cfg.effort if final else (cfg.effort_in_progress or cfg.effort)
    signature = hashlib.sha256(json.dumps(
        {"system": system, "user": user, "model": cfg.model, "effort": effort, "fallbacks": cfg.fallbacks},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"status": status, "fields": fields, "path": note_path, "template": template,
            "user": user, "system": system, "effort": effort, "signature": signature}


def write_day(cfg, day, data_mtime, final, tz, blocks, dry_run=False, log=print, cache_ttl=None, prepared=None):
    inputs = prepared or prepare_day(cfg, day, data_mtime, final, tz, blocks)
    path = inputs["path"]

    def write(ai):
        # AI 生成可能耗时几分钟。写回前重读笔记，保留期间用户新增的备注和字段。
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        _atomic_write(path, note.render(existing, inputs["template"], inputs["fields"], ai))

    ai = None
    if dry_run:
        log(f"===== {day}（{inputs['status']}）发给 Claude 的材料 =====\n{inputs['user']}\n")
    else:
        try:
            ai = report.generate(cfg, inputs["system"], inputs["user"], effort=inputs["effort"],
                                 cache_ttl=cache_ttl, log=log)
        except Exception:
            write(None)   # 数字照样更新，保留旧 AI 报告和最新的用户内容。
            raise
    write(ai)
    log(f"已写 每日/{day}.md（{inputs['status']}）")
    return inputs["status"]


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
    result = {"written": [], "finalized": [], "weekly": None, "errors": [], "fatal": None, "alerts": []}
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

    issues = ingest(cfg, state, log, save, now.timestamp())
    result["errors"].extend(msg for key, msg in issues.items() if key.startswith("export:"))
    save()

    reports = state.setdefault("reports", {})
    fails = state.setdefault("failures", {})
    latest = state.get("latest_export", 0)
    first = date.fromisoformat(today) - timedelta(days=cfg.lookback_days - 1)
    todo, prepared_days = [], {}
    for d, m in state.get("days", {}).items():
        if not first <= date.fromisoformat(d) <= date.fromisoformat(today):
            continue
        r = reports.get(d, {})
        final = is_final(d, m, latest, now, tz)
        inputs = prepare_day(cfg, d, m, final, tz, blocks)
        prepared_days[d] = inputs
        stale = (r.get("input_signature") != inputs["signature"]
                 or not (cfg.vault_dir / "每日" / f"{d}.md").exists())
        if stale:
            if r.get("refused_signature") == inputs["signature"]:
                issues[f"refused:{d}"] = f"{d} 的报告被 Claude 拒绝：请检查记录或用 --date 重试；旧报告已保留"
            else:
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
        final = is_final(d, m, latest, now, tz)
        inputs = prepared_days.get(d) or prepare_day(cfg, d, m, final, tz, blocks)
        signature = inputs["signature"]
        f = fails.get(d)
        if f and f.get("input_signature") == signature and f.get("count", 0) >= MAX_FAILURES and d not in force_days:
            log(f"{d} 已连续失败 {f['count']} 次，等新数据或用 --date 重试：{f.get('error')}")
            issues[f"report:{d}"] = f"{d} 的报告已连续失败 {MAX_FAILURES} 次：请查看日志，修正记录或用 --date 重试"
            continue
        try:
            status = write_day(cfg, d, m, final, tz, blocks, dry_run, log, cache_ttl, prepared=inputs)
        except report.Refused as e:
            log(f"{d} {e}")
            result["errors"].append(f"{d}: {e}")
            issues[f"refused:{d}"] = f"{d} 的报告被 Claude 拒绝：请检查记录或用 --date 重试；旧报告已保留"
            if not e.retry_later:
                reports.setdefault(d, {})["refused_signature"] = signature
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
            msg = f"{d} Claude API 暂时不可用（{e.status_code}），剩下的下个整点再写"
            log(msg)
            result["errors"].append(msg)
            issues["api-unavailable"] = f"Claude 接口暂时不可用（{e.status_code}），下个整点自动重试；旧报告已保留"
            break
        except anthropic.APIConnectionError as e:
            msg = f"{d} 连不上 Claude API（{e}），下个整点再写"
            log(msg)
            result["errors"].append(msg)
            issues["api-connect"] = "连不上 Claude 接口：请检查 Mac 网络，下个整点自动重试；旧报告已保留"
            break
        except Exception as e:           # 一天失败不影响其他天
            f = fails.setdefault(d, {"input_signature": signature, "count": 0})
            if f.get("input_signature") != signature:
                f.update(input_signature=signature, count=0)
            f["count"] += 1
            f["error"] = str(e)[:300]
            log(f"{d} 写报告失败：{e}")
            result["errors"].append(f"{d}: {e}")
            if f["count"] >= MAX_FAILURES:
                issues[f"report:{d}"] = f"{d} 的报告已连续失败 {MAX_FAILURES} 次：请查看日志，修正记录或用 --date 重试"
            save()
            continue
        result["written"].append(d)
        if not dry_run:
            fails.pop(d, None)
            if status == "定稿" and reports.get(d, {}).get("status") != "定稿":
                result["finalized"].append(d)
            reports[d] = {"data_mtime": m, "input_signature": signature, "status": status,
                          "generated_at": now.isoformat(timespec="seconds")}
            save()      # effort=max 一份报告要好几分钟，被打断也不用重来
    if not result["fatal"]:
        try:
            result["weekly"] = write_week(cfg, date.fromisoformat(today), now, tz, state, blocks, dry_run, log)
        except Exception as e:
            log(f"周报失败：{e}")
            result["errors"].append(f"周报: {e}")
    if result["fatal"]:
        issues["api-fatal"] = result["fatal"]
    _sync_alerts(state, issues, result)
    save()
    return result
