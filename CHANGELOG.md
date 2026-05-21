# Changelog

## V4.0 — Agent 驱动知识工程 (进行中 — 2026-05-21)

### V4.0 计划
- 单 Agent Harness（observe → plan → evaluate → execute → remember）
- Topic/Concept/Link 数据模型 + 双向知识图谱
- 飞书 CLI 原生输出

## V3.1 — 零配置 + 体验闭环 (完成 — 2026-05-20)

### 飞书推送 ✅
- 新建 `backend/feishu.py`：Webhook 卡片推送（零 SDK、零 OAuth）
- 新增 `push_feishu_weekly` MCP tool → 工具总数 14
- `SKILL.md`：工具数 13→14 + 飞书推送工作流 + 触发词
- `requirements.txt` 补 `requests` 依赖
- `.env.example` 补 `FEISHU_WEBHOOK_URL`

### 工程修复 ✅
- `session_ingestor.py`：`datetime.utcnow()` → `datetime.now(timezone.utc)`（Phase 1 遗漏）
- `vector_search.py`：`add_to_index` 加 `logging.exception`
- `extractor.py`：`load_dotenv()` 改为显式路径
- `database.py`：删除 FastAPI 时代 `get_db()` 死代码
- `HANDOFF.md`：工具表更新至 14 个 + 架构图

### V3.1 计划
1. Phase 6.1: 本地 embedding → 零 API Key
2. Phase 6.2: DAG 上下文 → 精确率提升
3. Phase 6.3: Hook 自动捕获 → 零操作入库
4. Phase 6.4: 经验库健康检查
5. Phase 6.5: README 设计决策

## V3.0 — 产品化优化 (完成 — 2026-05-19)

### Phase 5: 工程完善 ✅
- 12 个单元测试（services + vector_search），`python -m pytest tests/ -v` 全绿
- README 重写：产品思维叙事 + 飞书 V2.1 规划 + 架构图 + 产品决策表
- Skill description 改为广覆盖（所有技术场景自动触发）
- AGENTS.md 更新：角色重定位 + Phase 6 飞书规划

### Phase 4: Skill 智能触发 ✅
- Skill 新增"错误自动搜索"工作流（最高优先级，遇 error 自动搜经验库，带环境匹配）
- Skill 新增"主动提议记录"工作流（解决难题后自动问是否记录）
- `/devquest save` 改用 `save_problem`（结构化捕获，不再走 LLM 提取）
- 工具列表更新为 13 个 + 搜索策略强调传 environment 参数

### Phase 3: MCP 新工具 ✅
- 新建 `backend/services.py` 业务逻辑层（save_problem_service / record_feedback_service）
- 新增 `save_problem` MCP tool：结构化捕获，跳过 LLM 提取，自动分类+评分+去重+索引
- 新增 `record_feedback` MCP tool：显式反馈闭环，有用+10 usage / 没用-2
- `search_experience` 增强：新增 `environment` 参数，OS 匹配 +15% 权重，不匹配标注差异
- `_rrf_fusion` 加时效衰减 0.85^months + `_load_meta_for_boost` 批量加载环境/时间
- `record_search_impressions` 实际实现（搜索曝光 usage_count +1）

### Phase 2: 数据模型升级 ✅
- Problem 新增 5 字段：`environment`、`feedback_score`、`feedback_count`、`first_seen_at`、`solution_version`
- `database.py` 新增 `_migrate_v2_columns()` 静默迁移
- `extractor._save_to_db` 新建记录时写入 `first_seen_at`

### Phase 1: 工程修复 ✅
- `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)`（Python 3.12+ 废弃 API）
- `_safe_index` 加 `logging.exception`（异常不再静默吞噬）
- `backend/__init__.py` 配置日志级别（环境变量 `LOG_LEVEL`，默认 WARNING）

## V1.1 (已完成 — 2026-05-12)

### 新增
- ✅ `backend/session_ingestor.py`：自动扫描 Claude JSONL 对话记录，零操作摄入经验库
- ✅ `backend/weekly_report.py`：LLM 生成结构化周报（关键问题 / 技能成长 / 待补短板）
- ✅ 前端搜索增强：搜索建议、关键词高亮、最近7天快捷筛选
- ✅ 新增 API 端点：`/ingest/start`、`/ingest/status`、`/report/weekly`

### 变更
- 经验入库方式从"手动上传 .txt"升级为"自动读取 Claude 本地会话 JSONL"
- 新增环境变量 `CLAUDE_SESSIONS_DIR`、`WATCH_PROJECTS`

### 移除
- 原计划的 file_watcher.py（watchdog 监听 .txt 目录）— 被 session_ingestor 替代
- 原计划的 tray_app.py（pystray 系统托盘）— 无交互需求的常驻进程，投入产出比低

---

## V1.2 (已完成 — 2026-05-14)

### 定位调整
- 从"独立 Web 应用"重构为"MCP-native 服务"

### Phase 1: MCP Server 化
- 新建 `backend/mcp_server.py` — Python MCP SDK，11 个 tools
- 删除 `backend/app.py` — FastAPI 被 MCP Server 替代
- 删除 `frontend/app.py` — Streamlit 被 MCP Client 替代
- 删除 `backend/weekly_report.py` — 功能蔓延，与核心定位无关

### Phase 2: Rule-Maker 反思引擎
- 新建 `backend/rule_maker.py` — 读取本周 problems → LLM 反思 → 生成规则草案
- 写入 `rules_suggestions.md`（Human-in-the-loop，不直接覆写项目规则文件）
- MCP tools: `run_reflection`、`get_suggestions`

### Phase 3: 清理
- 精简 `requirements.txt`（移除 fastapi/uvicorn/streamlit/altair）
- 合并冗余文档为 3 个：AGENTS.md + CHANGELOG.md + README.md

### 新增 MCP Tools
- `get_dashboard` — 统计摘要
- `update_score` — 手动修改评分
- `run_reflection` — 触发 Rule-Maker 反思
- `get_suggestions` — 查看待确认规则草案

---
## V1.3 (已完成 — 2026-05-15)

### 检索增强

- 查询改写 (`_rewrite_query()`): 自然语言去口语化，中英文填充词过滤，纯规则引擎无 LLM 开销
- 隐式反馈闭环: `usage_count` 字段 + RRF 融合排序 boost，高频使用文档获得最多 30% 额外权重
- 语义去重: 新问题入库前向量相似度匹配，余弦距离 < 0.125 触发合并（追加 attempts / 替换更优 solution / 合并 tech_stack）

### 产品化

- README 追加"为什么做这个"段落，从产品视角讲述设计决策
- AGENTS.md 更新 V1.3 版本记录

### 评测
- 提取引擎 1 样本 4 问题: 召回率 100%，精确率 100%，类型准确率 75%（新增 three anchors 未影响评测结果）

---

## V1.0 (2026-05-10)

### 新增
- 对话导入与 LLM 问题提取引擎（extractor + classifier）
- 四维度优先级评分算法（scorer）
- STAR 面试故事生成（star_gen）
- ChromaDB 向量语义搜索 + SQLite FTS5 双通道混合检索（RRF 融合）
- FastAPI 后端 7 端点 + Streamlit 四页面仪表盘
- Docker / docker-compose 部署

### 技术栈
Python 3.10+ / FastAPI / LangChain / ChromaDB / SQLite FTS5 / Streamlit / DeepSeek API / 阿里百炼 Embedding / Docker

### 统计
13 次 commit，7 个 API 端点，4 页面仪表盘，双存储架构
