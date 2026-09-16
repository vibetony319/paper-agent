# ADR 0004：Agent 上下文压缩

## 状态

已接受

## 背景

论文助手的请求由系统提示、笔记块、最近 6 条会话历史和工具循环（≤6 轮 `read_section`/`search_paper`，单轮可返回大量论文文本）组成。模型档案此前没有任何 token 相关配置：请求大小不受约束，长会话或大段落工具结果可能超过提供方的上下文窗口，导致请求被提供方直接拒绝。参考 Codex/DeepSeek harness 的做法（预留输出空间、按阈值主动压缩、失败时降级截断），本项目需要一套自己的适配实现。已实现边界见[模型服务与模型档案](../model-services.md)。

## 决策

- 模型档案增加两个可空字段：`context_length`（1,000–10,000,000）与 `max_output_tokens`（1–200,000；都填写时必须小于 `context_length`）。**留空即不限制**：未配置 `context_length` 的档案完全不压缩、不截断、不下发 `max_tokens`，行为与之前一致。字段不进入 `ModelSnapshot`，也不属于会清空能力探测的 material configuration。
- `max_output_tokens` 配置后作为 `max_tokens` 下发到该档案的全部 provider 请求（结构化、普通、流式与工具调用）；未配置时省略该参数，保留提供方自己的输出上限。
- 预算计算在 [context_budget.py](../../src/paper_agent/services/context_budget.py)：`effective_limit = context_length − (max_output_tokens 或 8192 预留)`；token 估算不用 tokenizer 依赖——CJK 记 1 token/字、其余记 4 字符/token，并固定加 1000 token 工具定义开销。阈值 0.75 触发压缩，0.9 是硬截断上限。
- 压缩发生在请求组装层（`agent_runtime` 的工具循环每轮 `request_tool_turn` 前 + 最终回答派发前）：把 head 系统块与当前用户消息之间的历史折叠为一条摘要 system 消息，摘要由同一 chat client 非流式生成，中文提示词要求保留论文主题、已确认事实、证据元素 ID 和当前任务。工具循环中途压缩时，末尾的 assistant(tool_calls)+tool 消息对必须原样保留（OpenAI API 配对约束）。
- 摘要调用失败不失败整轮：降级为丢弃最旧完整对话轮的硬截断，直到低于 0.9×；摘要与截断都是瞬态行为，不落库、不进入模型快照、不影响 `request_id` 幂等重放。
- 前端模型设置对话框提供两个可选数字输入（留空占位符提示"不限制"），校验正整数与"输出 < 上下文"；编辑表单总是显式提交两字段，空值即清除。

## 后果

- 配置了 `context_length` 的档案在接近窗口上限前会先丢历史细节而不是整轮失败；未配置的档案零行为变化。
- 估算是启发式的：与真实 tokenizer 有偏差，阈值与预留常量吸收误差；需要精确计费时应在提供方侧核对。
- 摘要质量依赖同一模型的非流式调用，本身消耗一次请求；触发点在工具循环内，一次问答最多压缩数次（每轮检查）。
- 工具循环中段压缩要求消息分区保持 tool 配对完整，`partition_messages` 的尾部回溯必须把 assistant(tool_calls) 一并划入 tail（否则摘要会拆散配对、产生孤儿 tool 消息被 API 拒绝）。

## 被否决方案

- **引入 tiktoken 精确计数**：需要模型特定的 tokenizer 依赖与下载，超出本地单用户部署的依赖边界，收益只是阈值精度。
- **把压缩摘要持久化为会话消息**：会改写 durable 历史，破坏幂等重放与消息序列语义；压缩只影响单次请求组装。
- **在 provider 客户端层按 token 硬截断消息**：截断点在消息边界之外，无法保留系统提示与当前问题，丢弃的内容也不可追溯。
