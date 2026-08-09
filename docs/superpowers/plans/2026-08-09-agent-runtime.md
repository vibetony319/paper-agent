# Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a durable, vLLM-only, paper-grounded chat runtime that uses paper and graph tools, validates every paper citation before returning an answer, and keeps optional background knowledge visibly separate.

**Architecture:** The runtime remains local and synchronous for this increment. It gathers evidence through deterministic repository-backed tools, sends tool calls to the configured vLLM reasoning endpoint, then requests one strict JSON final answer. A Citation Guard accepts only source-element IDs returned by the executed tools; it replaces unsupported paper answers with a fixed insufficient-evidence response before HTTP can return anything. Conversations and citations are persisted in SQLite so the later Web UI can reload and link to source geometry.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 with SQLite, Pydantic 2, OpenAI Python client against a vLLM OpenAI-compatible server, pytest.

## Global Constraints

- Keep the product local-first, single-user, and vLLM-only; do not add a provider gateway, embeddings, web retrieval, or multi-agent orchestration.
- A paper-grounded citation is a located document_elements.id owned by the active paper. Graph node and edge IDs are navigation aids, not answer citations.
- Never expose local paths, model configuration, request prompts, raw model replies, or tool exception text in a public response.
- Paper only answers may be returned only after Citation Guard validation. This increment returns complete JSON responses; it does not stream unverified model text.
- External knowledge mode must serialize paper_answer and background_explanation separately. Paper claims retain the same citation requirement.
- Do not invoke a reasoning endpoint at FastAPI startup. Tool-calling validation is an explicit health request.
- All tool requests use parallel_tool_calls=False, execute at most one call per model turn, and stop after six tool turns.
- The reasoning server must support the selected tool-call parser/chat template and strict JSON-schema responses. Tests use injected fakes and make no real model request.
- Preserve existing upload, parsing, graph, note, and graph API behavior.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| src/paper_agent/domain.py | Durable conversation, message, mode, and validated-answer records. |
| src/paper_agent/database.py | Conversation, message, and message-citation SQLite tables with same-paper foreign keys. |
| src/paper_agent/storage.py | Atomic conversation/message persistence and ordered history retrieval. |
| src/paper_agent/services/agent_tools.py | Strict, deterministic paper/graph tool schemas and repository-backed execution. |
| src/paper_agent/models/vllm.py | Direct vLLM tool-turn, strict final-JSON, and explicit tool-health requests. |
| src/paper_agent/services/citation_guard.py | Final-answer schema plus evidence allow-list enforcement and safe fallback. |
| src/paper_agent/services/agent_runtime.py | Bounded model/tool loop, prompt construction, conversation lifecycle, and citation-gated persistence. |
| src/paper_agent/routes/agent.py | Thin HTTP boundary for chat, conversation retrieval, and tool-calling health. |
| src/paper_agent/schemas.py | Public chat/conversation/citation DTOs with normalized location data. |
| tests/unit | Domain, repository, tool, model-client, guard, and runtime behavior tests. |
| tests/integration/test_agent_api.py | Public endpoint, status mapping, and no-leak regression coverage. |
| README.md | Local tool-calling server setup and chat API examples. |

## Task 1: Durable conversations, messages, and citation joins

**Files:**

- Modify: src/paper_agent/domain.py
- Modify: src/paper_agent/database.py
- Modify: src/paper_agent/storage.py
- Test: tests/unit/test_domain.py
- Test: tests/unit/test_storage.py

**Interfaces:**

~~~python
class AgentMode(StrEnum):
    paper_only = "paper_only"
    external_knowledge = "external_knowledge"

class AgentMessageRole(StrEnum):
    user = "user"
    assistant = "assistant"

@dataclass(frozen=True)
class Conversation:
    paper_id: str
    mode: AgentMode
    id: str = field(default_factory=lambda: str(uuid4()))

@dataclass(frozen=True)
class ConversationMessage:
    conversation_id: str
    paper_id: str
    role: AgentMessageRole
    content: str
    citation_element_ids: tuple[str, ...] = ()
    sequence: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

