# DevQuest Log — Docker 镜像

FROM python:3.10-slim

WORKDIR /app

# 环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data /app/data/chroma_db

# 暴露 FastAPI (8000) 和 Streamlit (8501)
EXPOSE 8000 8501

# 启动脚本
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
