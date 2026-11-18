#!/bin/bash
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Python 3.10+：https://www.python.org/downloads/macos/"
  read -r -p "按回车退出..."
  exit 1
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "当前 Python 版本过低：$("$PY" -V 2>&1)"
  echo "请安装 Python 3.10+（推荐 3.11 或 3.12）。"
  read -r -p "按回车退出..."
  exit 1
fi

# 已在运行就直接打开页面，不重复占端口。
if curl -fsS --max-time 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  open "http://127.0.0.1:8000" >/dev/null 2>&1 || true
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "首次运行：创建 Python 虚拟环境..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  echo "安装依赖（首次约 2—5 分钟）..."
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "启动 A9 Markets：http://127.0.0.1:8000"
( sleep 2; open "http://127.0.0.1:8000" >/dev/null 2>&1 || true ) &
exec env PYTHONUNBUFFERED=1 .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
