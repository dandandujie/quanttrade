#!/bin/bash
set -e
cd "$(dirname "$0")"
PROJECT="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.quanttrade.a9markets.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
chmod +x start.command
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.quanttrade.a9markets</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT/start.command</string>
    <string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/quanttrade.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/quanttrade.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
echo "已安装开机自启动：$PLIST"
echo "服务会在登录后自动启动；首次启动可能需要安装依赖，请查看 ~/Library/Logs/quanttrade.log。"
