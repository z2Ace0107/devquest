# Tech Design: DevQuest Log

## 系统架构

Streamlit前端 → FastAPI后端 → LangChain AI处理层 → SQLite数据存储

## 技术栈

- Python 3.10+
- Streamlit
- FastAPI + Uvicorn
- LangChain
- DeepSeek API（兼容OpenAI格式）
- SQLite
- Docker

## 目录结构

devquest-log/
├── backend/
│   ├── app.py
│   ├── extractor.py
│   ├── classifier.py
│   ├── scorer.py
│   ├── star_gen.py
│   ├── models.py
│   └── database.py
├── frontend/
│   └── app.py
├── sample_conversations/
├── data/
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

## 数据库设计（SQLite）

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

## 关键模块设计

- extractor.py：使用LangChain + 自定义Prompt，要求LLM输出严格JSON。
- scorer.py：用LLM打分或基于规则加权，权重可配置。
- star_gen.py：以第一人称、口语化风格生成面试回答。
