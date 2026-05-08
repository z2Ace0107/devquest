
# Tech Design: DevQuest Log

## 系统架构

Streamlit前端 → FastAPI后端 → LangChain AI处理层 → SQLite + ChromaDB双存储

## 技术栈

- Python 3.10+
- Streamlit（前端）
- FastAPI + Uvicorn（后端）
- LangChain（对话分析、STAR生成、向量嵌入）
- DeepSeek API / OpenAI API（LLM + Embeddings）
- SQLite（结构化存储）
- ChromaDB（向量存储 + 语义搜索）
- Docker（部署）

## 目录结构

devquest-log/
├── backend/
│   ├── app.py              # FastAPI服务
│   ├── extractor.py        # 问题提取引擎
│   ├── classifier.py       # 技术分类
│   ├── scorer.py           # 优先级评分
│   ├── star_gen.py         # STAR故事生成
│   ├── vector_search.py    # ChromaDB向量搜索
│   ├── models.py           # SQLAlchemy模型
│   └── database.py         # 数据库初始化
├── frontend/
│   └── app.py              # Streamlit仪表盘
├── sample_conversations/   # 测试用对话样例
├── data/
│   ├── devquest.db         # SQLite数据库文件
│   └── chroma_db/          # ChromaDB持久化目录
├── requirements.txt
├── Dockerfile
└── README.md

## API设计

| 端点                    | 方法 | 说明                                 |
| :---------------------- | :--- | :----------------------------------- |
| `/extract`            | POST | 接收对话文本和项目名，提取问题并入库 |
| `/problems`           | GET  | 按项目/技术/分数筛选问题列表         |
| `/star/{id}`          | GET  | 为指定问题生成STAR故事               |
| `/problem/{id}/score` | PUT  | 手动更新优先级分数                   |
| `/dashboard`          | GET  | 返回统计摘要                         |
| `/search`             | GET  | 语义搜索历史问题（参数：q, k, tech） |
| `/rebuild-index`      | POST | 从SQLite重建ChromaDB向量索引         |

## 数据库设计

### SQLite（结构化存储）

CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT,
    description TEXT,
    attempts TEXT,
    solution TEXT,
    tech_stack TEXT,
    problem_type TEXT,
    priority_score INTEGER DEFAULT 5,
    raw_conversation TEXT,
    star_story TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

### ChromaDB（向量存储）

- 存储内容：问题的完整文本表示（标题 + 描述 + 解决方案）
- 元数据：id、tech_stack、priority_score
- 嵌入模型：OpenAI Embeddings（text-embedding-3-small）或 DeepSeek Embeddings

## 关键模块设计

- extractor.py：使用LangChain + 自定义Prompt，要求LLM输出严格JSON。
- scorer.py：用LLM打分或基于规则加权，权重可配置。
- star_gen.py：以第一人称、口语化风格生成面试回答。
- vector_search.py：封装ChromaDB的索引构建和相似搜索逻辑。