class ConversationReferenceError(ValueError): ...

class PaperRepository:
    def create_conversation(self, conversation: Conversation) -> Conversation: ...
    def get_conversation(self, paper_id: str, conversation_id: str) -> Conversation | None: ...
    def append_conversation_message(self, message: ConversationMessage) -> ConversationMessage: ...
    def get_conversation_messages(
        self, paper_id: str, conversation_id: str
    ) -> tuple[ConversationMessage, ...]: ...
~~~

The conversations table stores id, paper_id, and mode with a composite unique key for id and paper_id. The conversation_messages table stores conversation and paper IDs, role, content, and a monotonically increasing per-conversation sequence. conversation_message_citations joins an assistant message to located document elements through composite same-paper foreign keys. User messages have no citations. The repository permits zero-citation assistant messages; Citation Guard and the runtime (Tasks 4 and 5) ensure that this is persisted only for the canonical insufficient-evidence reply.

- [ ] **Step 1: Write failing domain and persistence tests**

~~~python
def test_repository_rejects_a_message_citation_from_another_paper(repository):
    first = _paper_with_located_element(repository)
    second = _paper_with_located_element(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=first.paper_id, mode=AgentMode.paper_only)
    )

    with pytest.raises(ConversationReferenceError, match="citation"):
        repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=first.paper_id,
                role=AgentMessageRole.assistant,
                content="A grounded answer.",
                citation_element_ids=(second.element_id,),
            )
        )
~~~

~~~python
def test_repository_returns_messages_in_durable_sequence_order(repository):
    conversation = _conversation_for_paper(repository)
    first = repository.append_conversation_message(_user_message(conversation, "First"))
    second = repository.append_conversation_message(_assistant_message(conversation, "Second"))

    assert [message.id for message in repository.get_conversation_messages(
        conversation.paper_id, conversation.id
    )] == [first.id, second.id]
~~~

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_storage.py -v

Expected: FAIL because conversation types, tables, and repository methods do not exist.

- [ ] **Step 3: Implement the narrow domain, table, and repository behavior**

~~~python
def append_conversation_message(self, message: ConversationMessage) -> ConversationMessage:
    if message.role is AgentMessageRole.user and message.citation_element_ids:
        raise ConversationReferenceError("user messages cannot cite source elements")
    with self.engine.begin() as connection:
        self._require_owned_conversation(connection, message.paper_id, message.conversation_id)
        self._require_located_conversation_citations(
            connection, message.paper_id, message.citation_element_ids
        )
        sequence = self._next_conversation_sequence(connection, message.conversation_id)
        connection.execute(insert(conversation_messages).values(..., sequence=sequence))
        connection.execute(insert(conversation_message_citations), [...])
    return replace(message, sequence=sequence)
~~~

Use a migration only for existing columns; the new tables are created by repository metadata on both new and existing local databases. Do not store a PDF path, raw tool result, or raw model response in either table.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_storage.py -v

Expected: PASS for same-paper, located-citation, ordering, and invalid-role coverage.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/domain.py src/paper_agent/database.py src/paper_agent/storage.py tests/unit/test_domain.py tests/unit/test_storage.py
git commit -m "feat: persist paper agent conversations"
~~~

## Task 2: Strict paper and graph tool registry

**Files:**

- Create: src/paper_agent/services/agent_tools.py
- Test: tests/unit/services/test_agent_tools.py

**Interfaces:**

~~~python
class AgentToolError(ValueError): ...

@dataclass(frozen=True)
class ToolExecution:
    name: str
    content: dict[str, object]
    evidence_element_ids: tuple[str, ...]

class PaperToolRegistry:
    def __init__(self, repository: PaperRepository) -> None: ...
    def definitions(self) -> tuple[dict[str, object], ...]: ...
    def execute(
        self, *, paper_id: str, name: str, arguments: dict[str, object]
    ) -> ToolExecution: ...
~~~

Expose exactly these tools:

