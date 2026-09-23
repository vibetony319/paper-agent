# 模型服务与模型档案

## OpenAI-compatible 接口兼容（2026-09-11）

Agent 最终回答是 Markdown，不使用 `response_format`，也不会被 Citation Guard 拦截或改写，内联引用只用于可选的页面跳转。档案页只检查服务地址的网络连通性，不调用模型生成内容。

实际工具调用使用 `parallel_tool_calls=False`。连接检查不验证 API 密钥、模型名称或对话能力；调用时由模型服务返回实际错误。

部分兼容服务会忽略 `parallel_tool_calls=False`，一次返回多个工具调用。运行时按返回顺序串行执行现有只读论文工具，在单次问答中累计最多执行 6 次；同批次重复调用 ID 或超预算请求会拒绝。历史消息包含完整的助手工具列表和逐一匹配的工具结果，引用范围仅来自实际执行所得证据，不丢弃额外调用来伪装成功。

本文说明 paper-agent 如何接入 OpenAI-compatible vLLM、管理多个模型档案，以及各功能对模型能力的要求。面向中文开发者；API 字段名保持与 OpenAPI 一致。

## 模型档案生命周期

```text
新增 → 可选的连接检查 → 设为默认 → 启用调用 → 停用或逻辑删除
```

1. `POST /api/model-profiles` 新增档案。`api_key` 可选，本地未启用鉴权的 vLLM 可省略或写 `EMPTY`。
2. `POST /api/model-profiles/{id}/test` 对服务地址发出最多等待 3 秒的 HEAD 请求，返回 HTTP 状态；任何 HTTP 响应都表示网络连通。检查不发送 API 密钥、不生成模型回答，也不修改档案。
3. `POST /api/model-profiles/{id}/default` 把某个启用档案设为默认。
4. 默认档案用于模型档案管理与 Agent 健康检查的默认解析；当前 Agent 与选区辅助请求仍必须显式携带 `model_profile_id`，不会悄悄改用默认档案。
5. `PATCH` 可停用档案，`DELETE` 是逻辑删除。删除后历史消息仍保留各自的不含密钥模型快照。

## 连接检查与实际调用

连接检查只确认服务地址有 HTTP 响应。HTTP 401、404 或 405 也表示地址可达，但不能证明密钥、模型名称或对话接口可用。论文助手与选区辅助不再要求事先完成能力测试，直接调用选定模型；具体能力不兼容时由实际请求报告错误。旧档案中已保存的能力字段保留兼容，但不再用于门禁或界面展示。

## 上下文长度与输出上限（2026-09-16）

档案可配置两个可选 token 字段：`context_length`（1,000–10,000,000）与 `max_output_tokens`（1–200,000；两者都填写时 `max_output_tokens` 必须小于 `context_length`）。

- **留空即不限制**：未配置 `context_length` 的档案不压缩、不截断，行为与之前完全一致；只配置 `max_output_tokens` 时仅下发输出上限，不做任何上下文管理。
- `max_output_tokens` 配置后作为 `max_tokens` 下发到该档案的全部请求（结构化、普通、流式、工具调用）；未配置时省略参数，保留提供方自己的输出上限。
- `context_length` 配置后启用 Agent 上下文压缩：有效预算为 `context_length − (max_output_tokens 或 8192 预留)`；请求估算（CJK ≈1 token/字、其余 ≈4 字符/token，另加 1000 token 工具定义开销）超过 0.75× 预算时，把系统提示与当前问题之间的会话历史折叠为一条摘要 system 消息，摘要由同一模型非流式生成；工具循环中途压缩会原样保留末尾的 assistant(tool_calls)+tool 消息对。摘要失败时降级为丢弃最旧完整对话轮的硬截断（至 0.9× 以下），不让整轮失败。
- 压缩与截断是请求组装层的瞬态行为：不落库、不进入模型快照、不影响 `request_id` 幂等重放。设计取舍见 [ADR 0004](adr/0004-context-compaction.md)。
- **上下文占用指示**：前端对话区右下角显示"下一次请求"的估算占用（与压缩同一估算器与预算口径）。每轮回答通过非流式响应的 `context_usage` 字段或流式 `completed` 事件返回；`GET /api/papers/{paper_id}/agent/conversations/{conversation_id}/context-usage?model_profile_id=...` 供会话建立或切换档案时刷新。未配置 `context_length` 的档案只报 `used_tokens`，上限字段为 null。
- 修改两个 token 字段会递增修订号，使客户端缓存重建。

## 档案 API 示例

新增档案：

