# 面试问答 — DevQuest

面试前 10 分钟翻一遍，脑子里有框架。不是给面试官看的，是给你自己看的。

---

## 掌握主动权：3 分钟项目介绍

你往哪个方向讲，面试官就往哪个方向问。用这 3 分钟把话题引到你准备好的三个点上。

> "DevQuest 是一个基于 MCP 协议的开发者'外脑' Agent。它的核心场景是：我每天用 Claude 写代码，对话里积累了大量踩坑经验，但这些经验对话结束就丢了。DevQuest 自动从 Claude 的 JSONL 对话文件中提取技术问题，用双通道混合检索做向量化存储，然后通过 MCP Server 把经验库无缝接回 Claude Desktop——下次遇到类似问题直接问 Claude 就能搜到历史方案。V1.2 我加了一个 Rule-Maker 模块，用 LLM 对本周的问题做反思，自动提炼开发规则草案，人工确认后注入 .cursorrules。架构上是 MCP Server → LangChain → SQLite + ChromaDB 双存储，检索用 RRF 融合算法。"

**你主动埋了三个钩子**：MCP 协议、双通道检索、Rule-Maker。面试官大概率挑一个追问——你准备好了。

---

## 必问 3 题 + 回答框架

### Q1：「为什么用 MCP，不用 REST API + function calling？」

**30 秒回答**：

"MCP 解决的是标准化问题。REST API 每个服务有自己的 endpoint 命名和参数格式，Claude 要记住 5 套不同的规范。MCP 统一了 tool 的 name/description/inputSchema，Client 端启动时自动发现所有工具——就像 USB-C 统一了接口，插上就能用。而且 MCP 不只是 tools，还定义了 Resources 和 Prompts，语义比 function calling 更完整。"

---

### Q2：「双通道检索里，向量和关键词结果冲突了怎么决定权重？」

**30 秒回答**：

"用 RRF——Reciprocal Rank Fusion。核心思路是不直接比较两路的分数，因为向量距离和 BM25 分数的量纲完全不同。RRF 比的是排名：`1/(k+rank)`，k 取 60。两路都排前 3 的文档天然比单路排第 1 的分数高——所以两路共识的文档自动胜出。我的场景里，关键词通道精确匹配 API 名称更准，向量通道语义相似搜索更准，RRF 让两者互补而不是互斥。"

**如果追问「能调权重吗」**：可以在公式前加乘数，但我不建议——加了一个需要维护的超参，默认 RRF 已经够好。

---

### Q3：「Rule-Maker 怎么保证生成的规则不污染项目？」

**30 秒回答**：

"我设计了 Human-in-the-loop：LLM 反思结果只写到 `cursorrules_suggestions.md`，从不直接覆写 `.cursorrules`。每条规则草案标注来源——基于哪几个 problem 推导出来的——用户手动 review 后确认合并。.cursorrules 每次对话都会注入，一条坏规则会持续污染所有后续对话，这个风险不值得冒。自动反思 + 人工确认，两个机制各司其职。"

---

### Q4（加分题）：「从 V1.0 到 V1.2 架构怎么演进的？」

**30 秒回答**：

"三步。V1.0 跑通核心链路——FastAPI + Streamlit，手动上传对话，证明想法可行。V1.1 解决手动上传太麻烦——session_ingestor 直接从 Claude JSONL 零操作摄入。V1.2 定位收敛——意识到 Streamlit 是过渡方案，MCP 化之后 Claude Desktop 就是前端；周报是功能蔓延，砍掉；Rule-Maker 才是真正有壁垒的功能。每一步都基于上一步的认知调整方向，不是堆需求。"

---

## 大概率不会问，你不用怕

| 担心 | 实际情况 |
|------|----------|
| 「ChromaDB 的 HNSW 索引参数怎么调的？」 | 不会问。向量库只是你工具箱里的一项，不是你的研究方向 |
| 「DeepSeek 和 GPT-4 的内部差异？」 | 不会问。你选 DeepSeek 是因为 OpenAI 兼容 + 性价比，这就够了 |
| 「MCP 协议的底层是 JSON-RPC 还是 gRPC？」 | 大概率没人懂。真问了就说 JSON-RPC 2.0 over stdio，标准化了 tools/list 和 tools/call |
| 「SQLite FTS5 的 BM25 公式？」 | 不会问。你理解它能做精确关键词匹配、跟向量互补就够了 |
| 「为什么不用 Pinecone / Weaviate？」 | 可能会问，但很好答——个人工具不需要集群，ChromaDB 嵌入式零运维 |

---

## 核心原则

你已经跑通了代码，你能解释为什么选 A 不选 B。这就超过了 80% 的候选人。

面试官不逐行看代码——他们听你讲 3 分钟，挑感兴趣的点追问。你掌握主动权。