~~~text
search_paper(query, limit=5)
read_element(element_id)
read_section(section_id)
search_graph(query, limit=5)
inspect_node(node_id)
expand_graph(node_id, depth=1)
find_graph_paths(source_node_id, target_node_id, max_depth=3)
~~~

Every parameter model uses extra=forbid, rejects whitespace-only strings, bounds limit to 1 through 10 and depths to 0 through 3, and produces OpenAI function schemas with strict=true. Tool output contains only public paper/graph fields and evidence_element_ids; it never includes stored filenames or filesystem paths. Only located document elements become evidence_element_ids.

- [ ] **Step 1: Write failing tool-boundary tests**

~~~python
def test_read_element_returns_only_same_paper_located_evidence(repository):
    first = _paper_with_located_element(repository)
    second = _paper_with_located_element(repository)
    tools = PaperToolRegistry(repository)

    with pytest.raises(AgentToolError, match="element"):
        tools.execute(
            paper_id=first.paper_id,
            name="read_element",
            arguments={"element_id": second.element_id},
        )

    result = tools.execute(
        paper_id=first.paper_id,
        name="read_element",
        arguments={"element_id": first.element_id},
    )
    assert result.evidence_element_ids == (first.element_id,)
    assert "path" not in repr(result.content)
~~~

~~~python
def test_tool_schema_and_execution_reject_unknown_arguments(repository):
    with pytest.raises(AgentToolError, match="arguments"):
        PaperToolRegistry(repository).execute(
            paper_id=_paper_id(repository),
            name="search_paper",
            arguments={"query": "router", "page": 7},
        )
~~~

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_agent_tools.py -v

Expected: FAIL because the tool registry is absent.

- [ ] **Step 3: Implement deterministic tool execution**

~~~python
def execute(self, *, paper_id: str, name: str, arguments: dict[str, object]) -> ToolExecution:
    handler = self._handlers.get(name)
    if handler is None:
        raise AgentToolError("requested paper tool is unavailable")
    return handler(paper_id, arguments)

def _located_evidence_ids(elements: tuple[DocumentElement, ...]) -> tuple[str, ...]:
    return tuple(element.id for element in elements if element.location_status == "located")
~~~

Implement search_paper as deterministic Unicode-normalized substring matching over document-element text. Graph tools call the existing repository graph queries and include source-element IDs from graph evidence. Do not add vector search or an embedding dependency.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_agent_tools.py -v

Expected: PASS for schema strictness, ownership, public output, graph navigation, and located-evidence filtering.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/services/agent_tools.py tests/unit/services/test_agent_tools.py
git commit -m "feat: add evidence-scoped paper agent tools"
~~~

## Task 3: Direct vLLM tool-calling and final-answer client

**Files:**

- Modify: src/paper_agent/models/__init__.py
- Modify: src/paper_agent/models/vllm.py
- Test: tests/unit/models/test_vllm_tools.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class VllmToolCall:
    id: str
    name: str
    arguments: dict[str, object]

@dataclass(frozen=True)
class VllmToolTurn:
    content: str | None
    tool_calls: tuple[VllmToolCall, ...]

class VllmToolCallingError(RuntimeError): ...

class VllmToolCallingClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None: ...
    def request_tool_turn(
        self, *, messages: list[dict[str, object]], tools: tuple[dict[str, object]],
        tool_choice: str | dict[str, object]
    ) -> VllmToolTurn: ...
    def generate_json_messages(
        self, *, messages: list[dict[str, object]], schema_name: str, schema: dict[str, object]
    ) -> dict[str, object]: ...
    def validate_tool_calling(self) -> None: ...
~~~

request_tool_turn calls the configured model with temperature=0, the given tools, parallel_tool_calls=False, and the requested tool_choice. It parses only the first completion choice and JSON-decodes every tool argument object. generate_json_messages reuses the same direct OpenAI client and requests a strict json_schema response over the full message list. validate_tool_calling sends a single explicit health tool request and fails if no valid health call is returned. Every client, protocol, or JSON failure becomes a short safe exception that contains neither prompt nor response text.

- [ ] **Step 1: Write failing request-shape and failure-safety tests**

