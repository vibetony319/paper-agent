# 多模型档案与调用追溯 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将启动时固定的单一推理模型升级为可管理的多个 vLLM 模型档案，并让 Agent、知识图谱及历史记录准确追溯每次调用所用模型。

**Architecture:** SQLite 保存非敏感模型档案，`ModelSecretStore` 在应用数据目录单独保存 API 密钥；`ReasoningClientProvider` 按 `(profile_id, revision)` 解析并缓存不可变客户端。路由在请求开始时冻结模型快照，再把具体客户端和快照传给现有 Agent/图谱服务。

**Tech Stack:** Python 3.12、FastAPI 0.115、Pydantic 2、SQLAlchemy 2、OpenAI Python SDK、SQLite、pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 仅支持 OpenAI-compatible vLLM，不增加多供应商 Gateway。
- 当前版本是单用户本地部署，不增加用户、登录或权限表。
- API 密钥不得出现在 SQLite 普通字段、响应、日志、模型快照或 Git 中。
- 保留 `PAPER_AGENT_REASONING_*` 环境变量作为只读兼容模型。
- 模型切换不新建会话；每次请求在开始时冻结模型配置。
- Agent 的论文事实继续经过 Citation Guard；不得流式展示未经校验的论文回答。
- Python 必须为 3.12 或更高版本。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## File Map

**Create**

- `src/paper_agent/migrations.py`：轻量顺序迁移运行器与本计划所需 schema 迁移。
- `src/paper_agent/model_profiles.py`：模型档案、能力和快照领域类型。
- `src/paper_agent/model_profile_storage.py`：模型档案持久化。
- `src/paper_agent/services/model_secrets.py`：原子读写后端密钥文件。
- `src/paper_agent/services/model_profiles.py`：档案业务规则、环境变量兼容和能力检测。
- `src/paper_agent/services/reasoning_clients.py`：按档案解析 vLLM 客户端和有界缓存。
- `src/paper_agent/routes/model_profiles.py`：模型档案 HTTP API。
- `tests/unit/test_migrations.py`
- `tests/unit/test_model_profile_storage.py`
- `tests/unit/services/test_model_secrets.py`
- `tests/unit/services/test_model_profiles.py`
- `tests/unit/services/test_reasoning_clients.py`
- `tests/integration/test_model_profiles_api.py`
- `docs/model-services.md`

**Modify**

- `src/paper_agent/database.py`
- `src/paper_agent/config.py`
- `src/paper_agent/domain.py`
- `src/paper_agent/models/vllm.py`
- `src/paper_agent/models/__init__.py`
- `src/paper_agent/schemas.py`
- `src/paper_agent/storage.py`
- `src/paper_agent/app.py`
- `src/paper_agent/routes/__init__.py`
- `src/paper_agent/routes/agent.py`
- `src/paper_agent/routes/graph.py`
- `src/paper_agent/services/agent_runtime.py`
- `src/paper_agent/services/graph_construction.py`
- `tests/unit/models/test_vllm.py`
- `tests/unit/models/test_vllm_tools.py`
- `tests/unit/services/test_agent_runtime.py`
- `tests/unit/services/test_graph_construction.py`
- `tests/integration/test_agent_api.py`
- `tests/integration/test_graph_api.py`
- `.gitignore`
- `.env.example`

---

### Task 1: 引入版本化数据库迁移和模型追溯列

**Files:**
- Create: `src/paper_agent/migrations.py`
- Modify: `src/paper_agent/database.py`
- Test: `tests/unit/test_migrations.py`

**Interfaces:**
- Produces: `run_schema_migrations(engine: Engine) -> None`
- Produces tables: `schema_migrations`, `model_profiles`
- Adds nullable columns to `conversation_messages`: `model_profile_id`, `model_snapshot_json`, `request_id`
- Adds nullable columns to `processing_runs`: `model_profile_id`, `model_snapshot_json`, `request_id`

- [ ] **Step 1: 写迁移失败测试**

