"""命令行提醒、退出状态和每小时调度的回归测试，不调用 API 或系统通知。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from wristwell import __main__ as cli


def prepare_cli(tmp_path, monkeypatch, *, notify=True, **updates):
    cfg = SimpleNamespace(vault_dir=tmp_path, notify=notify)
    result = {
        "written": [], "finalized": [], "weekly": None,
        "errors": [], "fatal": None, "alerts": [],
    }
    result.update(updates)
    monkeypatch.setattr(cli.config, "load", lambda *_: cfg)
    monkeypatch.setattr(cli.run, "run_once", lambda *_, **__: result)
    notifications = []
    monkeypatch.setattr(cli, "notify", notifications.append)
    return result, notifications


def test_alerts_notify_and_keep_existing_report_notifications(tmp_path, monkeypatch):
    _, notifications = prepare_cli(
        tmp_path, monkeypatch,
        finalized=["2026-10-13"],
        alerts=["没有收到健康导出：请打开 Health Auto Export 手动导出一次。"],
    )
    cli.main([])
    assert notifications == [
        "2026-10-13 的健康报告已定稿",
        "没有收到健康导出：请打开 Health Auto Export 手动导出一次。",
    ]


def test_notify_false_suppresses_alerts_and_report_notifications(tmp_path, monkeypatch):
    _, notifications = prepare_cli(
        tmp_path, monkeypatch, notify=False,
        finalized=["2026-10-13"], alerts=["请检查 iCloud 导出设置。"],
    )
    cli.main([])
    assert notifications == []


def test_normal_run_errors_exit_nonzero(tmp_path, monkeypatch):
    _, notifications = prepare_cli(
        tmp_path, monkeypatch,
        errors=["没有权限读取健康导出"], alerts=["请给 Python 开启完全磁盘访问权限。"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 1
    assert notifications == ["请给 Python 开启完全磁盘访问权限。"]


def test_fatal_error_still_notifies_and_exits_nonzero(tmp_path, monkeypatch):
    _, notifications = prepare_cli(tmp_path, monkeypatch, fatal="Claude 登录信息无效，请更新 API Key。")
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 1
    assert notifications == ["Claude 登录信息无效，请更新 API Key。"]


@pytest.mark.parametrize(("start", "next_run"), [
    (datetime(2026, 10, 14, 10, 0, 20), datetime(2026, 10, 14, 10, 1)),
    (datetime(2026, 10, 14, 10, 2), datetime(2026, 10, 14, 11, 1)),
    (datetime(2026, 10, 14, 10, 1), datetime(2026, 10, 14, 11, 1)),
])
def test_loop_runs_at_next_available_minute_one(tmp_path, monkeypatch, start, next_run):
    result, _ = prepare_cli(tmp_path, monkeypatch, notify=False)
    clock = [start]
    run_times = []

    class Finished(BaseException):
        pass

    class SimulatedDateTime:
        @staticmethod
        def now():
            return clock[0]

    def simulated_run(*args, **kwargs):
        run_times.append(clock[0])
        if len(run_times) == 2:
            raise Finished()
        return result

    def simulated_sleep(seconds):
        clock[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(cli, "datetime", SimulatedDateTime)
    monkeypatch.setattr(cli.run, "run_once", simulated_run)
    monkeypatch.setattr(cli.time, "sleep", simulated_sleep)
    with pytest.raises(Finished):
        cli.main(["--loop"])
    assert run_times == [start, next_run]


def test_repeat_fatal_is_not_notified_again(monkeypatch, tmp_path):
    from wristwell import __main__ as cli
    from types import SimpleNamespace
    result = {"written": [], "finalized": [], "weekly": None, "errors": [],
              "fatal": "相同的接口故障", "alerts": [], "notify_fatal": False}
    monkeypatch.setattr(cli.config, "load", lambda *_: SimpleNamespace(vault_dir=tmp_path, notify=True))
    monkeypatch.setattr(cli.run, "run_once", lambda *a, **kw: result)
    notices = []
    monkeypatch.setattr(cli, "notify", notices.append)
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 1 and notices == []
