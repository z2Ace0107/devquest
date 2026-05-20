# DevQuest — 交接文档 (2026-05-16)

## 项目状态: V3.0 完成

## 版本历史

| 版本 | 内容 | 状态 |
|------|------|------|
| V1.0 | extractor + classifier + scorer + star_gen + vector_search + FastAPI + Streamlit | ✅ |
| V1.1 | session_ingestor + weekly_report + 搜索增强 | ✅ |
| V1.2 | MCP Server 化 + Rule-Maker + 文档精简 + 评测脚本 | ✅ |
| V1.3 | 查询改写 + 隐式反馈闭环 + 语义去重 | ✅ |
| V3.0 | 项目更名为 DevQuest + 一键安装 + Skill 快捷命令 + 评测扩至 4 样本 | ✅ |

## 当前架构

```
MCP Client ← MCP Server (11 tools) → LangChain + DeepSeek → SQLite + ChromaDB
                  ↑                         ↑
            Skill 包装层             查询改写 / 反馈闭环 / 语义去重
```

## 当前文件结构

```
devquest/
├── backend/
│   ├── mcp_server.py          # MCP Server 入口 (11 tools)
│   ├── extractor.py           # 问题提取引擎 + 语义去重 (_merge_problem)
│   ├── classifier.py          # 技术标签分类
│   ├── scorer.py              # 优先级评分
│   ├── star_gen.py            # STAR 故事生成
│   ├── vector_search.py       # 双通道 RRF 检索 + 查询改写 + 反馈闭环
│   ├── session_ingestor.py    # Claude JSONL 自动摄入
│   ├── rule_maker.py          # Rule-Maker 反思引擎
│   ├── database.py / models.py
├── skill/
│   └── SKILL.md               # Claude Code Skill（/devquest save 等）
├── scripts/
│   ├── eval_extractor.py      # 提取引擎评测（多样本）
│   └── smoke_test.py          # 冒烟测试
├── sample_conversations/      # 4 组评测样本
│   ├── mock_web_app_debug.txt # Docker/Nginx/SQLAlchemy/架构
│   ├── react_table_bug.txt    # React 渲染性能
│   ├── api_migration_issue.txt# REST→GraphQL 迁移
│   ├── cicd_deploy_issue.txt  # CI/CD 流水线优化
│   └── expected.json          # 人工标注预期
├── data/                      # SQLite + ChromaDB（不入 git）
├── install.ps1                # 一键安装脚本
├── AGENTS.md                  # AI 开发规范
├── CHANGELOG.md               # 版本记录
└── README.md                  # 产品视角说明
```

## 安装方式

**一键安装（Windows）：**
```powershell
.\install.ps1
```
自动完成：pip 依赖 → MCP Server 注册 → Skill 安装 → 权限配置。然后在 `.env` 里填 API Key，重启 Claude Code。

**手动安装（macOS/Linux）：**
1. `pip install -r requirements.txt`
2. 创建 `.env` 填入 API Key
3. 在 `~/.claude.json` 注册 MCP Server
4. `cp skill/SKILL.md ~/.claude/skills/devquest/SKILL.md`
5. 重启 Claude Code

## 配置管理（用户级，不在本仓库）

- MCP Server 配置: `~/.claude.json` → `mcpServers.devquest`
- MCP 权限: `~/.claude/settings.json` → `mcp__devquest__*`（11 个）
- Skill: `~/.claude/skills/devquest/SKILL.md`

## 11 个 MCP Tools

| Tool | 说明 |
|------|------|
| `search_experience` | 查询改写 + 双通道 RRF 检索 + 隐式反馈 boost |
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

## Skill 快捷命令

| 命令 | 效果 |
|------|------|
| `/devquest save` | 自动从当前对话提取问题入库（自动推断项目名） |
| `/devquest 记` | 同上 |
| 说"查经验 XXX" | 搜索历史经验 |
| 说"经验库概览" | 查看统计摘要 |
| `/devquest save 项目名` | 指定项目名入库 |

## V3.0 新增特性

### 查询改写 (`_rewrite_query()`)
- 去除中英文口语填充词，提取技术关键词
- 纯规则引擎，零延迟零成本
- 改写词在 `_debug.rewritten_query` 中可观测

### 隐式反馈闭环
- Problem 新增 `usage_count` 字段
- STAR 生成时自动 +1，RRF 融合时高频文档获得最多 30% 额外权重
- `search_experience` 返回改写词 + 记录曝光

### 语义去重 (`search_similar()` + `_merge_problem()`)
- 新问题入库前与已有库做向量相似度匹配
- 余弦距离 < 0.125 视为重复，合并而非新建
- 合并策略：追加 attempts、替换更优 solution、合并 tech_stack

## 评测数据 (2026-05-16)

提取引擎在 4 个对话样本（13 个预期问题）上：

| 指标 | 数值 |
|------|------|
| 平均召回率 | 83% |
| 平均精确率 | 78% |
| 平均类型准确率 | 85% |
| 最强单样本 | 召回率 100% / 精确率 100% (api_migration_issue) |

## 仓库

- GitHub: `https://github.com/z2Ace0107/devquest`
- 父仓库 `z2Ace0107/claude` 中也包含一份副本

## 待办

| # | 任务 | 说明 |
|---|------|------|
| - | 使用数据更新 | 开发 Agent 项目期间用 `/devquest save` 积累真实数据后，更新 README 中的"已沉淀 XX 条" |
| - | Agent 项目交叉引用 | Agent 主项目 README 加一句"历史方案检索复用 DevQuest RRF 方案" |