```python
def test_model_profile_migration_upgrades_an_existing_database(tmp_path):
    database_url = database_url_for(tmp_path)
    engine = create_database_engine(database_url)
    legacy = MetaData()
    Table(
        "conversation_messages",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), nullable=False),
        Column("paper_id", String(36), nullable=False),
        Column("role", String(16), nullable=False),
        Column("content", String, nullable=False),
        Column("sequence", Integer, nullable=False),
    )
    legacy.create_all(engine)

    run_schema_migrations(engine)

    message_columns = {column[1] for column in engine.connect().exec_driver_sql(
        "PRAGMA table_info(conversation_messages)"
    )}
    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= message_columns
    assert engine.connect().exec_driver_sql(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).scalars().all() == [1]
```

再增加测试：连续执行两次迁移仍只有一个版本记录；新数据库存在 `model_profiles`；未删除默认模型只有一个。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/test_migrations.py -v`

Expected: FAIL，提示无法导入 `paper_agent.migrations`。

- [ ] **Step 3: 实现迁移运行器和表结构**

```python
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


def run_schema_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, applied_at VARCHAR NOT NULL)"
        )
        applied = set(connection.exec_driver_sql(
            "SELECT version FROM schema_migrations"
        ).scalars())
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            migration.apply(connection)
            connection.execute(
                text("INSERT INTO schema_migrations(version, name, applied_at) "
                     "VALUES (:version, :name, :applied_at)"),
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
```

在迁移 1 中用 `PRAGMA table_info` 判定列是否存在后再执行 `ALTER TABLE`。在 `database.py` 声明 `model_profiles`，字段固定为：`id`、`display_name`、`base_url`、`model_name`、`secret_ref`、`enabled`、`is_default`、`revision`、三个能力布尔值、`capabilities_checked_at`、`created_at`、`updated_at`、`deleted_at`。建立部分唯一索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS model_profiles_one_default
ON model_profiles(is_default)
WHERE is_default = 1 AND deleted_at IS NULL;
```

`initialize_database()` 的顺序固定为：创建 metadata 中的新表 → 执行现有 legacy 修复 → 执行 `run_schema_migrations()`。

- [ ] **Step 4: 运行迁移和现有存储测试**

Run: `python -m pytest tests/unit/test_migrations.py tests/unit/test_storage.py -v`

Expected: PASS，现有存储回归不变。

- [ ] **Step 5: 提交迁移基础**

```bash
git add src/paper_agent/migrations.py src/paper_agent/database.py tests/unit/test_migrations.py
git commit -m "feat: add versioned model profile migrations"
```

---

### Task 2: 实现模型档案领域对象、存储和密钥文件

**Files:**
- Create: `src/paper_agent/model_profiles.py`
- Create: `src/paper_agent/model_profile_storage.py`
- Create: `src/paper_agent/services/model_secrets.py`
- Modify: `src/paper_agent/config.py`
- Modify: `.gitignore`
- Test: `tests/unit/test_model_profile_storage.py`
- Test: `tests/unit/services/test_model_secrets.py`

**Interfaces:**
- Produces: `ModelProfile`, `ModelCapabilities`, `ModelSnapshot`
- Produces: `ModelProfileRepository`
- Produces: `ModelSecretStore.set(profile_id: str, secret: str) -> str`
- Produces: `ModelSecretStore.get(secret_ref: str | None) -> str`
- Produces: `ModelSecretStore.delete(secret_ref: str | None) -> None`
- Modifies: `Settings` with `model_secrets_path: Path` and existing `reasoning_model` fallback intact

- [ ] **Step 1: 写领域与密钥存储失败测试**

```python
def test_secret_store_round_trips_without_exposing_secret_in_reference(tmp_path):
    store = ModelSecretStore(tmp_path / "secrets" / "model-profiles.json")
    reference = store.set("profile-a", "top-secret")

    assert reference == "model-profile:profile-a"
    assert store.get(reference) == "top-secret"
    assert "top-secret" not in reference


def test_soft_delete_removes_profile_from_default_listing(repository, secret_store):
    profile = repository.create(_profile(display_name="本地 Qwen", is_default=True))
    secret_store.set(profile.id, "key-a")

    deleted = repository.soft_delete(profile.id, expected_revision=profile.revision)

    assert deleted.deleted_at is not None
    assert repository.list_active() == ()
```

覆盖：名称和 URL 去空格、空模型名拒绝、默认模型唯一、修订冲突、列表不返回逻辑删除项、快照不含 secret。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/test_model_profile_storage.py tests/unit/services/test_model_secrets.py -v`

Expected: FAIL，缺少模型档案模块。

- [ ] **Step 3: 实现领域类型与 repository**

```python
@dataclass(frozen=True)
class ModelCapabilities:
    basic_chat: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    checked_at: datetime | None = None


@dataclass(frozen=True)
class ModelProfile:
    display_name: str
    base_url: str
    model_name: str
    enabled: bool = True
    is_default: bool = False
    revision: int = 1
    secret_ref: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ModelSnapshot:
    profile_id: str
    display_name: str
    base_url: str
    model_name: str
    revision: int


@dataclass(frozen=True)
class Unchanged:
    pass


UNCHANGED = Unchanged()


@dataclass(frozen=True)
class ModelProfileChanges:
    display_name: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    secret_ref: str | None | Unchanged = UNCHANGED
```

`ModelProfileRepository` 实现 `create(profile)`、`get(profile_id)`、`list_active()`、`update(profile_id, *, expected_revision, changes)`、`set_default(profile_id, *, expected_revision)`、`soft_delete(profile_id, *, expected_revision)`。更新使用 `WHERE id = :id AND revision = :expected_revision`，受影响行数不是 1 时抛出 `ModelProfileRevisionError`。逻辑删除清空默认状态并递增修订号。

- [ ] **Step 4: 实现原子密钥文件**

```python
def set(self, profile_id: str, secret: str) -> str:
    secrets = self._read_all()
    secrets[profile_id] = secret
    self.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = self.path.with_suffix(".tmp")
    temporary.write_text(json.dumps(secrets), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(self.path)
    return f"model-profile:{profile_id}"
```

`get()` 只接受 `model-profile:<uuid>` 格式；文件损坏时抛出不含原始内容的 `ModelSecretStoreError`。`delete()` 原子重写文件。把 `.paper-agent/` 与 `*.tmp` 保持在 `.gitignore`，但不要忽略 `.env.example`。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_model_profile_storage.py tests/unit/services/test_model_secrets.py -v`

Expected: PASS。

- [ ] **Step 6: 提交档案与密钥存储**

```bash
git add src/paper_agent/model_profiles.py src/paper_agent/model_profile_storage.py src/paper_agent/services/model_secrets.py src/paper_agent/config.py tests/unit/test_model_profile_storage.py tests/unit/services/test_model_secrets.py .gitignore
git commit -m "feat: persist vllm model profiles securely"
```

---

### Task 3: 增加文本流客户端、能力检测和动态客户端提供器

**Files:**
- Modify: `src/paper_agent/models/vllm.py`
- Modify: `src/paper_agent/models/__init__.py`
- Create: `src/paper_agent/services/reasoning_clients.py`
- Create: `src/paper_agent/services/model_profiles.py`
- Test: `tests/unit/models/test_vllm.py`
- Test: `tests/unit/models/test_vllm_tools.py`
- Test: `tests/unit/services/test_reasoning_clients.py`
- Test: `tests/unit/services/test_model_profiles.py`

**Interfaces:**
- Produces: `VllmChatClient.complete(messages: list[dict[str, str]]) -> str`
- Produces: `VllmChatClient.stream_text(messages: list[dict[str, str]]) -> Iterator[str]`
- Produces: `ResolvedReasoningClients(profile, snapshot, chat, structured, tools)`
- Produces: `ReasoningClientProvider.resolve(profile_id: str) -> ResolvedReasoningClients`
- Produces: `ModelProfileService.test_capabilities(profile_id: str) -> ModelCapabilities`

- [ ] **Step 1: 写客户端与缓存失败测试**

```python
def test_provider_caches_by_profile_revision(repository, secrets, client_factory):
    profile = repository.create(_profile(revision=1))
    provider = ReasoningClientProvider(repository, secrets, client_factory=client_factory)

    first = provider.resolve(profile.id)
    second = provider.resolve(profile.id)

    assert first is second
    updated = repository.update(profile.id, expected_revision=1, display_name="新名称")
    third = provider.resolve(updated.id)
    assert third is not first
    assert third.snapshot.revision == 2


def test_chat_stream_rejects_non_text_deltas(fake_openai):
    fake_openai.stream_chunks = [object()]
    client = VllmChatClient(_config(), client=fake_openai)
    with pytest.raises(VllmResponseError, match="could not stream text"):
        list(client.stream_text([{"role": "user", "content": "解释"}]))
```

能力测试覆盖三个调用互相独立：基础对话失败不再测试后两项；结构化输出失败不阻止工具测试结果被记录；异常文本不含 provider 原始 payload。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/models/test_vllm.py tests/unit/models/test_vllm_tools.py tests/unit/services/test_reasoning_clients.py tests/unit/services/test_model_profiles.py -v`

Expected: FAIL，缺少 `VllmChatClient` 和 provider。

- [ ] **Step 3: 实现普通文本客户端**

```python
class VllmChatClient:
    def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=0,
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise TypeError
            return content
        except Exception:
            raise VllmResponseError("vLLM could not complete text generation.") from None

    def stream_text(self, messages: list[dict[str, str]]) -> Iterator[str]:
        try:
            stream = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=0,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta is None:
                    continue
                if not isinstance(delta, str):
                    raise TypeError
                yield delta
        except Exception:
            raise VllmResponseError("vLLM could not stream text generation.") from None
```

- [ ] **Step 4: 实现 provider 和能力检测**

provider 返回类型固定为：

```python
@dataclass(frozen=True)
class ResolvedReasoningClients:
    profile: ModelProfile
    snapshot: ModelSnapshot
    chat: VllmChatClient
    structured: VllmStructuredClient
    tools: VllmToolCallingClient
```

`ReasoningClientProvider.resolve()`：读取活动档案 → 读取 secret 或使用 `EMPTY` → 构造 `VllmModelConfig` → 创建三种客户端 → 生成无密钥 `ModelSnapshot` → 放入最多 16 项的 LRU 缓存。档案不存在、停用或逻辑删除分别抛出稳定领域异常。

`ModelProfileService.test_capabilities()` 使用以下固定探测：基础请求返回非空 `OK`；结构化请求 schema 只允许 `{"status": "ok"}`；工具请求复用 `validate_tool_calling()`。保存三个布尔值和检测时间，并递增档案修订号。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/models tests/unit/services/test_reasoning_clients.py tests/unit/services/test_model_profiles.py -v`

Expected: PASS。

- [ ] **Step 6: 提交动态客户端**

```bash
git add src/paper_agent/models src/paper_agent/services/reasoning_clients.py src/paper_agent/services/model_profiles.py tests/unit/models tests/unit/services/test_reasoning_clients.py tests/unit/services/test_model_profiles.py
git commit -m "feat: resolve reasoning clients per model profile"
```

---

### Task 4: 提供模型档案 HTTP API 和应用装配

**Files:**
- Create: `src/paper_agent/routes/model_profiles.py`
- Modify: `src/paper_agent/routes/__init__.py`
- Modify: `src/paper_agent/schemas.py`
- Modify: `src/paper_agent/app.py`
- Test: `tests/integration/test_model_profiles_api.py`

**Interfaces:**
- Produces routes: `GET/POST/PATCH/DELETE /api/model-profiles`
- Produces routes: `POST /api/model-profiles/{id}/test` and `/default`
- Produces stable response fields: `has_api_key`, `api_key_mask`, capabilities, revision; never `api_key`

- [ ] **Step 1: 写 API 失败测试**

```python
def test_model_profile_crud_masks_secret_and_soft_deletes(client):
    created = client.post("/api/model-profiles", json={
        "display_name": "本地 Qwen",
        "base_url": "http://127.0.0.1:8001/v1",
        "model_name": "Qwen3-32B",
        "api_key": "secret-value",
        "is_default": True,
    })
    assert created.status_code == 201
    payload = created.json()
    assert payload["has_api_key"] is True
    assert payload["api_key_mask"] == "••••••••"
    assert "api_key" not in payload

    deleted = client.delete(
        f"/api/model-profiles/{payload['id']}",
        headers={"If-Match": str(payload["revision"])},
    )
    assert deleted.status_code == 204
    assert client.get("/api/model-profiles").json() == []
```

增加 422 URL/名称校验、409 修订冲突、默认模型切换、能力测试 fake client、响应全文不含 secret 的测试。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/integration/test_model_profiles_api.py -v`

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现 schema 和路由**

```python
class ModelProfileCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: HttpUrl
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    is_default: bool = False


class ModelProfileResponse(BaseModel):
    id: UUID
    display_name: str
    base_url: str
    model_name: str
    enabled: bool
    is_default: bool
    revision: int
    has_api_key: bool
    api_key_mask: str | None
    capabilities: ModelCapabilitiesResponse
```

PATCH 使用全部可选字段并要求 `If-Match` 修订号。删除活动默认档案时，若仍有其他启用档案，服务先选择最早创建项为默认；否则进入“无模型”状态。异常统一映射为带 `code` 与中文 `detail` 的安全响应。

- [ ] **Step 4: 在应用中装配服务**

`create_app()` 创建 `ModelProfileRepository`、`ModelSecretStore`、`ModelProfileService` 和 `ReasoningClientProvider`，放入 `app.state`，注册 `model_profiles_router`。现有环境变量配置作为 service 的只读 fallback，不再直接在 `app.py` 创建 Agent/图谱客户端。

- [ ] **Step 5: 运行模型 API 与 OpenAPI 测试**

Run: `python -m pytest tests/integration/test_model_profiles_api.py tests/integration/test_health_api.py -v`

Expected: PASS；OpenAPI 响应模型中不存在可写回的密钥字段。

- [ ] **Step 6: 提交模型 API**

```bash
git add src/paper_agent/routes/model_profiles.py src/paper_agent/routes/__init__.py src/paper_agent/schemas.py src/paper_agent/app.py tests/integration/test_model_profiles_api.py
git commit -m "feat: expose local model profile settings api"
```

---

### Task 5: 让 Agent 按请求选择模型并保存快照

**Files:**
- Modify: `src/paper_agent/domain.py`
- Modify: `src/paper_agent/storage.py`
- Modify: `src/paper_agent/services/agent_runtime.py`
- Modify: `src/paper_agent/routes/agent.py`
- Modify: `src/paper_agent/schemas.py`
- Modify: `src/paper_agent/app.py`
- Test: `tests/unit/test_storage.py`
- Test: `tests/unit/services/test_agent_runtime.py`
- Test: `tests/integration/test_agent_api.py`

**Interfaces:**
- `AgentMessageRequest` requires `model_profile_id: UUID` and `request_id: UUID`
- `PaperAgentRuntime.ask(*, paper_id: str, question: AgentQuestion, client: VllmToolCallingClient, model_snapshot: ModelSnapshot, request_id: str) -> AgentTurn`
- `ConversationMessage` gains nullable `model_profile_id`, `model_snapshot`, `request_id`
- Agent response and conversation history expose `model: ModelSnapshotResponse | None`

- [ ] **Step 1: 写会话内切换模型失败测试**

```python
def test_same_conversation_can_continue_with_a_different_model(client, configured_models, uploaded_paper):
    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={
            "content": "方法是什么？",
            "mode": "paper_only",
            "model_profile_id": configured_models.qwen.id,
            "request_id": str(uuid4()),
        },
    ).json()
    second = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={
            "content": "继续解释。",
            "mode": "paper_only",
            "conversation_id": first["conversation_id"],
            "model_profile_id": configured_models.deepseek.id,
            "request_id": str(uuid4()),
        },
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{first['conversation_id']}"
    ).json()
    assert [message["model"]["profile_id"] for message in history["messages"]] == [
        configured_models.qwen.id,
        configured_models.qwen.id,
        configured_models.deepseek.id,
        configured_models.deepseek.id,
    ]
