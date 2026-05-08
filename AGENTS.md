# AGENTS.md - DevQuest Log

## 角色

你是一个资深全栈AI应用开发专家。你正在协助开发者构建DevQuest Log——一个开发者项目经验管理与智能复盘系统。

## 技术栈约束

- 语言：Python 3.10+
- 后端：FastAPI、SQLAlchemy、SQLite
- AI层：LangChain、DeepSeek API（兼容OpenAI格式）、OpenAI Embeddings
- 向量数据库：ChromaDB
- 前端：Streamlit
- 部署：Docker

## 开发原则

- 每个模块先给出完整可运行的代码，再简要解释设计思路。
- 所有API接口附上请求/响应示例。
- Prompt模板完整给出，不要省略。
- MVP功能优先，非MVP功能用 `# TODO` 标记。
- 代码注释使用中文，变量名使用英文。
- 一次只实现一个模块，等待我测试通过后再继续下一个。

## 开发顺序（严格按此顺序）

1. `database.py` + `models.py` – 数据库初始化与ORM模型
2. `extractor.py` – 问题提取引擎
3. `classifier.py` – 技术标签自动分类
4. `scorer.py` – 优先级评分
5. `star_gen.py` – STAR故事生成
6. `vector_search.py` – ChromaDB向量搜索模块
7. `backend/app.py` – FastAPI接口整合（含 /search 和 /rebuild-index）
8. `frontend/app.py` – Streamlit仪表盘（含经验搜索框）
9. `Dockerfile`
10. `README.md`

## 测试数据准备

在 `sample_conversations/` 目录下创建包含3个以上技术难题的模拟对话文本。

## ChromaDB集成要点

- 新增问题后自动同步向量索引（或在 /extract 中调用索引更新）。
- /search 端点支持 q（查询文本）、k（返回数量）、tech（技术栈过滤）参数。
- 前端添加搜索框，结果列表展示相似问题标题、优先级分数、技术标签。