~~~python
def test_tool_turn_uses_single_nonparallel_call_and_parses_arguments(fake_openai):
    client = VllmToolCallingClient(_config(), client=fake_openai)

    turn = client.request_tool_turn(
        messages=[{"role": "user", "content": "Read the method."}],
        tools=(_read_element_tool(),),
        tool_choice="auto",
    )

    assert turn.tool_calls[0].name == "read_element"
    assert turn.tool_calls[0].arguments == {"element_id": "e1"}
    assert fake_openai.requests[0]["parallel_tool_calls"] is False
    assert fake_openai.requests[0]["temperature"] == 0
~~~

~~~python
def test_tool_client_never_exposes_raw_model_payload_on_bad_arguments(fake_openai):
    fake_openai.set_tool_arguments("{bad-secret-from-server")

    with pytest.raises(VllmToolCallingError) as error:
        VllmToolCallingClient(_config(), client=fake_openai).request_tool_turn(
            messages=[{"role": "user", "content": "source"}],
            tools=(_read_element_tool(),),
            tool_choice="auto",
        )

    assert "bad-secret-from-server" not in str(error.value)
~~~

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/unit/models/test_vllm_tools.py -v

Expected: FAIL because the tool-calling client does not exist.

- [ ] **Step 3: Implement the thin direct client and explicit health check**

~~~python
response = self.client.chat.completions.create(
    model=self.config.model,
    messages=messages,
    tools=list(tools),
    tool_choice=tool_choice,
    parallel_tool_calls=False,
    temperature=0,
)
~~~

The health definition is a strict no-argument function named paper_agent_tool_health. Send a system instruction requiring that call, use tool_choice="auto", and accept success only when the returned call has the expected name and {} arguments. This explicit request is the only health probe; do not call it during app creation.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/unit/models/test_vllm_tools.py -v

Expected: PASS for first-choice parsing, tool schema/request shape, health validation, strict final JSON, and safe errors.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/models/__init__.py src/paper_agent/models/vllm.py tests/unit/models/test_vllm_tools.py
git commit -m "feat: add direct vllm tool calling client"
~~~

## Task 4: Citation Guard and final answer contract

**Files:**

- Create: src/paper_agent/services/citation_guard.py
- Test: tests/unit/services/test_citation_guard.py

**Interfaces:**

~~~python
class CitationGuardError(ValueError): ...

@dataclass(frozen=True)
class CitationValidatedAnswer:
    status: Literal["grounded", "insufficient_evidence"]
    paper_answer: str
    citation_element_ids: tuple[str, ...]
    background_explanation: str | None

class CitationGuard:
    def output_schema(self) -> dict[str, object]: ...
    def validate(
        self, payload: dict[str, object], *, mode: AgentMode,
        allowed_evidence_ids: frozenset[str]
    ) -> CitationValidatedAnswer: ...
~~~

The strict model output contract is:

~~~json
{
  "status": "grounded",
  "paper_answer": "The paper's evidence-backed answer.",
  "citation_element_ids": ["document-element-id"],
  "background_explanation": null
}
~~~

For grounded, paper_answer is nonblank and citations are a nonempty, duplicate-free subset of allowed_evidence_ids. For insufficient_evidence, the guard ignores model prose, returns the canonical paper answer "I could not find enough evidence in this paper to answer that reliably.", and returns no citations. In paper_only mode, background_explanation must be null; in external_knowledge mode, it may be a trimmed string but remains separate from the paper answer.

- [ ] **Step 1: Write failing citation-guard tests**