```

增加：重复 `request_id` 返回同一助手消息、不再次调用模型；快照 JSON 无密钥；禁用模型在写入用户消息前返回 409/503；旧消息模型为空仍可读取。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_agent_runtime.py tests/integration/test_agent_api.py -v`

Expected: FAIL，请求 schema 不识别 `model_profile_id` 或消息无模型字段。

- [ ] **Step 3: 修改 runtime 的依赖边界**

从 `PaperAgentRuntime.__init__` 删除固定 `client`。将签名改为：

```python
def ask(
    self,
    *,
    paper_id: str,
    question: AgentQuestion,
    client: VllmToolCallingClient,
    model_snapshot: ModelSnapshot,
    request_id: str,
) -> AgentTurn:
```

路由先调用 provider `resolve(payload.model_profile_id)`，再调用 runtime。创建用户和助手消息时都保存相同 `request_id` 与快照。`_preflight()` 不再判断固定 client，只校验论文和会话。历史消息只把 role/content 传给当前模型，切换模型不修改历史。

- [ ] **Step 4: 实现 Agent 幂等读取**

在 repository 增加：

```python
def get_agent_turn_by_request(
    self, paper_id: str, request_id: str
) -> tuple[ConversationMessage, ConversationMessage] | None:
```

请求开始先查已有成对消息；存在则重建响应，不调用 provider。只存在用户消息表示上次失败，允许在同一 conversation 下重试并补齐助手消息，但不得追加第二条用户消息。

