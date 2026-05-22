# DevQuest

**Agent 驱动的知识工程系统——你的第二大脑。**

DevQuest 的 Agent 静默采集你的开发经验，自主组织为结构化知识，自动同步到飞书。你不需要记笔记——Agent 替你维护。

---

## 为什么做

每天和 AI 结对编程，排查一小时的 Docker 配置问题修好了，换 session 就消失。下次遇到"上次那个容器启动报错怎么修的"，只能从头来。

现有的"解决方案"——手动记笔记（靠自律，一定会忘）、AI 工具的搜索（搜不到跨 session 的内容）——都没用。

DevQuest 的做法：**不靠你记得去搜。MCP Server 后台运行，AI 回答时自动带着你踩过的坑。**

---

## 三个产品设计原则

### 知识不依赖记忆

你不需要记得"2026 年 4 月 15 号那场对话"。只需要碎片——"docker 容器启动报错"——双通道 RRF 混合检索（向量语义 + FTS5 关键词 + 环境匹配 + 时效衰减）就能定位到当时的上下文和解决方案。

### 知识会生长

同一个 bug 在不同 session 中反复出现时，DevQuest 不是再建一条新记录——它自动归并到已有条目：attempts 累计、solution 迭代为新方案、solution_version 递增、tech_stack 合并补充。经验不是静态快照，是活的。

### 知识会反馈

高频使用的经验在搜索排序中获得隐式 boost（最多 30% 额外权重），被标记"没用"的自动降权。越有用的经验越容易被找到。Rule-Maker 反思引擎每周分析问题模式，自动生成开发规则草案——但**不直接覆写**项目规则文件，而是输出到 `rules_suggestions.md` 等待人工确认。一条坏规则污染所有后续对话的风险，不值得冒。

---

## 关键产品决策

| # | 决策 | 为什么 |
| - | --- | ------ |
| 1 | **MCP 协议**，不用 REST | REST 每个服务 endpoint 命名和参数格式各不相同。MCP 统一了 tool discovery——Client 启动时自动发现所有工具。就像 USB-C 统一了接口 |
| 2 | **双通道 + RRF 融合**，不用纯向量 | 向量适合语义（"容器启动不了"），关键词适合精确匹配（"Dockerfile CMD"）。两路分数量纲不同不能直接加权——RRF 比的是排名，两路共识的文档自动胜出 |
| 3 | **ChromaDB 嵌入式**，不用 Pinecone | 个人工具不需要分布式集群。零运维，数据就在本地——和 SQLite 一样的哲学 |
| 4 | **Human-in-the-loop 规则注入** | LLM 反思结果只写到 `rules_suggestions.md`，用户手动确认后才合并。AI 起草建议，人确认——幻觉规则污染项目配置不可接受 |
| 5 | **语义去重**，不建重复记录 | 同类问题再次遇到时自动归并：attempts 累积、solution 迭代、技术栈补充。一条经验持续生长，而不是一堆重复卡片 |

---

## 检验：DevQuest → LineMind 技术复用

DevQuest 是 [LineMind](../LineMind/linemind/)（企业工单智能分析 Agent 系统）的**技术实验田**。在副项目上验证的技术方案，直接复用到主项目：

| 复用内容 | DevQuest（实验田） | LineMind（业务应用） |
|----------|---------------------|--------------------------|
| MCP Server 标准化 | 13 工具，JSON-RPC 2.0 stdio/HTTP | 12 工具，同一套模式 |
| ChromaDB + Embedding | 向量语义检索通道（阿里百炼） | RAG `search_solutions`，同方案 |
| 混合检索 | 语义 + FTS5 关键词双通道 RRF 融合 | 同架构思路，向量 + SQL 结构化联合查询 |
| 双通道消融实验 | RRF k=60 + 环境匹配 + 时效衰减 | RAG 消融实验：置信度提升 10.5% |
| 工具设计经验 | 总结出 description 怎么写 LLM 才选得准 | 12 工具全部遵循同一设计规范 |

---

## 产品演进路线

```
V1.0 MVP              V1.3 检索增强          V3.0 产品化            V3.1 飞书连接
   ↓                       ↓                     ↓                      ↓
 LLM提取+入库          查询改写+反馈闭环      Service分层+单测      团队知识推送
 双通道RRF检索         语义去重+经验生长     结构化录入(Skill)      飞书Bot查询入口
                                                                    周报自动推送
```

**V3.1（Phase 6 进行中）**—— DevQuest 从个人工具到团队记忆的桥：

1. **周报自动推送**：Rule-Maker 每周反思 → 飞书卡片推送摘要，无需手动打开。**已实现** ✅
2. **团队知识推送**：反思发现高频共性模式 → 飞书卡片推送到团队群。**已实现** ✅
3. **飞书经验文档整理**：对 DevQuest 说"整理这周 Docker 经验" → 自动搜经验库，格式化为结构化文档推送飞书卡片 → 复制到飞书 Doc 即成个人经验总结。**规划中**
4. **飞书机器人查询**：非 IDE 用户在飞书 @机器人 直接搜经验库。**主项目 LineMind 更适用**

> 飞书接入全部通过 Webhook URL，零依赖、零 OAuth、零 CLI——判断场景复杂度后的最简方案。

---

## 架构

