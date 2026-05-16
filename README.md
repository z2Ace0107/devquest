# DevQuest

**开发者的"外脑" — 从 AI 编程对话中自动沉淀经验，在需要时精准找回。**

## 为什么做这个

每次和 Claude Code 结对编程，花一小时排查的 bug 解决了，换个 session 这些经验就消失在对话历史里。下次遇到类似问题，又得从头来过。

DevQuest 是我的答案：它静默地从每一段 AI 编程对话中提取技术问题、分类、评分、索引。下次搜索"上次那个 Docker 容器启动报错怎么修的"，它能直接定位到当时的上下文和解决方案，并告诉你那次方案现在还适用吗。

## 三个核心设计决策

- **知识不依赖记忆**：双通道检索（语义 + 关键词 + RRF 融合 + 查询改写），即使只记得碎片描述也能命中
- **知识会生长**：同一个问题在不同 session 中再出现时，自动归并到已有记录——attempts 积累、solution 迭代、技术栈补充
- **知识会反馈**：高频使用的问题在搜索排序中获得隐式 boost，越有用的经验越容易被找到

## 工作流

```
你的 Claude Code 对话
        │
        ▼
  session_ingestor          ← 自动扫描 JSONL，冷却检测 + 去重
        │
        ▼
  extractor → classifier → scorer  ← LLM 提取 + 分类 + 评分
        │
        ▼
  SQLite + ChromaDB + FTS5        ← 双通道索引（语义 + 全文）
        │
        ▼
  MCP Server (11 tools)            ← Claude Code 内直接调用
        │
        ├─ search_experience       ← "上次那个 docker 报错怎么修的"
        ├─ run_reflection          ← 每周自动反思，生成开发规则
        └─ generate_star           ← 面试前生成 STAR 故事
```

## 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（LLM）
- 阿里百炼 API Key（Embedding）

### 一键安装

```powershell
.\install.ps1
```

自动完成：pip 依赖 → MCP Server 注册 → Skill 安装 → 权限配置。安装后在项目目录的 `.env` 里填入你的 API Key，重启 Claude Code 即可。

### 手动安装

如果你不想用脚本，或使用 macOS/Linux：

1. `pip install -r requirements.txt`
2. 创建 `.env` 填入 API Key（参考 `.env.example`）
3. 注册 MCP Server：在 `~/.claude.json` 中加入 devquest 条目（参考 [HANDOFF.md](HANDOFF.md)）
4. 安装 Skill：`mkdir -p ~/.claude/skills/devquest && cp skill/SKILL.md ~/.claude/skills/devquest/SKILL.md`
5. 重启 Claude Code

### Skill 使用

Skill 提供快捷命令和自然语言触发，无需记住工具名和参数。

将 `skill/SKILL.md` 复制到 `~/.claude/skills/devquest/SKILL.md`：

```bash
mkdir -p ~/.claude/skills/devquest
cp skill/SKILL.md ~/.claude/skills/devquest/SKILL.md
```

重启 Claude Code 后即可使用：

| 快捷命令 | 效果 |
|----------|------|
| `/devquest save` | 自动从当前对话提取问题入库 |
| `/devquest 记` | 同上 |
| 说"查经验" | 搜索历史经验 |
| 说"经验库概览" | 查看统计摘要 |

### 首次摄入

在 Claude Code 中输入：

> 扫描我的历史会话，把所有技术问题导入经验库

DevQuest 会自动扫描 `~/.claude/projects/` 下的 JSONL 对话记录，提取技术问题并入库。

## MCP Tools

### 经验检索
| Tool | 说明 |
|------|------|
| `search_experience` | 双通道混合检索（查询改写 + 向量语义 + FTS5 关键词 + RRF 融合 + 隐式反馈 boost） |
| `list_problems` | 按项目/技术栈/评分/类型组合筛选 |
| `get_dashboard` | 经验库统计摘要（类型分布、技术栈排名、评分分布） |

### 数据摄入
| Tool | 说明 |
|------|------|
| `ingest_sessions` | 从 Claude JSONL 自动摄入，支持增量/全量模式 |
| `ingest_status` | 查看摄入状态（已处理会话数、累计问题数、待处理数） |
| `extract_from_text` | 手动粘贴对话文本提取问题（支持 ChatGPT/Copilot Chat 等外部对话） |

### 反思与成长
| Tool | 说明 |
|------|------|
| `run_reflection` | Rule-Maker 反思引擎：读取本周问题 → LLM 提炼共性模式 → 生成开发规则草案 |
| `get_suggestions` | 查看待确认的规则草案（Human-in-the-loop，不直接覆写） |
| `generate_star` | 为指定问题生成面试 STAR 故事（Situation/Task/Action/Result） |

### 维护
| Tool | 说明 |
|------|------|
| `rebuild_index` | 全量重建 ChromaDB + FTS5 双索引 |
| `update_score` | 手动调整问题优先级评分 (1-10) |

## 评测

提取引擎在 4 个对话样本（13 个预期问题）上的表现：

| 指标 | 数值 |
|------|------|
| 平均召回率 | 83% |
| 平均精确率 | 78% |
| 平均类型准确率 | 85% |
| 最强单样本 | 召回率 100% / 精确率 100% (api_migration_issue) |

评测脚本：`python scripts/eval_extractor.py`

## 项目结构

```
devquest/
├── backend/
│   ├── mcp_server.py         # MCP Server 入口（11 tools）
│   ├── extractor.py          # 问题提取引擎 + 语义去重
│   ├── classifier.py         # 技术标签自动分类
│   ├── scorer.py             # 优先级评分
│   ├── vector_search.py      # 双通道检索 + 查询改写 + 反馈闭环
│   ├── star_gen.py           # STAR 故事生成
│   ├── session_ingestor.py   # Claude JSONL 自动摄入
│   ├── rule_maker.py         # Rule-Maker 反思引擎
│   ├── models.py / database.py
├── scripts/
│   ├── eval_extractor.py     # 提取引擎评测
│   └── smoke_test.py         # 冒烟测试
├── sample_conversations/     # 评测用对话样本 (4 组)
├── skill/
│   └── SKILL.md               # Claude Code Skill（一键安装）
├── data/                     # SQLite + ChromaDB 持久化
├── AGENTS.md                 # AI 开发规范
└── CHANGELOG.md              # 版本记录
```

## 技术栈

Python 3.10+ · MCP SDK · LangChain · DeepSeek · 阿里百炼 Embedding · ChromaDB · SQLite FTS5 · SQLAlchemy

## 许可

MIT
