# AGENTS.md - DevQuest

## 角色

你是一个资深全栈AI应用开发专家。你正在协助开发者完善 DevQuest——一个**MCP-native AI 编程上下文增强层**。定位从"经验搜索引擎"转为"让 AI 带着你的历史回答你"。

## 当前计划

**V3.0 产品化优化** — 详细计划见 `C:\Users\Y7000p\.claude\plans\mutable-gliding-lark.md`（或 Claude Code 中输入 `/plans` 查看）。

产品定位从"经验搜索引擎"转变为"AI 编程上下文增强层"。分 5 个 Phase 执行：
1. ✅ Phase 1: 工程修复（utcnow 废弃 API、日志）
2. ✅ Phase 2: 数据模型升级（environment / feedback / 时效衰减字段）
3. ✅ Phase 3: MCP 新工具（save_problem / record_feedback / search_experience 增强）
4. ✅ Phase 4: Skill 智能触发（错误自动搜索 / 主动提议记录）
5. Phase 5: 单测 + README 产品定位重写 + 文档同步 ← **当前**
6. Phase 6: 飞书连接（团队知识推送 / Bot 查询入口 / 周报推送）← 规划中

每个 Phase 完成后 → 手动测试 → Git 提交 → 用户验证通过 → 下一步。

## 核心原则

- **解决实际问题优先**：每个功能必须回答"我明天会不会用到"。不加简历驱动的功能。
- **AGENTS.md 先行**：需求变更先更新本文，代码跟着 AGENTS.md 走。
- **零操作目标**：能自动化的绝不手动，能从源头消费的不走中间文件。
- **PRD/Tech Design 事后补**：每个大版本结束时更新一次，日常迭代只维护 AGENTS.md。

## 技术栈约束

- 语言：Python 3.10+
- 协议层：Python MCP SDK（替代 FastAPI + Streamlit）
- 存储：SQLAlchemy、SQLite
- AI层：LangChain、DeepSeek API（兼容OpenAI格式）、阿里百炼 Embedding
- 向量数据库：ChromaDB
- 交互：MCP Client（Claude Code / Claude Desktop / Cursor 等）
- 对话源：Claude JSONL session 文件（~/.claude/projects/）

## 开发原则

- 每个模块先给出完整可运行的代码，再简要解释设计思路。
- 所有API接口附上请求/响应示例。
- Prompt模板完整给出，不要省略。
- MVP功能优先，非MVP功能用 `# TODO` 标记。
- 代码注释使用中文，变量名使用英文。
- 一次只实现一个模块，等待我测试通过后再继续下一个。

## V1.0 — MVP（已完成）

- database.py + models.py
- extractor.py — 问题提取引擎
- classifier.py — 技术标签自动分类
- scorer.py — 优先级评分
- star_gen.py — STAR 故事生成
- vector_search.py — 双通道混合检索
- backend/app.py — FastAPI 7端点
- frontend/app.py — Streamlit 4页面仪表盘

## V1.1 — 零操作自动化（当前版本）

### 目标

用户不再需要任何手动操作。Claude 对话结束后自动入库，每周自动生成技术成长周报。

### 开发顺序（严格按此顺序）