```
┌─ 消费层 ──────────────────────────────────────────────────┐
│  Claude Code Skill         飞书 Bot（规划中）              │
│  自然语言 / 斜杠命令        自然语言查询                   │
└──────────────────────┬────────────────────────────────────┘
                       │ MCP Protocol (JSON-RPC 2.0)
┌──────────────────────┴────────────────────────────────────┐
│  MCP Server 层（跨平台通用）                                │
│  14 tools · Service 层解耦 · 任何 MCP Client 可用          │
│                                                            │
│  ┌──────────┬──────────┬──────────┬──────────┐            │
│  │ 经验检索  │ 数据摄入  │ 反馈闭环  │ 反思+维护 │            │
│  │ search   │ save     │ record   │ reflection│            │
│  │ list     │ ingest   │ feedback │ rebuild   │            │
│  │ dashboard│ extract  │          │ star      │            │
│  └──────────┴──────────┴──────────┴──────────┘            │
└──────────────────────┬────────────────────────────────────┘
                       │
┌──────────────────────┴────────────────────────────────────┐
│  Service 层（业务逻辑）                                     │
│  save_problem_service · record_feedback_service           │
└──────────────────────┬────────────────────────────────────┘
                       │
┌──────────────────────┴────────────────────────────────────┐
│  检索引擎 ── 双通道 RRF 融合                                │
│  向量(ChromaDB) + 关键词(FTS5) + 环境匹配 + 时效衰减        │
└──────────────────────┬────────────────────────────────────┘
                       │
┌──────────────────────┴────────────────────────────────────┐
│  存储层 — SQLite + ChromaDB · 零运维 · 数据在本地           │
└───────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

- Python 3.10+
- LLM API Key（通过 opencode.ai 等兼容 OpenAI 格式的 provider）
- 阿里百炼 API Key（Embedding）

### 一键安装（Windows）

```powershell
.\install.ps1
```

自动完成：创建 venv → pip 依赖 → `.env.example` 复制 → MCP Server 注册 → Skill 安装 → 权限配置。

### 手动安装（macOS/Linux）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入 API Key
```

在 `~/.claude/settings.json` 的 `mcpServers` 中添加 `devquest` 条目，指向 `.venv/bin/python` + `backend/mcp_server.py`。

---

## MCP 工具（14 个）

### 经验检索
| Tool | 说明 |
| --- | --- |
| `search_experience` | 查询改写 + 双通道 RRF 检索 + 环境匹配 + 时效衰减 |
| `list_problems` | 按项目/技术栈/评分/类型筛选 |
| `get_dashboard` | 统计摘要（类型分布、技术栈排名、评分分布） |

### 数据摄入
| Tool | 说明 |
| --- | --- |
| `save_problem` | **结构化录入**（推荐）：跳过 LLM 提取，自动分类/评分/去重/索引 |
| `extract_from_text` | LLM 从对话文本提取问题 |
| `ingest_sessions` | 从 Claude JSONL 自动摄入 |
| `ingest_status` | 摄入状态 |

### 反馈闭环
| Tool | 说明 |
| --- | --- |
| `record_feedback` | 标记经验有用/没用，影响排序权重 |

### 反思与推送
| Tool | 说明 |
| --- | --- |
| `run_reflection` | Rule-Maker：LLM 分析本周问题 → 生成规则草案 |
| `get_suggestions` | 查看待确认规则草案 |
| `push_feishu_weekly` | 推送本周经验摘要到飞书群（Webhook） |

### 维护
| Tool | 说明 |
| --- | --- |
| `rebuild_index` | 全量重建 ChromaDB + FTS5 双索引 |
| `generate_star` | STAR 面试故事生成 |
| `update_score` | 手动调整评分 |

---

## 项目结构

```
devquest/
├── backend/
│   ├── mcp_server.py         # MCP Server 入口（14 tools）
│   ├── services.py           # 业务逻辑层（save/feedback）
│   ├── extractor.py          # LLM 问题提取引擎 + 语义去重
│   ├── classifier.py         # 技术标签自动分类
│   ├── scorer.py             # 优先级评分
│   ├── vector_search.py      # 双通道检索 + 查询改写 + 环境匹配 + 反馈闭环
│   ├── star_gen.py           # STAR 故事生成
│   ├── session_ingestor.py   # Claude JSONL 自动摄入
│   ├── rule_maker.py         # Rule-Maker 反思引擎（Human-in-the-loop）
│   ├── models.py / database.py
├── tests/
│   ├── test_services.py      # save / feedback 单测
│   └── test_vector_search.py # RRF / rewrite 单测
├── scripts/
│   ├── eval_extractor.py     # 提取引擎评测
│   └── smoke_test.py         # 冒烟测试
├── skill/
│   └── SKILL.md              # Claude Code Skill
├── install.ps1 / install.sh  # 安装脚本
├── data/                     # SQLite + ChromaDB 持久化
├── AGENTS.md                 # AI 开发规范
└── CHANGELOG.md              # 版本记录
```

## 评测

提取引擎在 4 组真实对话样本（13 个人工标注问题）上：召回率 83% / 精确率 78% / 类型准确率 85%。

> 运行：`python scripts/eval_extractor.py` · 冒烟：`python scripts/smoke_test.py` · 单测：`python -m pytest tests/ -v`

## 技术栈

Python 3.10+ · MCP SDK · LangChain · DeepSeek · 阿里百炼 Embedding · ChromaDB · SQLite FTS5 · SQLAlchemy

## 许可

MIT
