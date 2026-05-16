# DevQuest — 交接文档 (2026-05-15)

## 项目状态: V1.2 完成，V1.3 规划中

## 版本历史

| 版本 | 内容 | 状态 |
|------|------|------|
| V1.0 | extractor + classifier + scorer + star_gen + vector_search + FastAPI + Streamlit | ✅ |
| V1.1 | session_ingestor + weekly_report + 搜索增强 | ✅ |
| V1.2 | MCP Server 化 + Rule-Maker + 文档精简 + 评测脚本 | ✅ |

## 当前架构

```
Claude Code ← MCP Server (11 tools) → LangChain + DeepSeek → SQLite + ChromaDB
                  ↑
            Skill 包装层
```

## 当前文件结构

```
backend/
├── mcp_server.py          # MCP Server 入口 (11 tools)
├── rule_maker.py          # Rule-Maker 反思引擎 → rules_suggestions.md
├── extractor.py           # 问题提取引擎
├── classifier.py          # 技术分类
├── scorer.py              # 优先级评分
├── star_gen.py            # STAR 故事生成 (降级保留)
├── vector_search.py       # 双通道 RRF 检索
├── session_ingestor.py    # Claude JSONL 自动摄入
├── database.py / models.py
scripts/
├── eval_extractor.py      # 提取引擎评测脚本
sample_conversations/
├── mock_web_app_debug.txt # 测试样本
├── expected.json          # 人工标注预期
docs/
├── INTERVIEW_QA.md        # 面试问答
data/                      # SQLite + ChromaDB
reports/                   # 历史周报归档
```

## 配置管理（用户级，不在本仓库）

- MCP Server 配置: `~/.claude.json` → `mcpServers.devquest`
- MCP 权限: `~/.claude/settings.json` → `permissions.allow`
- Skill: `~/.claude/skills/devquest/SKILL.md`

## 11 个 MCP Tools

| Tool | 说明 |
|------|------|
| `search_experience` | 双通道 RRF 检索 |
| `ingest_sessions` | 自动摄入 Claude 对话 |
| `ingest_status` | 摄入状态 |
| `extract_from_text` | 手动导入对话 |
| `list_problems` | 筛选问题列表 |
| `get_dashboard` | 统计摘要 |
| `rebuild_index` | 重建索引 |
| `generate_star` | STAR 故事 |
| `update_score` | 修改评分 |
| `run_reflection` | Rule-Maker 反思 |
| `get_suggestions` | 查看规则草案 |

## 评测数据 (2026-05-15)

提取引擎在 1 个样本（4 个问题）上:
- 召回率: 100%
- 精确率: 100%
- 类型准确率: 75%

## 待办 (按优先级)

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| P3 | ask_experience tool | backend/mcp_server.py | 搜索 + LLM 适用性分析，搬到项目代码里 |
| P5 | scheduler.py | scripts/scheduler.py | 定时调度框架（不实际跑） |
| P6 | 更新面试问答 | docs/INTERVIEW_QA.md | 加入双项目组合回答 |
| - | 扩充测试集 | sample_conversations/ | 加 2-3 个更多样的对话样本 |
