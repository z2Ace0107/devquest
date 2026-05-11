# DevQuest Log — 项目交接文档

## 项目概述

开发者项目经验管理与智能复盘系统。从 AI 编程对话中自动提取技术问题，构建结构化经验库，支持语义搜索、STAR 故事生成。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Streamlit 1.41 |
| 后端 | FastAPI + Uvicorn |
| AI | LangChain + DeepSeek (deepseek-v4-flash) |
| 结构化存储 | SQLite + SQLAlchemy |
| 向量存储 | ChromaDB |
| 向量化 | 阿里百炼 text-embedding-v3 (1024维) |
| 关键词检索 | SQLite FTS5 |
| 部署 | Docker / docker-compose |

## 项目结构

```
backend/
├── app.py              # FastAPI 服务 (7 API + CORS + 异常处理)
├── database.py         # 数据库初始化 (含 FTS5 建表)
├── models.py           # ORM 模型 (Project / Problem)
├── extractor.py        # LLM 问题提取 (LangChain + DeepSeek)
├── classifier.py       # 技术标签分类 (LLM + 规则兜底)
├── scorer.py           # 四维度评分 (复杂度/曲折度/影响力/技术广度)
├── star_gen.py         # STAR 面试故事生成
├── vector_search.py    # 双通道检索 (ChromaDB + FTS5 → RRF 融合)
frontend/
├── app.py              # Streamlit 仪表盘 (4 页面)
sample_conversations/
├── mock_web_app_debug.txt  # 测试用模拟对话 (4 个技术问题)
```

## 启动方式

```bash
# 后端
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload

# 前端
streamlit run frontend/app.py

# Docker
docker compose up -d
```

## 环境变量 (.env)

```
DEEPSEEK_API_KEY=sk-xxx        # DeepSeek LLM
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
EMBEDDING_API_KEY=sk-xxx       # 阿里百炼 Embedding
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/extract` | POST | 对话提取 → 分类 → 评分 → 双通道索引 |
| `/problems` | GET | 按 项目/技术/分数/类型 筛选 |
| `/star/{id}` | GET | STAR 故事生成 (带缓存) |
| `/problem/{id}/score` | PUT | 手动修改评分 |
| `/dashboard` | GET | 统计摘要 |
| `/search` | GET | 双通道混合搜索 (含 _debug) |
| `/rebuild-index` | POST | 全量重建索引 |
| `/health` | GET | 健康检查 |

## 核心架构决策

1. **双通道混合检索**: ChromaDB 向量 + SQLite FTS5 关键词 → RRF 融合
2. **/extract 流水线容错**: 每步独立 try/except，分类/评分/索引失败不影响入库
3. **FTS5 关键词通道**: 英文走 FTS5 BM25，中文降级 SQL LIKE (Windows 无 ICU 分词器)
4. **altair 锁定 5.2.0**: Python 3.10 兼容，图表用 plotly 兜底
5. **ChromaDB 1.5.x**: `$contains` 不可用，技术栈过滤在 Python 侧实现

## 已知限制

1. FTS5 中文分词：需 Linux + ICU tokenizer 支持，当前 Windows 中文走 LIKE 兜底
2. ChromaDB `where` 不支持 AND 组合：project 过滤和 tech 过滤不能同时走 DB 层
3. Altair 5.2.0 + Python 3.14 有兼容风险，已用 plotly 兜底

## 测试方法

```bash
# 注入测试数据
python -c "import requests; r=requests.post('http://localhost:8000/extract', json={'project_name':'电商后台管理系统','conversation_text':open('sample_conversations/mock_web_app_debug.txt','r',encoding='utf-8').read()}, timeout=120); print(r.json()['count'], 'problems')"

# 验证各端点
curl http://localhost:8000/health
curl http://localhost:8000/dashboard
curl "http://localhost:8000/search?q=Docker+nginx&k=3"
```