1. `backend/session_ingestor.py` — Claude session 直接读取引擎
   - 扫描 ~/.claude/projects/<project-slug>/*.jsonl
   - 按 session 聚合对话消息
   - 增量处理：记录 last_processed_uuid，只处理新消息
   - 过滤非技术对话（/clear、/config、闲聊等）
   - session 冷却判定：文件静默 30min+ 视为对话结束，触发 /extract
   - 去重：同一 session 已处理过的不重复入库
   - 提供 ingest_all() 和 ingest_incremental() 两个接口

2. `backend/weekly_report.py` — 周报生成器
   - 从 SQLite 取出本周新增问题
   - LLM 生成 Markdown 周报：关键问题 / 技能成长 / 待补短板
   - 输出到 reports/weekly_YYYY-MM-DD.md
   - 支持手动触发 + 定时（周日自动）

3. `frontend/app.py` — 搜索体验增强
   - 搜索框加搜索建议（输入关键词下拉显示相关标签）
   - 搜索结果高亮关键词
   - 问题列表页加"最近7天"快捷筛选项

### 不做的事情（及原因）

- ~~file_watcher.py + watchdog~~ — 要求用户手动导出 .txt，违背零操作目标
- ~~tray_app.py + pystray~~ — 无交互需求的常驻进程，startup.bat 足够
- ~~前端自动刷新开关~~ — 换成更有用的搜索增强，不锦上添花

## V1.2 — MCP 化 + Rule-Maker（当前版本）

### 目标

从"独立 Web 应用"变为"MCP-native 服务"。砍掉前端，MCP Server 协议化所有接口，新增后台反思引擎自动沉淀开发规则。

### Phase 1: MCP Server 化（P0）

新建 `backend/mcp_server.py`，用 Python MCP SDK 替代 FastAPI + Streamlit。

**MCP Tool 映射：**

| Tool | 来源 | 说明 |
|------|------|------|
| `search_experience` | `GET /search` | 双通道混合检索历史经验 |
| `ingest_sessions` | `POST /ingest/start` | 增量摄入 Claude 会话 |
| `ingest_status` | `GET /ingest/status` | 查看摄入状态 |
| `extract_from_text` | `POST /extract` | 手动粘贴对话提取问题 |
| `list_problems` | `GET /problems` | 按条件筛选问题列表 |
| `get_dashboard` | `GET /dashboard` | 统计摘要 |
| `rebuild_index` | `POST /rebuild-index` | 重建双通道索引 |
| `generate_star` | `GET /star/{id}` | STAR 故事（保留但不主打） |
| `update_score` | `PUT /problem/{id}/score` | 手动修改评分 |

**删除：**
- `frontend/app.py` — Streamlit（MCP Client 替代）
- `backend/weekly_report.py` — 周报生成器（功能蔓延，与核心定位无关）
- `backend/app.py` — FastAPI 服务（被 mcp_server.py 替代）

### Phase 2: Rule-Maker 反思引擎（P1）

新建 `backend/rule_maker.py`：

1. 从 SQLite 取出本周新增 problems
2. LLM 反思：提取共性模式 → 生成平台无关的规则草案
3. 写入 `rules_suggestions.md`（不直接覆写，Human-in-the-loop）
4. 用户 review 确认后手动合并到对应工具的规则文件

**MCP Tool：**

| Tool | 说明 |
|------|------|
| `run_reflection` | 手动触发反思，生成规则建议 |
| `get_suggestions` | 查看当前待确认的规则草案 |

**安全设计（面试重点）**：LLM 自动反思 + 人工确认双层机制，避免幻觉规则污染项目配置。

### Phase 3: 清理（P2）

- 精简 `requirements.txt`（移除 fastapi/uvicorn/streamlit/altair）
- 合并冗余文档为 3 个：AGENTS.md + CHANGELOG.md + README.md

### 开发顺序

1. `backend/mcp_server.py` — MCP Server 入口（8 个 tools）
2. `backend/rule_maker.py` — Rule-Maker 反思引擎
3. 删除废弃文件 + 精简依赖

### 目标架构

```
MCP Client ← MCP Server → LangChain → SQLite + ChromaDB
                  ↓
          Rule-Maker Daemon
                  ↓
      rules_suggestions.md
                  ↓
 用户 review 确认后注入项目规则文件
```

### 不做的事情（及原因）

- ~~ChatGPT 对话导入~~ → 等核心链路跑通再说
- ~~前端重写（Vue/React）~~ → MCP Client 就是交互入口
- ~~多用户支持 / 云端化~~ → 个人工具定位，简历够用即可
- ~~weekly_report.py~~ → 功能蔓延，与"开发者外脑"定位无关

## V1.3 — 检索增强 + 知识生长（当前版本）

### 目标

让检索更精准，让知识库从"存储"进化为"生长"。

### 已完成

1. **查询改写** (`vector_search.py` — `_rewrite_query()`)
   - 去除自然语言口语填充词，提取技术关键词
   - 纯规则引擎，零延迟零成本
   - 改写词在 `_debug.rewritten_query` 中可观测

2. **隐式反馈闭环** (`vector_search.py` + `models.py` + `mcp_server.py`)
   - Problem 新增 `usage_count` 字段，STAR 生成时自动 +1
   - RRF 融合时高频文档获得最多 30% 额外权重
   - `search_experience` 返回改写词 + 记录曝光

3. **语义去重** (`vector_search.py` — `search_similar()` + `extractor.py` — `_merge_problem()`)
   - 新问题入库前与已有库做向量相似度匹配
   - 余弦距离 < 0.125 视为重复，合并而非新建
   - 合并策略：追加 attempts、替换更优 solution、合并 tech_stack

### 开发顺序

此版本三项改动已完成（2026-05-15），4 组评测样本验证通过。

## 多源扩展（规划中，暂缓）

- ChatGPT 对话导出导入
- 手动快速录入
- 跨设备同步

## Session 源数据格式

Claude 对话存储在 `~/.claude/projects/<project-slug>/<session-id>.jsonl`，每行一条 JSON 消息：

```json
{
  "type": "user" | "assistant" | "system",
  "message": {"role": "user" | "assistant", "content": "..."},
  "uuid": "unique-message-id",
  "parentUuid": "parent-message-uuid",
  "sessionId": "session-uuid",
  "timestamp": "2026-05-11T14:09:43.114Z",
  "cwd": "e:\\develop\\claude",
  "gitBranch": "main"
}
```

## MCP Tools（V1.2 目标）

| Tool | 说明 | 版本 |
|------|------|------|
| `search_experience` | 双通道混合搜索（向量 + FTS5 → RRF） | V1.0 |
| `ingest_sessions` | 启动 session 增量摄入 | V1.1 |
| `ingest_status` | 查看摄入状态 | V1.1 |
| `extract_from_text` | 手动粘贴对话提取问题 | V1.0 |
| `list_problems` | 按项目/技术/分数/类型筛选 | V1.0 |
| `get_dashboard` | 统计摘要 | V1.0 |
| `rebuild_index` | 全量重建双通道索引 | V1.0 |
| `generate_star` | STAR 故事生成（保留不主打） | V1.0 |
| `update_score` | 手动修改评分 | V1.0 |
| `run_reflection` | 触发 Rule-Maker 反思 | V1.2 |
| `get_suggestions` | 查看待确认规则草案 | V1.2 |

## 环境变量 (.env)

```
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CLAUDE_SESSIONS_DIR=C:/Users/Y7000p/.claude/projects
WATCH_PROJECTS=e--develop-claude
```

## 已知限制

1. FTS5 中文分词：需 Linux + ICU tokenizer 支持，当前 Windows 中文走 LIKE 兜底
2. ChromaDB `where` 不支持 AND 组合：project 过滤和 tech 过滤不能同时走 DB 层
3. Session 源路径硬编码 Windows 格式，跨平台需配置化
4. MCP Server 依赖 MCP Client（Claude Code / Claude Desktop / Cursor 等），无独立 UI
