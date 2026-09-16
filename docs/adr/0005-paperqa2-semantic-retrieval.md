# ADR 0005：paperqa2 语义检索替换首末页固定投喂

## 状态

已接受

## 背景

论文助手此前对"概括类"问题（如"这篇论文讲了什么"）采用固定投喂策略：检测到 overview 关键词后，把论文的前三个元素和最后一个元素作为证据 system 消息直接注入请求。这有两个问题：

1. **投喂内容与问题无关**——前三个元素通常是标题、作者、摘要开头，不一定是研究问题、方法或结论所在；
2. **检索不可组合**——子串匹配对中文提问 ↔ 英文论文的跨语言场景完全失效，模型只能拿到被喂的片段，无法自己探索全文。

用户要求接入 paperqa2，让模型通过搜索自行获取相关内容。

## 决策

- **paperqa2 仅作为 `search_paper` 工具的语义检索后端**。对话循环、`[[元素ID]]` 引用契约、SSE 流式与上下文压缩全部保留不动；模型仍然只能通过工具拿到论文内容。
- 新增 [paper_search.py](../../src/paper_agent/services/paper_search.py) 的 `SemanticPaperSearchService`：对论文全部已解析元素文本（`order_index` 序）批量嵌入，查询向量与元素向量做余弦相似度 top-k。嵌入走 paperqa2 的本地栈——`paperqa.embedding_model_factory` 对 `st-` 前缀模型返回 `SentenceTransformerEmbeddingModel`（sentence-transformers，CPU，离线推理），默认模型 `st-paraphrase-multilingual-MiniLM-L12-v2`（多语言，中文提问可命中英文论文），可用 `PAPER_AGENT_EMBEDDING_MODEL` 覆盖。
- **索引持久化与惰性重建**：向量矩阵存 `.paper-agent/search-indexes/{paper_id}.npz`，JSON sidecar 记录元素数、内容指纹（元素 ID/序/全文的 SHA-256）与模型名。首次 `search` 时构建（首问多花数秒）；进程内 LRU 缓存（4 篇）。指纹或模型名不匹配（重新上传、stage1 重解析、换嵌入模型）即视为过期，下次搜索自动重建——上传流程不变，无需显式重索引。
- **优雅降级**：paperqa2/torch 不可用、嵌入失败或向量形状异常时记 warning 并返回空结果，`search_paper` 回退纯子串匹配；嵌入调用与索引构建由单把进程锁串行（sentence-transformers 的 encode 不是线程安全的）。
- **合并策略**（[agent_tools.py](../../src/paper_agent/services/agent_tools.py)）：子串命中优先（mode=`substring`、score=1.0），语义命中去重后补足（mode=`semantic`、score=相似度），按 `limit` 截断；每个元素载荷增加 `search_mode`/`search_score` 标记。`evidence_element_ids`（仅 located 元素）与跨论文隔离逻辑不变。
- **移除固定投喂**：删除 `agent_runtime` 的 overview 检测与 `elements[:3] + elements[-1:]` 播种块及 `_seeded_evidence_message`；工具循环恒为 `range(MAX_TOOL_TURNS)`。所有问题统一走"模型自己搜索→读→答"。系统提示与工具描述更新为鼓励多轮语义检索（含跨语言说明）。

## 后果

- 中文概括类问题可以命中英文论文的摘要/结论相关段落，且模型可对任意问题自行检索全文，不再依赖首末页样本。
- 首次对一篇论文提问会触发索引构建（CPU 嵌入，几千元素约数秒）；此后命中磁盘缓存，只有查询向量需要计算。
- `paper-qa[local]` 安装体积大（torch 约 2GB）；首次使用需从 HuggingFace 下载嵌入模型（约 100–500MB，之后离线）。国内可设 `HF_ENDPOINT=https://hf-mirror.com`。Windows 上安装可能撞上 MAX_PATH（litellm 的深层文件），见 README 的安装说明。
- 语义分数是余弦相似度（0–1），不是相关性真值；子串优先的合并保证确定性命中永远不被语义结果挤掉。
- 集成测试注入 substring-only 的 `PaperToolRegistry`，避免在 CI 中加载真实嵌入模型；语义服务本身用确定性假嵌入向量在 [test_paper_search.py](../../tests/unit/services/test_paper_search.py) 覆盖（建索引/复用/指纹失效/模型切换/失败回退）。

## 被否决方案

- **用 paperqa2 的 `Docs`/`ask` 管线整体替换对话循环**：会替换掉 `[[元素ID]]` 引用、SSE 流式、上下文压缩与幂等重放，且其 LLM 调用配置与本地多档案体系冲突。
- **保留首末页播种作为回退**：语义检索失败时已有子串回退；再加播种会让"模型读到的内容"存在三条来源路径，证据边界难以推理。
- **上传时同步建索引**：把嵌入耗时加进上传延迟，且 stage2 重解析后索引立即作废；惰性重建以首问延迟换取零无效工作。
- **直接依赖 sentence-transformers 绕过 paperqa2**：用户明确选择 paperqa2 作为检索后端；`st-` 前缀的模型命名与工厂来自 paperqa2，未来切换 hybrid/sparse 嵌入只需换模型名。
