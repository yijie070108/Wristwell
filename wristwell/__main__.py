"""命令行入口：python start.py（或 python -m wristwell）"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from wristwell import config, report, run

DEMO_TZ = "Asia/Shanghai"
# 示例导出文件的「导出时间」：01-04 那天在 01-05 早上导出过（→ 定稿），01-05 截至 21:30（→ 进行中）
DEMO_MTIMES = {"2026-01-04": "2026-01-05 08:12", "2026-01-05": "2026-01-05 21:30"}


def notify(text):
    """定稿时在 Mac 上弹一个系统通知（其他系统只打印）。文字通过参数传入，不拼进脚本。"""
    print(text)
    if platform.system() == "Darwin":
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "腕康宝"\nend run'
        subprocess.run(["osascript", "-e", script, text], check=False)


def setup_demo(cfg):
    demo = config.ROOT / "workspace"
    vault, data, export = demo / "demo-vault", demo / "demo-data", demo / "demo-export"
    for p in (vault, data, export):
        shutil.rmtree(p, ignore_errors=True)
    shutil.copytree(config.ROOT / "vault-template", vault)
    shutil.copytree(config.ROOT / "examples" / "demo-vault", vault, dirs_exist_ok=True)
    tz = ZoneInfo(DEMO_TZ)
    for kind in ("Health", "Workout"):
        shutil.copytree(config.ROOT / "examples" / kind, export / kind)
        for f in (export / kind).glob("*.json"):
            t = datetime.strptime(DEMO_MTIMES[f.stem[-10:]], "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp()
            os.utime(f, (t, t))
    cfg.export_dir, cfg.vault_dir, cfg.data_dir, cfg.timezone = export, vault, data, DEMO_TZ
    return datetime(2026, 1, 5, 22, 0, tzinfo=tz)


def main(argv=None):
    p = argparse.ArgumentParser(prog="wristwell", description="Apple Watch 数据 → Obsidian 每日 AI 健康报告")
    p.add_argument("--config", help="配置文件路径，默认是仓库根目录的 config.json")
    p.add_argument("--loop", action="store_true", help="一直运行，每个整点过 1 分钟跑一次")
    p.add_argument("--date", action="append", default=[], help="强制重写某天（YYYY-MM-DD），可写多次；--loop 时只在第一次生效")
    p.add_argument("--today", help="把「今天」当成这一天（测试用）")
    p.add_argument("--dry-run", action="store_true", help="不调用 Claude，只填数字并打印发给 Claude 的材料")
    p.add_argument("--demo", action="store_true", help="用 examples/ 里的虚构数据跑一遍，结果在 workspace/demo-vault/")
    a = p.parse_args(argv)
    cfg = config.load(a.config)
    for d in a.date + ([a.today] if a.today else []):
        try:
            date.fromisoformat(d)
        except ValueError:
            p.error(f"日期格式不对：{d}（要 YYYY-MM-DD）")

    now = None
    if a.demo:
        now = setup_demo(cfg)
        a.today, a.loop = "2026-01-05", False
        if not (a.dry_run or report.has_credentials()):
            print("没有找到 Claude 登录信息（ANTHROPIC_API_KEY 或 `ant auth login`），改用 --dry-run：只填数字，不调用 Claude。\n")
            a.dry_run = True
    elif cfg.vault_dir is None:
        sys.exit("还没配置 Obsidian 库：cp config.example.json config.json，填好 vault_dir 和 export_dir；想先看看效果就用 --demo")
    elif not cfg.vault_dir.is_dir():
        sys.exit(f"找不到 Obsidian 库 {cfg.vault_dir}：检查 config.json 的 vault_dir")

    force = list(a.date)

    def once():
        nonlocal force
        result = run.run_once(cfg, today=a.today, now=now, force_days=force, dry_run=a.dry_run)
        force = []
        if cfg.notify:
            for d in result["finalized"]:
                notify(f"{d} 的健康报告已定稿")
            if result["fatal"]:
                notify(result["fatal"][:120])
        return result

    while True:
        try:
            result = once()
        except Exception as e:      # 网络、iCloud 偶尔出错，--loop 时下个整点再试
            if not a.loop:
                raise
            print(f"本次运行失败：{e}")
            result = None
        if not a.loop:
            if result and (result["fatal"] or (a.demo and result["errors"])):
                sys.exit(1)
            return
        nxt = (datetime.now() + timedelta(hours=1)).replace(minute=1, second=0, microsecond=0)
        while datetime.now() < nxt:     # 按墙上时间等：Mac 睡眠醒来后能马上补跑
            time.sleep(min(60, max(1, (nxt - datetime.now()).total_seconds())))


if __name__ == "__main__":
    main()
