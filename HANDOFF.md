# DevQuest — 交接文档 (2026-05-24)

## 项目状态: V4.3 代码审查修复（进行中）

详细优化计划见 `plans/v4.3-optimization.md`

### 🔴 必须修的 Bug（5 个）
1. guardrails._check_compile() 检查 `problem_count` 但 harness 传 `new_count` → compile 被阻止
2. extractor.py 飞书归档 URL 是未插值 f-string
3. rule_maker.py 用 `p.get("project")` 但 `to_dict()` 返回 `"project_name"`
4. vector_search.py SQL 拼接注入风险
5. services.py `usage_count +=10/-2` 语义扭曲

### 🟡 架构优化（7 个）
6. `_plan()` 从规则引擎升级为 LLM 推理决策
7. DAG 上下文接入搜索或砍掉
8. 飞书归档改为软标记
9. Hook 守护进程简化
10. mcp_server.py 拆分
11. `_probe_llm()` 缓存
12. `_graph_expand()` 批量查询

## 版本历史

| 版本 | 内容 | 状态 |
|------|------|------|
| V1.0–V1.3 | extractor/classifier/scorer/star_gen/search/session_ingestor | ✅ |
| V3.0 | 项目更名为 DevQuest + 一键安装 + Skill + MCP 14 tools | ✅ |
| V4.0 P1 | Agent 框架核心（harness/state/tools/memory/guardrails，6 文件） | ✅ |
| V4.0 P2 | 数据模型升级（Topic/Concept/Link/AgentAction + organize 聚类 + compile topic_id） | ✅ |
| V4.0 P3 | 统一 LLM 客户端主备切换（`llm_client.py` + 5 文件重构） | ✅ |
| V4.0 P4.1 | 飞书 CLI 输出（`feishu_cli.py` + compile_tool push_to_feishu + state 动态检测） | ✅ |
| V4.0 P4.2 | Hook 自动捕获 + DAG 上下文（`hook_capture.py` + 4 新 MCP tools） | ✅ |

## 当前架构

```
MCP Client (Claude Code)
    │
    ▼
MCP Server (21 tools) ──→ Harness Agent (observe→plan→evaluate→execute→remember)
    │                              │
    ├── search_experience ←────────┤ 9 Agent Tools
    ├── save_problem              ├── auto_ingest / observe / capture / organize
    ├── ingest_sessions           ├── compile / search / health_check / feishu_status / push
    ├── push_feishu_weekly        │
    ├── run_reflection            ▼
    ├── run_agent          llm_client.py (Primary: opencode.ai / Fallback: api.deepseek.com)
    ├── hook_status/start/stop         │
    └── ...                    SQLite + ChromaDB + FTS5
                               (Problem/Topic/Concept/Link/AgentAction)
         Hook Daemon ──→ data/hook_state.json ──→ Agent State 读取
```

## 当前文件结构

```
devquest/
├── backend/
│   ├── agent/
│   │   ├── __init__.py         # Agent 模块
│   │   ├── harness.py          # 主循环 observe→plan→evaluate→execute
│   │   ├── state.py            # 三层状态感知（知识层/输出层/输入层 + Topic数据）
│   │   ├── tools.py            # 8 工具函数（compile 支持 push_to_feishu 参数）
│   │   ├── memory.py           # 工作记忆 + AgentAction 持久化
│   │   └── guardrails.py       # 6 条质量约束（push/compile/organize）
│   ├── llm_client.py           # 统一 LLM 客户端（主备自动切换）
│   ├── mcp_server.py           # MCP Server 入口 (15 tools)
│   ├── extractor.py            # 问题提取引擎 + 语义去重
│   ├── classifier.py           # 技术标签分类
│   ├── scorer.py               # 优先级评分
│   ├── star_gen.py             # STAR 故事生成
│   ├── vector_search.py        # 双通道 RRF 检索 + 查询改写 + 反馈闭环
│   ├── session_ingestor.py     # Claude JSONL 自动摄入
│   ├── rule_maker.py           # Rule-Maker 反思引擎
│   ├── feishu.py               # 飞书 Webhook 推送
│   ├── feishu_cli.py           # 飞书 lark-cli 封装（文档创建/更新）
│   ├── services.py             # 结构化录入 + 反馈 Service 层
│   ├── database.py             # SQLAlchemy + Migration + FTS5
│   └── models.py               # ORM: Project/Problem/Topic/Concept/Link/AgentAction
├── skill/
│   └── SKILL.md                # Claude Code Skill
├── tests/
│   ├── test_agent.py           # Agent 框架单测（17 条）
│   ├── test_services.py        # Service 层单测（5 条）
│   ├── test_vector_search.py   # 检索单测（7 条）
│   ├── test_feishu_cli.py      # 飞书 CLI 单测（14 条）
│   ├── test_llm_client.py      # LLM 客户端单测（16 条）
│   └── test_hook_capture.py    # V4.2 新增：Hook 捕获单测（22 条）
├── scripts/
│   ├── eval_extractor.py
│   ├── smoke_test.py
│   └── hook_capture.py       # V4.2 新增：Hook 后台守护进程
├── install.ps1                 # 一键安装
├── AGENTS.md                   # AI 开发规范（当前）
├── CHANGELOG.md                # 版本记录（当前）
├── HANDOFF.md                  # 本文档
└── README.md                   # 产品视角说明
```

