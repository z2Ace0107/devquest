# DevQuest Log

开发者项目经验管理与智能复盘系统 — 自动从 AI 编程对话中提取问题、构建结构化经验库，支持语义搜索、STAR 故事生成和技术成长追踪。

## 架构

```
Streamlit 前端 → FastAPI 后端 → LangChain AI 层 → SQLite + ChromaDB 双存储
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- DeepSeek API Key（LLM）
- 阿里百炼 API Key（Embedding）

### 2. 安装

```bash
cd devquest-log
pip install -r requirements.txt
```

### 3. 配置

创建 `.env` 文件（参考 `.env.example`）：

```env
# DeepSeek (LLM)
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 阿里百炼 (Embedding)
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 4. 启动

**终端 1 — 后端:**
```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

**终端 2 — 前端:**
```bash
streamlit run frontend/app.py
```

浏览器访问 `http://localhost:8501`

### 5. Docker 部署

```bash
docker compose up -d
```

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/extract` | POST | 导入对话，提取问题并入库 |
| `/problems` | GET | 按项目/技术/分数筛选问题 |
| `/star/{id}` | GET | 生成 STAR 面试故事 |
| `/problem/{id}/score` | PUT | 手动更新评分 |
| `/dashboard` | GET | 统计摘要 |
| `/search` | GET | 语义搜索 |
| `/rebuild-index` | POST | 重建向量索引 |
| `/health` | GET | 健康检查 |

## 项目结构

```
devquest-log/
├── backend/
│   ├── app.py              # FastAPI 服务
│   ├── extractor.py        # 问题提取引擎
│   ├── classifier.py       # 技术分类
│   ├── scorer.py           # 优先级评分
│   ├── star_gen.py         # STAR 故事生成
│   ├── vector_search.py    # ChromaDB 向量搜索
│   ├── models.py           # SQLAlchemy 模型
│   └── database.py         # 数据库初始化
├── frontend/
│   └── app.py              # Streamlit 仪表盘
├── sample_conversations/   # 测试用对话样例
├── data/                   # SQLite + ChromaDB 数据目录
├── AGENTS.md               # AI 开发规范
├── PRD.md                  # 产品需求文档
├── Tech Design.md          # 技术设计文档
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