~~~python
def test_guard_replaces_an_unsupported_paper_answer_with_a_safe_refusal():
    answer = CitationGuard().validate(
        {
            "status": "grounded",
            "paper_answer": "The method improves accuracy.",
            "citation_element_ids": ["invented-element"],
            "background_explanation": None,
        },
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.citation_element_ids == ()
    assert "improves accuracy" not in answer.paper_answer
~~~

~~~python
def test_external_mode_keeps_background_separate_from_cited_paper_answer():
    answer = CitationGuard().validate(
        _grounded_payload(citations=["e1"], background="General background."),
        mode=AgentMode.external_knowledge,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.paper_answer
    assert answer.background_explanation == "General background."
~~~

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_citation_guard.py -v

Expected: FAIL because Citation Guard is absent.

- [ ] **Step 3: Implement strict parsing and canonical fallback**

~~~python
if not citation_ids or not set(citation_ids).issubset(allowed_evidence_ids):
    return _insufficient_evidence_answer(mode, background_explanation=None)
if mode is AgentMode.paper_only and background_explanation is not None:
    return _insufficient_evidence_answer(mode, background_explanation=None)
~~~

Use a Pydantic model with extra=forbid for the response boundary. Convert validation errors to CitationGuardError("invalid final answer contract") with from None; do not include model content in the exception. An invalid final contract is handled by the runtime as the canonical insufficient-evidence answer, not an unverified answer.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_citation_guard.py -v

Expected: PASS for exact evidence subsets, duplicate rejection, external-mode partitioning, safe fallback, and no raw-content error leaks.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/services/citation_guard.py tests/unit/services/test_citation_guard.py
git commit -m "feat: guard paper agent citations"
~~~

## Task 5: Bounded paper-agent orchestration

**Files:**

- Create: src/paper_agent/services/agent_runtime.py
- Test: tests/unit/services/test_agent_runtime.py

**Interfaces:**

~~~python
class AgentRuntimeUnavailableError(RuntimeError): ...
class AgentRuntimePrerequisiteError(RuntimeError): ...
class AgentRuntimeResponseError(RuntimeError): ...

@dataclass(frozen=True)
class AgentQuestion:
    content: str
    mode: AgentMode
    conversation_id: str | None = None

@dataclass(frozen=True)
class AgentTurn:
    conversation: Conversation
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    answer: CitationValidatedAnswer

class PaperAgentRuntime:
    def __init__(
        self, *, repository: PaperRepository, tools: PaperToolRegistry,
        client: VllmToolCallingClient | None, guard: CitationGuard
    ) -> None: ...
    def ask(self, *, paper_id: str, question: AgentQuestion) -> AgentTurn: ...
    def validate_tool_calling(self) -> None: ...
~~~

Before creating a conversation or writing a message, ask verifies the paper exists, Stage 1 is completed, a supplied conversation belongs to the paper and has the requested mode, and a reasoning client is configured. It raises typed preflight errors without a new processing row or chat write.

For a valid request, persist the user message, assemble the latest six durable messages, execute at most six single tool turns, and accumulate only ToolExecution.evidence_element_ids actually returned during this request. The first request uses tool_choice="required"; later turns use "auto". Then call generate_json_messages with CitationGuard.output_schema, validate through the guard, and only then persist the assistant message. A malformed final payload becomes the canonical insufficient-evidence response. A vLLM transport or protocol failure raises a safe runtime error and never persists a raw model response.

- [ ] **Step 1: Write failing orchestration tests**

~~~python
def test_runtime_executes_tools_then_persists_only_guarded_answer(repository):
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient.tool_then_final(
        tool_name="search_paper",
        tool_arguments={"query": "router", "limit": 5},
        final_payload=_grounded_payload(citations=[paper.element_id]),
    )
    runtime = _runtime(repository, client)

    turn = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="How does routing work?", mode=AgentMode.paper_only),
    )

    assert turn.answer.status == "grounded"
    assert turn.assistant_message.citation_element_ids == (paper.element_id,)
    assert client.tool_choices == ["required"]
~~~

~~~python
def test_runtime_returns_safe_insufficient_answer_before_any_ui_response(repository):
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime(
        repository,
        FakeAgentClient.tool_then_final(
            tool_name="search_paper",
            tool_arguments={"query": "router", "limit": 5},
            final_payload=_grounded_payload(citations=["fabricated"]),
        ),
    )

    turn = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="What improves accuracy?", mode=AgentMode.paper_only),
    )

    assert turn.answer.status == "insufficient_evidence"
    assert turn.assistant_message.citation_element_ids == ()
