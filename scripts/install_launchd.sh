#!/bin/bash
# 装 macOS 定时任务：每个整点过 1 分钟运行一次腕康宝。
# 用法：./scripts/install_launchd.sh        卸载：./scripts/install_launchd.sh --uninstall
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL="com.wristwell.hourly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已卸载"
  exit 0
fi

PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || { echo "找不到 $PY：先运行 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[[ -f "$REPO/config.json" ]] || { echo "还没有 config.json：先 cp config.example.json config.json 并填好 vault_dir"; exit 1; }
mkdir -p "$REPO/workspace" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$REPO/start.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>1</integer></dict>
  <key>StandardOutPath</key><string>$REPO/workspace/launchd.log</string>
  <key>StandardErrorPath</key><string>$REPO/workspace/launchd.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装：每个整点过 1 分钟运行一次，日志在 $REPO/workspace/launchd.log"
echo
echo "还差一步：给 Python 开「完全磁盘访问权限」，不然读不了 iCloud 里的文件。"
echo "  1. 系统设置 → 隐私与安全性 → 完全磁盘访问权限 → ＋"
echo "  2. 按 ⌘⇧G，粘贴下面这个路径："
echo "     $(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$PY")"
