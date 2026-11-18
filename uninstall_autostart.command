#!/bin/bash
set -e
PLIST="$HOME/Library/LaunchAgents/com.quanttrade.a9markets.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已移除开机自启动。"