~~~

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_agent_runtime.py -v

Expected: FAIL because the runtime does not exist.

- [ ] **Step 3: Implement preflight, tool loop, history, and guarded persistence**

~~~python
for tool_turn in range(MAX_TOOL_TURNS):
    turn = self.client.request_tool_turn(
        messages=messages,
        tools=self.tools.definitions(),
        tool_choice="required" if tool_turn == 0 else "auto",
    )
    if not turn.tool_calls:
        break
    call = turn.tool_calls[0]
    execution = self.tools.execute(paper_id=paper_id, name=call.name, arguments=call.arguments)
    allowed_evidence_ids.update(execution.evidence_element_ids)
    messages.extend(_tool_result_messages(turn, execution))
~~~

The system prompt explicitly tells the model that graph information is for navigation, only returned source-element IDs may be cited, and unsupported paper claims must use insufficient_evidence. In external mode it also says background explanation belongs only in the separate field. Add no streaming interface in this task.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/unit/services/test_agent_runtime.py -v

Expected: PASS for preflight/no-write behavior, tool bound, ownership, durable history, external separation, citation fallback, and safe client failures.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/services/agent_runtime.py tests/unit/services/test_agent_runtime.py
git commit -m "feat: orchestrate citation-aware paper chat"
~~~

## Task 6: Agent HTTP API and application wiring

**Files:**

- Modify: src/paper_agent/app.py
- Modify: src/paper_agent/schemas.py
- Create: src/paper_agent/routes/agent.py
- Modify: src/paper_agent/routes/__init__.py
- Create: tests/integration/test_agent_api.py

**Endpoints:**

~~~text
POST /api/papers/{paper_id}/agent/messages
GET  /api/papers/{paper_id}/agent/conversations/{conversation_id}
POST /api/agent/health
~~~

POST /api/papers/{paper_id}/agent/messages accepts:

~~~json
{
  "content": "Why does the paper use an auxiliary loss?",
  "mode": "paper_only",
  "conversation_id": null
}
~~~

It returns a complete, citation-validated response:

~~~json
{
  "conversation_id": "...",
  "message_id": "...",
  "status": "grounded",
  "paper_answer": "...",
  "background_explanation": null,
  "citations": [
    {
      "id": "...",
      "kind": "paragraph",
      "page_number": 4,
      "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.3}
    }
  ]
}
~~~

GET returns the persisted conversation and message citations. POST /api/agent/health performs explicit vLLM tool-calling validation and never runs automatically at startup. Wire exactly one shared PaperRepository, one PaperToolRegistry, one CitationGuard, and one PaperAgentRuntime into app state. Build the direct vLLM client only when Settings.reasoning_model exists.

- [ ] **Step 1: Write failing agent API tests**

~~~python
def test_agent_returns_locatable_same_paper_citations_without_paths(client, uploaded_paper):
    _configure_fake_agent_runtime(client.app, _grounded_tool_flow(uploaded_paper))

    response = client.post(
        f"/api/papers/{uploaded_paper['id']}/agent/messages",
        json={"content": "Explain the method.", "mode": "paper_only"},
    )

    assert response.status_code == 200
    assert response.json()["citations"][0]["page_number"] == 1
    assert str(client.app.state.settings.data_dir) not in response.text
~~~

