#!/bin/bash
# DevQuest Log — Docker 容器入口
# 同时启动 FastAPI 后端和 Streamlit 前端

set -e

echo "Starting DevQuest Log..."

# 启动 FastAPI 后端
uvicorn backend.app:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"

# 等待后端就绪
sleep 3

# 启动 Streamlit 前端
streamlit run frontend/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.serverAddress localhost &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"

# 等待任一进程退出
wait -n $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
echo "Process exited, shutting down..."
kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
wait