- [ ] **Step 5: 运行 Agent 测试**

Run: `python -m pytest tests/unit/test_storage.py tests/unit/services/test_agent_runtime.py tests/integration/test_agent_api.py -v`

Expected: PASS，Citation Guard 现有测试全部保持通过。

- [ ] **Step 6: 提交 Agent 动态模型**

```bash
git add src/paper_agent/domain.py src/paper_agent/storage.py src/paper_agent/services/agent_runtime.py src/paper_agent/routes/agent.py src/paper_agent/schemas.py src/paper_agent/app.py tests/unit/test_storage.py tests/unit/services/test_agent_runtime.py tests/integration/test_agent_api.py
git commit -m "feat: track agent model per conversation turn"
```

---

### Task 6: 让知识图谱使用请求模型并保存构建快照

**Files:**
- Modify: `src/paper_agent/storage.py`
- Modify: `src/paper_agent/services/graph_construction.py`
- Modify: `src/paper_agent/routes/graph.py`
- Modify: `src/paper_agent/schemas.py`
- Test: `tests/unit/services/test_graph_construction.py`
- Test: `tests/integration/test_graph_api.py`

**Interfaces:**
- Produces: `GraphBuildRequest(model_profile_id: UUID, request_id: UUID)`
- `GraphConstructionService.build_core(paper_id, *, client, model_snapshot, request_id)`
- `GraphConstructionService.build_deep(paper_id, *, client, model_snapshot, request_id)`
- Processing rows expose model snapshot for the latest graph stage build