```bash
curl -X POST http://127.0.0.1:8000/api/model-profiles \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "本地 Qwen",
    "base_url": "http://127.0.0.1:8001/v1",
    "model_name": "Qwen3-32B",
    "api_key": "EMPTY",
    "enabled": true,
    "is_default": true,
    "context_length": 131072,
    "max_output_tokens": 8192
  }'
```

响应包含 `id` 和 `revision`，不包含 `api_key`；密钥只以 `has_api_key` 和掩码形式表达。`context_length` 与 `max_output_tokens` 可省略或置 `null` 表示不限制；`PATCH` 中显式 `null` 清除、省略字段保持不变。

连接检查、设默认、修改和删除都要求 `If-Match` 头携带当前修订号，避免使用过期档案：

```bash
curl -X POST http://127.0.0.1:8000/api/model-profiles/<profile-id>/test \
  -H "If-Match: 1"

curl -X POST http://127.0.0.1:8000/api/model-profiles/<profile-id>/default \
  -H "If-Match: 1"

curl -X PATCH http://127.0.0.1:8000/api/model-profiles/<profile-id> \
  -H "If-Match: 2" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

curl -X DELETE http://127.0.0.1:8000/api/model-profiles/<profile-id> \
  -H "If-Match: 3"
```

修订冲突返回 `409 revision_conflict`，应刷新后重试。档案正在被请求使用时删除会返回 `409 profile_in_use`。

## 调用方请求体

论文助手使用聊天框当前选中的模型，每次请求必须带新的 `request_id` 用于幂等与追溯：

```bash
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/agent/messages \
  -H "Content-Type: application/json" \
  -d '{
    "content": "这篇文章解决了什么问题？",
    "model_profile_id": "<profile-id>",
    "request_id": "8ef96cf9-75be-4ab9-97af-b4a93c3d4b12"
  }'
```

前端使用流式端点，逐段渲染回答；重复的已完成 `request_id` 只重放已保存的回合，不再次调用模型：

```bash
curl -N -X POST http://127.0.0.1:8000/api/papers/<paper-id>/agent/messages/stream \
  -H "Content-Type: application/json" \
  -d '{
    "content": "这篇文章解决了什么问题？",
    "model_profile_id": "<profile-id>",
    "request_id": "8ef96cf9-75be-4ab9-97af-b4a93c3d4b12"
  }'
```

会话内切换模型只需在下一条消息中使用不同的 `model_profile_id`；`conversation_id` 不变，不会清空历史或新建会话。每条助手消息、AI 笔记与选区辅助请求都会保存当次请求的不可变模型快照。

`ReasoningClientProvider` 按档案 ID 和 revision 解析 chat、structured、tool 三类客户端；缓存键同样包含 revision。因此档案更新不会改写正在进行或历史请求的来源。请求期间 `ModelProfileService.usage_lease()` 阻止删除正在使用的可写档案；删除返回 `409 profile_in_use` 时应稍后再试。

## 密钥与快照安全

- 密钥只保存在数据目录下的 `secrets/model-profiles.json`，Posix 创建时权限为 `0600`；Windows 下取决于当前账户 ACL。
- 密钥不进入 SQLite 普通字段、API 响应、模型快照、缓存键、日志或异常文本。
- 密钥文件对运行账户和本机管理员仍可读，因此不要在共享机器上写入高价值密钥。
- 模型快照只包含 `profile_id`、展示名、base URL、模型名和修订号五个公开字段。

## 环境变量只读回退

未启用任何数据库档案时，服务可以用以下环境变量提供只读兼容档案：

```powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8001/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
$env:PAPER_AGENT_REASONING_CONTEXT_LENGTH = "131072"
$env:PAPER_AGENT_REASONING_MAX_OUTPUT_TOKENS = "8192"
```

后两个变量可选（默认不限制），必须是不小于 1 的整数，否则启动报配置错误。该档案使用稳定保留 ID `00000000-0000-0000-0000-000000000000`，不能编辑或删除；一旦存在启用的数据库档案，环境变量回退就不再解析。它仍须由调用方作为 `model_profile_id` 显式提交；不构成按请求自动回退。

## 部署边界

当前版本是本地单用户、单服务进程部署。密钥文件锁、请求幂等锁和模型使用租约都是进程内机制，多 worker 或多进程共享 SQLite 尚未得到保证。

接口的完整错误码、`If-Match` 语义、schema 与请求重放规则见 [HTTP API 业务语义](api.md)；本地启动、能力诊断和测试 fake 注入见 [本地开发](development.md)。