## 安装方式

**一键安装（Windows）：**
```powershell
.\install.ps1
```

**手动安装：**
1. `pip install -r requirements.txt`
2. 创建 `.env` 填入 API Key（见下方环境变量）
3. `cp skill/SKILL.md ~/.claude/skills/devquest/SKILL.md`
4. 在 `~/.claude.json` 注册 MCP Server
5. 在 `~/.claude/settings.json` 注册权限
6. 重启 Claude Code

## 环境变量 (.env)

```
# LLM Primary Provider（opencode.ai DeepSeek V4 Flash）
LLM_PRIMARY_API_KEY=sk-your-key
LLM_PRIMARY_BASE_URL=https://opencode.ai/zen/go/v1
LLM_PRIMARY_MODEL=deepseek-v4-flash

# LLM Fallback Provider（旧 DeepSeek API，主不可用时自动降级）
LLM_FALLBACK_API_KEY=sk-your-key
LLM_FALLBACK_BASE_URL=https://api.deepseek.com/v1
LLM_FALLBACK_MODEL=deepseek-v4-flash

# Embedding（阿里百炼）
EMBEDDING_API_KEY=sk-your-key
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 飞书推送（可选）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token
```

## 21 个 MCP Tools

| Tool | 说明 |
|------|------|
| `search_experience` | 双通道 RRF 检索 + 环境匹配 + 时效衰减 |
| `save_problem` | 结构化录入（推荐） |
| `extract_from_text` | 手动导入对话（LLM 提取） |
| `ingest_sessions` | 自动摄入 Claude 对话 |
| `ingest_status` | 摄入状态 |
| `list_problems` | 筛选问题列表 |
| `get_dashboard` | 统计摘要 |
| `record_feedback` | 显式反馈闭环 |
| `push_feishu_weekly` | 推送周报到飞书 |
| `run_reflection` | Rule-Maker 反思 |
| `get_suggestions` | 查看规则草案 |
| `rebuild_index` | 重建索引 |
| `generate_star` | STAR 故事 |
| `update_score` | 修改评分 |
| `run_agent` | 手动触发 Agent 认知循环 |
| `llm_status` | V4.1 LLM 提供商状态 + 额度通知 |
| `acknowledge_quota` | V4.1 确认 LLM 额度耗尽决策 |
| `hook_status` | **V4.2** Hook 自动捕获运行状态 |
| `start_hook` | **V4.2** 启动 Hook 后台守护进程 |
| `stop_hook` | **V4.2** 停止 Hook 后台守护进程 |
| `get_dag_context` | **V4.2** 查看 DAG 上下文（工作目录/分支） |

## V4.0 Agent 架构要点

**单 Agent Harness 循环**: observe → plan → evaluate → execute → remember

**决策优先级**:
0. Hook 检测到待摄入会话 → auto_ingest（V4.2 新增，最高优先级）
1. 孤儿 Problem ≥ 3 → organize（聚类成 Topic + 创建 Link）
2. Growing Topic 有实质更新 → compile（若 lark-cli 可用则 compile_push 自动推送飞书文档）
3. 低质量 > 5 条 → health_check
4. 过期 > 10 条 → health_check
5. 本周有新经验 + 飞书配置 → push 摘要

**6 条 Guardrails**:
- 推送内容过短 (<50 字) → 阻止
- Topic < 2 条经验 → 不编译飞书文档
- 上次推送 < 2h → 降频
- 飞书文档连续 3 次无人访问 → 降优先级
- LLM 摘要 < 50 字 → 重新生成
- 编译内容无实质变更 → 不推送

## 测试

```bash
.venv/Scripts/python.exe -m pytest tests/ -v  # 80 tests

# 手动触发 Agent
.venv/Scripts/python.exe -c "
from backend.agent.harness import HarnessAgent
r = HarnessAgent().run()
print('决策:', r['decision'], '状态:', r['state'])
"
```

## 待办（V4.3–V4.5）

| Phase | 内容 | 状态 | 关键文件 |
|-------|------|------|---------|
| V4.3 🔴 | 5 个 Bug 修复 | 进行中 | guardrails/extractor/rule_maker/vector_search/services |
| V4.3 🟡 | Agent LLM 推理决策 | 待开始 | harness.py |
| V4.3 🟡 | DAG 接入搜索 boost | 待开始 | state.py, vector_search.py |
| V4.3 🟡 | 飞书归档软标记 | 待开始 | extractor.py |
| V4.3 🟡 | Hook 简化 | 待开始 | hook_capture.py |
| V4.3 🟡 | mcp_server 拆分 | 待开始 | mcp_server.py |
| V4.3 🟡 | _probe_llm 缓存 | 待开始 | llm_client.py |
| V4.3 🟡 | 图谱批量查询 | 待开始 | vector_search.py |
| V4.3 🟢 | 死代码清理 | 待开始 | classifier/star_gen |
| V4.3 🟢 | config.py 集中配置 | 待开始 | 新增文件 |
| V4.5 | 零 API Key（本地 embedding） | 待开始 | vector_search.py |
