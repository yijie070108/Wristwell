"""命令行入口：python -m wankangbao 或 python start.py"""
import argparse
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime

from wankangbao import config, run


def notify(text):
    """定稿时在 Mac 上弹一个系统通知（其他系统只打印）。"""
    print(text)
    if platform.system() == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "腕康宝"'], check=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="wankangbao", description="Apple Watch 数据 → Obsidian 每日 AI 健康报告")
    p.add_argument("--config", help="配置文件路径，默认是仓库根目录的 config.json")
    p.add_argument("--loop", action="store_true", help="一直运行，每个整点过 1 分钟跑一次")
    p.add_argument("--date", action="append", default=[], help="强制重写某天（YYYY-MM-DD），可写多次")
    p.add_argument("--today", help="把「今天」当成这一天（测试用）")
    p.add_argument("--dry-run", action="store_true", help="不调用 Claude，只填数字并打印发给 Claude 的材料")
    p.add_argument("--demo", action="store_true", help="用 examples/ 里的虚构数据跑一遍，结果在 workspace/demo-vault/")
    a = p.parse_args(argv)
    cfg = config.load(a.config)
    if a.demo:
        demo = config.ROOT / "workspace"
        shutil.rmtree(demo / "demo-vault", ignore_errors=True)
        shutil.rmtree(demo / "demo-data", ignore_errors=True)
        shutil.copytree(config.ROOT / "vault-template", demo / "demo-vault")
        cfg.export_dir, cfg.vault_dir, cfg.data_dir = config.ROOT / "examples", demo / "demo-vault", demo / "demo-data"
        cfg.timezone, a.today, a.loop = "Asia/Shanghai", "2026-01-05", False
        if not (os.environ.get("ANTHROPIC_API_KEY") or a.dry_run):
            print("没有找到 ANTHROPIC_API_KEY，改用 --dry-run：只填数字，不调用 Claude。\n")
            a.dry_run = True

    def once():
        result = run.run_once(cfg, today=a.today, force_days=a.date, dry_run=a.dry_run)
        for d in result["finalized"]:
            notify(f"{d} 的健康报告已定稿")

    once()
    while a.loop:
        now = datetime.now()
        wait = 3600 - (now.minute * 60 + now.second) + 60
        time.sleep(wait)
        try:
            once()
        except Exception as e:      # 网络、iCloud 偶尔出错，下个整点再试
            print(f"本次运行失败：{e}")


if __name__ == "__main__":
    main()