~~~python
def test_agent_maps_preflight_and_configuration_failures_without_chat_writes(
    client, uploaded_paper, stage1_incomplete_paper
):
    assert client.post(
        f"/api/papers/{stage1_incomplete_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 409
    assert client.post(
        f"/api/papers/{uploaded_paper['id']}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 503
~~~

Create stage1_incomplete_paper directly through the existing repository fixture helpers; do not depend on a malformed upload producing a reusable API record.

- [ ] **Step 2: Run test to verify it fails**

Run: .venv\Scripts\python.exe -m pytest tests/integration/test_agent_api.py -v

Expected: FAIL because agent routes and DTOs do not exist.

- [ ] **Step 3: Implement thin routes and safe mappings**

~~~python
try:
    turn = _runtime(request).ask(paper_id=paper_id, question=payload.to_question())
except AgentRuntimePrerequisiteError as error:
    raise HTTPException(409, "Paper agent prerequisites are not complete.") from error
except AgentRuntimeUnavailableError as error:
    raise HTTPException(503, "Reasoning model is not configured.") from error
except AgentRuntimeResponseError as error:
    raise HTTPException(502, "Reasoning model could not complete the request.") from error
~~~

Map unknown paper and conversation IDs to 404. Let malformed request fields, invalid modes, and invalid UUID-like values receive FastAPI/Pydantic 422 responses. Citation Guard fallbacks remain HTTP 200 with status insufficient_evidence, never an unverified answer. The health route maps no reasoning configuration or failed tool validation to a safe 503.

- [ ] **Step 4: Run test to verify it passes**

Run: .venv\Scripts\python.exe -m pytest tests/integration/test_agent_api.py tests/integration/test_papers_api.py -v

Expected: PASS for answer/citation DTOs, conversation history, 404/409/422/502/503 boundaries, external partitioning, no path leakage, and explicit health behavior.

- [ ] **Step 5: Commit**

~~~bash
git add src/paper_agent/app.py src/paper_agent/schemas.py src/paper_agent/routes/agent.py src/paper_agent/routes/__init__.py tests/integration/test_agent_api.py
git commit -m "feat: expose citation-aware paper agent API"
~~~

## Task 7: Local tool-calling runbook and full verification

**Files:**

- Modify: README.md
- Test: all tests

Document that the vLLM reasoning server and paper-agent use separate local ports in the example: vLLM 8001 and paper-agent 8000. Explain that the served model must have a tool-compatible chat template, auto tool choice enabled, an appropriate vLLM --tool-call-parser, and strict JSON-schema support. Include a generic command shape without selecting a model-specific parser:

~~~text
vllm serve <your-model> --port 8001 --enable-auto-tool-choice --tool-call-parser <parser-for-your-model>
~~~

Document the explicit POST /api/agent/health validation, the non-streaming citation guard, paper_only versus external_knowledge, and a chat request after a successfully parsed upload. State that Web UI streaming, vision enrichment, embeddings, and tool-calling retries beyond the bounded runtime loop remain outside this increment.

- [ ] **Step 1: Add the runbook and update API examples**

~~~markdown
paper_only returns only a citation-validated paper_answer. If the runtime cannot validate the evidence, it returns status: "insufficient_evidence" and does not return model prose as a paper claim. external_knowledge places model background only in background_explanation.
~~~

- [ ] **Step 2: Run complete verification**

Run:

~~~bash
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pytest -v
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\uvicorn.exe paper_agent.app:create_app --factory --version
git diff --check HEAD
~~~

Expected: all commands exit 0 and the entire test suite passes without a configured vLLM endpoint.

- [ ] **Step 3: Commit**

~~~bash
git add README.md
git commit -m "docs: document citation-aware paper chat"
~~~

## Plan Self-Review

- **Spec coverage:** Task 1 supplies durable conversations/messages and same-paper citation joins. Task 2 adds paper and graph navigation tools. Task 3 implements direct vLLM tool calling plus explicit capability validation. Task 4 enforces the Citation Guard and paper/background separation. Task 5 provides bounded orchestration with no pre-guard output. Task 6 exposes safe API surfaces and Task 7 documents local operation and verifies the full repository.
- **Scope:** The plan intentionally excludes vision calls, embeddings, web retrieval, model-provider abstraction, multi-agent coordination, and browser UI. It keeps the current reasoning vLLM role and uses no startup model call.
- **Placeholder scan:** Each task names exact paths, public interfaces, failing tests, implementation behavior, verification command, and commit boundary. No unspecified implementation or deferred code step remains in the tasks.
- **Type consistency:** AgentMode, Conversation, ConversationMessage, ToolExecution, VllmToolTurn, CitationValidatedAnswer, AgentQuestion, and AgentTurn are introduced before their consumers. API DTOs consume only runtime/public domain types, and citations consistently use document_elements.id.