- [ ] **Step 1: 写图谱模型追溯失败测试**

```python
def test_graph_build_uses_requested_profile_and_records_snapshot(client, uploaded_paper, qwen_profile):
    response = client.post(
        f"/api/papers/{uploaded_paper.id}/graph/core",
        json={
            "model_profile_id": qwen_profile.id,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200
    summary = client.get(f"/api/papers/{uploaded_paper.id}").json()
    assert summary["stage2_model"]["profile_id"] == qwen_profile.id
```

增加：重复请求不重建；模型缺少结构化能力时不创建 queued row；核心重建使用新模型后深层阶段仍按现有规则失效；失败运行保留安全模型快照。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_graph_construction.py tests/integration/test_graph_api.py -v`

Expected: FAIL，图谱 POST 尚不接受请求体。

- [ ] **Step 3: 将客户端改为构建参数**

```python
def build_core(
    self,
    paper_id: str,
    *,
    client: VllmStructuredClient,
    model_snapshot: ModelSnapshot,
    request_id: str,
) -> PaperGraph:
    return self._build(
        paper_id,
        GraphStage.core,
        client=client,
        model_snapshot=model_snapshot,
        request_id=request_id,
    )
```

`_extract_nodes()` 和 `_extract_edges()` 接受本次 client，不读取 `self.client`。路由解析 provider 后传入结构化客户端。repository 写 queued/running/completed/failed processing row时都保存档案 ID、快照 JSON 与 request ID。

- [ ] **Step 4: 实现图谱幂等和摘要字段**

相同 `paper_id + stage + request_id` 已完成时返回当前对应阶段图谱；运行中返回 409；失败允许同 key 重试并新增 processing sequence。`PaperSummaryResponse` 增加 `stage2_model`、`stage3_model`，从各阶段最新持久运行解析。

- [ ] **Step 5: 运行图谱回归测试**

Run: `python -m pytest tests/unit/services/test_graph_construction.py tests/integration/test_graph_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交图谱动态模型**

