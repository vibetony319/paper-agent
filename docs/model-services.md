# 模型服务与模型档案

本文说明 paper-agent 如何接入 OpenAI-compatible vLLM、管理多个模型档案，以及各功能对模型能力的要求。面向中文开发者；API 字段名保持与 OpenAPI 一致。

## 模型档案生命周期

```text
新增 → 测试能力 → 设为默认 → 启用调用 → 停用或逻辑删除
```

1. `POST /api/model-profiles` 新增档案。`api_key` 可选，本地未启用鉴权的 vLLM 可省略或写 `EMPTY`。
2. `POST /api/model-profiles/{id}/test` 探测三项能力并保存结果；只有已测试合格的档案才应参与对应任务。
3. `POST /api/model-profiles/{id}/default` 把某个启用档案设为默认。
4. 默认档案用于自动回退；会话内也可以显式选择其他档案。
5. `PATCH` 可停用档案，`DELETE` 是逻辑删除。删除后历史消息和构建记录仍保留各自的不含密钥模型快照。

## 能力要求

| 功能 | 必需能力 |
|---|---|
| 解释 / 翻译选区 | 基础对话 |
| 知识图谱构建 | 结构化输出 |
| 论文助手 | 结构化输出 + 工具调用 |

数据库档案在请求开始时检查能力；能力不足会在写入任何处理状态前返回 `409`。只读环境变量档案不执行能力门禁，直接尝试调用，由运行时错误兜底。

修改 `base_url`、`model_name` 或替换密钥会清空已保存的能力结果并递增修订号，需要重新测试。只改展示名、默认状态等展示字段不会清空能力结果。

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
    "is_default": true
  }'
```

响应包含 `id` 和 `revision`，不包含 `api_key`；密钥只以 `has_api_key` 和掩码形式表达。

测试能力、设默认、修改和删除都要求 `If-Match` 头携带当前修订号，避免并发覆盖：

```bash
curl -X POST http://127.0.0.1:8000/api/model-profiles/<profile-id>/test \
  -H "If-Match: 1"

curl -X POST http://127.0.0.1:8000/api/model-profiles/<profile-id>/default \
  -H "If-Match: 2"

curl -X PATCH http://127.0.0.1:8000/api/model-profiles/<profile-id> \
  -H "If-Match: 3" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

curl -X DELETE http://127.0.0.1:8000/api/model-profiles/<profile-id> \
  -H "If-Match: 4"
```

修订冲突返回 `409 revision_conflict`，应刷新后重试。档案正在被请求使用时删除会返回 `409 profile_in_use`。

## 调用方请求体

论文助手使用聊天框当前选中的模型，每次请求必须带新的 `request_id` 用于幂等与追溯：

```bash
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/agent/messages \
  -H "Content-Type: application/json" \
  -d '{
    "content": "这篇文章解决了什么问题？",
    "mode": "paper_only",
    "model_profile_id": "<profile-id>",
    "request_id": "8ef96cf9-75be-4ab9-97af-b4a93c3d4b12"
  }'
```

知识图谱构建同样传模型和请求标识；重复的已完成 `request_id` 直接重放，不再次调用模型：

```bash
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/graph/core \
  -H "Content-Type: application/json" \
  -d '{
    "model_profile_id": "<profile-id>",
    "request_id": "6d8e9554-c42a-4dd3-8b4e-76a9f37e76db"
  }'
```

会话内切换模型只需在下一条消息中使用不同的 `model_profile_id`；`conversation_id` 不变，不会清空历史或新建会话。每条助手消息和每个图谱阶段都会保存当次请求的不可变模型快照。

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
```

该档案使用稳定保留 ID `00000000-0000-0000-0000-000000000000`，不能编辑或删除；一旦存在启用的数据库档案，环境变量回退就不再解析。

## 部署边界

当前版本是本地单用户、单服务进程部署。密钥文件锁、请求幂等锁和模型使用租约都是进程内机制，多 worker 或多进程共享 SQLite 尚未得到保证。
