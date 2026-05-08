# DevQuest Log — Docker 镜像
# TODO: 完整 Dockerfile 将在第 8 步实现

FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 暴露 FastAPI 和 Streamlit 端口
EXPOSE 8000 8501

# 默认启动 FastAPI 后端
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