```bash
git add src/paper_agent/storage.py src/paper_agent/services/graph_construction.py src/paper_agent/routes/graph.py src/paper_agent/schemas.py tests/unit/services/test_graph_construction.py tests/integration/test_graph_api.py
git commit -m "feat: record graph build model provenance"
```

---

### Task 7: 编写模型服务文档并运行完整后端回归

**Files:**
- Create: `docs/model-services.md`
- Create: `.env.example`
- Modify: `README.md`
- Test: all backend tests

**Interfaces:**
- Documents the exact model-profile fields, environment fallback, capability requirements, secret location and request examples.

- [ ] **Step 1: 写中文模型服务文档**

`docs/model-services.md` 必须包含：

```text
模型档案生命周期
  新增 → 测试能力 → 设为默认 → 启用调用 → 停用/逻辑删除

能力要求
  解释/翻译：基础对话
  知识图谱：结构化输出
  论文助手：结构化输出 + 工具调用
```

写明 secret 文件位置、权限边界、管理员可读风险、环境变量只读 fallback、会话内切换模型和快照不含密钥。提供新增档案、测试和 Agent/图谱请求的完整 curl 示例。

- [ ] **Step 2: 添加安全环境模板**

`.env.example` 只包含：

```dotenv
PAPER_AGENT_REASONING_BASE_URL=http://127.0.0.1:8001/v1
PAPER_AGENT_REASONING_MODEL=your-served-model
PAPER_AGENT_REASONING_API_KEY=EMPTY
```

README 只增加指向新中文模型文档的短链接；完整 README 中文化在最后一份计划执行。

- [ ] **Step 3: 运行完整后端测试**

Run: `python -m pytest -v`

Expected: 全部测试 PASS，0 failures。

- [ ] **Step 4: 检查密钥泄漏和 schema**

Run: `rg -n "top-secret|secret-value" src tests docs README.md .env.example`

Expected: 只在测试输入和安全说明中出现，不在响应快照 fixture 中出现。

Run: `python -c "from paper_agent.app import create_app; s=create_app().openapi(); assert 'api_key' not in str(s['components']['schemas'].get('ModelProfileResponse', {}))"`

Expected: exit 0。

- [ ] **Step 5: 提交文档与回归结果**

```bash
git add docs/model-services.md .env.example README.md
git commit -m "docs: explain multi-model vllm configuration"
```
