#!/usr/bin/env bash
# 掼蛋独立版 · 本地开发启动（后端 8002 + 前端 dev 3012）
set -e
cd "$(dirname "$0")"

# set -m：让后台 job 各自成为独立进程组（PID==PGID），退出时杀整组，
# 连带 uvicorn/vite fork 的子进程一起带走，避免孤儿/僵尸进程。
set -m

# ⚠️ Hermes 终端会注入 PYTHONPATH 污染 venv，必须 env -u PYTHONPATH
echo "[1/2] 启动后端 FastAPI :8002 ..."
env -u PYTHONPATH ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002 &
BACKEND_PID=$!

echo "[2/2] 启动前端 Vite dev :3012 ..."
cd ui
node node_modules/vite/bin/vite.js --port 3012 --host &
FRONTEND_PID=$!

# 退出清理：杀整个进程组（负 PID），先 TERM 优雅退出、再 KILL 兜底，
# 最后 wait 回收，确保子进程不残留。
cleanup() {
  for grp in "$BACKEND_PID" "$FRONTEND_PID"; do
    kill -TERM -- -"$grp" 2>/dev/null || true
  done
  sleep 1
  for grp in "$BACKEND_PID" "$FRONTEND_PID"; do
    kill -KILL -- -"$grp" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo ""
echo "  访问: http://127.0.0.1:3012"
echo "  后端: http://127.0.0.1:8002/docs"
echo "  Ctrl+C 停止"
wait
