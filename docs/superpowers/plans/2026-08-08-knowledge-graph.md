# Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, evidence-backed paper knowledge graph that extracts Stage 2 core concepts and Stage 3 deep concepts through a configured local vLLM endpoint, then exposes safe graph query APIs.

**Architecture:** SQLite owns graph nodes, edges, and their document-element evidence joins. A small vLLM-only structured-output client is injected into a synchronous graph-construction service; it never exposes a provider abstraction. The construction service extracts nodes per document scope, deduplicates them deterministically, extracts edges in a separate call, validates every model-returned identifier against allowed source elements, and persists a stage atomically.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, OpenAI Python client against vLLM's OpenAI-compatible server, pytest, httpx.

## Global Constraints

- Preserve the existing local-first parsing/persistence behavior; a reasoning vLLM configuration is optional until a graph-build endpoint is invoked.
- Use only a direct vLLM OpenAI-compatible client. Do not add LiteLLM, a provider gateway, a vector database, or a graph database.
- The fixed node vocabulary is `problem`, `contribution`, `claim`, `method`, `component`, `concept`, `experiment`, `dataset`, `metric`, `result`, `ablation`, and `limitation`.
- Stage 2 permits only `problem`, `contribution`, `claim`, `method`, and `experiment`; Stage 3 permits only `component`, `concept`, `dataset`, `metric`, `result`, `ablation`, and `limitation`.
- The fixed relation vocabulary is `addresses`, `part_of`, `uses`, `compares_with`, `evaluated_on`, `measured_by`, `produces`, `tests`, `supports`, `contradicts`, `defines`, `illustrates`, and `related_to`.
- Every persisted graph node and edge must cite at least one existing, same-paper, `located` document element. Invalid or ambiguous model output is never persisted as graph data.
- Stage 0/1 data and any already-successful graph stage survive later graph-stage failure. Stage 2 and Stage 3 status are persisted and queryable independently.
- Model output, model error text, database exceptions, and local filesystem paths never appear in public API responses.
- Node extraction and edge extraction are separate vLLM calls. The Stage 2/3 endpoints are synchronous for this local MVP; a later worker may replace them without changing the durable graph contract.

---

## File Structure

~~~text
src/paper_agent/
  config.py                         vLLM reasoning configuration from environment
  domain.py                         graph value objects and fixed vocabularies
  database.py                       graph tables and same-paper foreign keys
  storage.py                        graph persistence and traversal queries
  schemas.py                        graph and graph-stage public DTOs
  models/
    __init__.py
    vllm.py                         direct structured-output vLLM client
  services/
    graph_extraction.py             strict model-output parsing and deterministic dedupe
    graph_construction.py           Stage 2/3 orchestration
  routes/
    graph.py                        graph build and query routes
  app.py                            graph service wiring
tests/
  unit/test_domain.py
  unit/test_storage.py
  unit/models/test_vllm.py
  unit/services/test_graph_extraction.py
  unit/services/test_graph_construction.py
  integration/test_papers_api.py
  integration/test_graph_api.py
~~~

## Task 1: Evidence-backed graph domain and SQLite repository

**Files:**

- Modify: `src/paper_agent/domain.py`
- Modify: `src/paper_agent/database.py`
- Modify: `src/paper_agent/storage.py`
- Test: `tests/unit/test_domain.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**

~~~python
class GraphStage(StrEnum):
    core = "stage2"
    deep = "stage3"

@dataclass(frozen=True)
class GraphNode:
    node_type: str
    name: str
    summary: str
    stage: GraphStage
    evidence_element_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: str(uuid4()))

@dataclass(frozen=True)
class GraphEdge:
    source_node_id: str
    target_node_id: str
    relation_type: str
    stage: GraphStage
    evidence_element_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: str(uuid4()))

@dataclass(frozen=True)
class PaperGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

class GraphReferenceError(ValueError): ...

class PaperRepository:
    def replace_graph_stage(
        self, paper_id: str, stage: GraphStage,
        nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
    ) -> PaperGraph: ...
    def get_graph(self, paper_id: str) -> PaperGraph: ...
    def get_graph_node(self, paper_id: str, node_id: str) -> GraphNode | None: ...
    def get_graph_neighbors(self, paper_id: str, node_id: str) -> PaperGraph: ...
    def find_graph_paths(
        self, paper_id: str, source_node_id: str, target_node_id: str, max_depth: int
    ) -> tuple[tuple[str, ...], ...]: ...
    def get_graph_subgraph(
        self, paper_id: str, node_ids: tuple[str, ...], depth: int
    ) -> PaperGraph: ...
~~~

Create `graph_nodes`, `graph_edges`, `graph_node_evidence`, and
`graph_edge_evidence`. Keep `paper_id` on every table. Use composite foreign
keys from graph records to `(paper_id, id)` on graph nodes and document
elements so a graph cannot reference another paper's source. Store a normalized
node name for deterministic deduplication, and create a unique constraint on
`(paper_id, node_type, normalized_name)`. `replace_graph_stage` must run in
a single transaction; replacing core clears both core and dependent deep graph
records, while replacing deep clears only deep records.

- [ ] **Step 1: Write the failing graph-domain tests**

~~~python
def test_graph_records_require_allowed_types_and_source_evidence():
    with pytest.raises(ValueError, match="evidence"):
        GraphNode(
            node_type="method", name="Router", summary="Routes tokens.",
            stage=GraphStage.core, evidence_element_ids=(),
        )

    with pytest.raises(ValueError, match="relation"):
        GraphEdge(
            source_node_id="a", target_node_id="b", relation_type="improves",
            stage=GraphStage.core, evidence_element_ids=("element-1",),
        )
~~~

- [ ] **Step 2: Run the domain test and verify it fails**

Run: `python -m pytest tests/unit/test_domain.py -v`

Expected: FAIL because graph value objects do not exist.

- [ ] **Step 3: Implement immutable graph value objects and vocabularies**

~~~python
CORE_NODE_TYPES = frozenset({"problem", "contribution", "claim", "method", "experiment"})
DEEP_NODE_TYPES = frozenset({"component", "concept", "dataset", "metric", "result", "ablation", "limitation"})
RELATION_TYPES = frozenset({
    "addresses", "part_of", "uses", "compares_with", "evaluated_on",
    "measured_by", "produces", "tests", "supports", "contradicts",
    "defines", "illustrates", "related_to",
})
~~~

Validate nonempty trimmed names/summaries, unique nonempty evidence IDs,
stage-specific node types, valid relation types, and non-self edges. Do not
silently coerce model strings into valid values.

- [ ] **Step 4: Write the failing repository atomicity and ownership tests**

~~~python
def test_repository_rejects_graph_evidence_from_another_paper(repository):
    first = _paper_with_located_element(repository, "first")
    second = _paper_with_located_element(repository, "second")
    node = GraphNode(
        node_type="method", name="Router", summary="Routes tokens.",
        stage=GraphStage.core, evidence_element_ids=(second.element_id,),
    )

    with pytest.raises(GraphReferenceError, match="evidence"):
        repository.replace_graph_stage(first.paper_id, GraphStage.core, (node,), ())
~~~

~~~python
def test_core_replacement_rolls_back_and_preserves_existing_graph(repository):
    paper = _paper_with_located_element(repository, "paper")
    old = _deep_nodes(paper)
    repository.replace_graph_stage(paper.paper_id, GraphStage.deep, old, ())

    with pytest.raises(IntegrityError):
        repository.replace_graph_stage(paper.paper_id, GraphStage.core, _duplicate_nodes(paper), ())

    assert repository.get_graph(paper.paper_id).nodes == old
~~~

- [ ] **Step 5: Run the repository tests and verify they fail**

Run: `python -m pytest tests/unit/test_storage.py -v`

Expected: FAIL because graph tables and repository methods do not exist.

- [ ] **Step 6: Implement graph tables, write validation, and traversal queries**

Inside one database transaction: validate every referenced located element,
insert nodes, insert node evidence, validate edge endpoints against
inserted/surviving nodes, insert edges, then insert edge evidence. For a core
replacement, delete deep edge evidence/edges/nodes first, then core edge
evidence/edges/nodes; only commit after the new core graph is valid.
`get_graph_neighbors`, breadth-first `get_graph_subgraph`, and
`find_graph_paths` must never leave the requested paper.

- [ ] **Step 7: Run focused tests and commit**

Run: `python -m pytest tests/unit/test_domain.py tests/unit/test_storage.py -v`

Expected: PASS.

~~~bash
git add src/paper_agent/domain.py src/paper_agent/database.py src/paper_agent/storage.py tests/unit/test_domain.py tests/unit/test_storage.py
git commit -m "feat: persist evidence-backed paper graphs"
~~~

## Task 2: Direct vLLM structured-output configuration and client

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/paper_agent/config.py`
- Create: `src/paper_agent/models/__init__.py`
- Create: `src/paper_agent/models/vllm.py`
- Test: `tests/unit/models/test_vllm.py`

**Interfaces:**

~~~python
@dataclass(frozen=True)
class VllmModelConfig:
    base_url: str
    model: str
    api_key: str = "EMPTY"

class VllmConfigurationError(ValueError): ...
class VllmResponseError(RuntimeError): ...

class VllmStructuredClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None: ...
    def generate_json(
        self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict
    ) -> dict: ...

def get_settings(*, data_dir: Path | None = None) -> Settings: ...
~~~

Extend `Settings` with `reasoning_model: VllmModelConfig | None`.
`get_settings()` reads all-or-nothing configuration from
`PAPER_AGENT_REASONING_BASE_URL`, `PAPER_AGENT_REASONING_MODEL`, and optional
`PAPER_AGENT_REASONING_API_KEY`; one of the required pair without the other
raises `VllmConfigurationError`. Add `openai>=1.0,<3` to runtime dependencies.

Use `OpenAI(base_url=config.base_url, api_key=config.api_key)`. Send each
extraction request to `chat.completions.create` with the configured model, a
system/user message pair, temperature `0`, and a JSON-schema response format.
Read only the first choice's message content, parse it with `json.loads`,
require an object result, and convert client/protocol/JSON failures into
`VllmResponseError` without embedding raw prompts or server responses in the
exception message.

- [ ] **Step 1: Write the failing configuration and request-shape tests**

~~~python
def test_settings_rejects_partial_reasoning_vllm_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_AGENT_REASONING_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.delenv("PAPER_AGENT_REASONING_MODEL", raising=False)

    with pytest.raises(VllmConfigurationError, match="both"):
        get_settings(data_dir=tmp_path / "data")


def test_structured_client_returns_only_a_json_object(fake_openai_client):
    client = VllmStructuredClient(_config(), client=fake_openai_client)

    assert client.generate_json(
        system_prompt="extract", user_prompt="source", schema_name="nodes", schema={"type": "object"}
    ) == {"nodes": []}
    assert fake_openai_client.requests[0]["temperature"] == 0
~~~

- [ ] **Step 2: Run the vLLM unit tests and verify they fail**

Run: `python -m pytest tests/unit/models/test_vllm.py -v`

Expected: FAIL because the configuration and client module do not exist.

- [ ] **Step 3: Implement the smallest direct vLLM client**

~~~python
response = self.client.chat.completions.create(
    model=self.config.model,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    temperature=0,
    response_format={
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": True},
    },
)
~~~

Inject the client in tests. Do not create a generic provider interface, perform
endpoint discovery, or make a request during app startup.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/unit/models/test_vllm.py -v`

Expected: PASS.

~~~bash
git add pyproject.toml src/paper_agent/config.py src/paper_agent/models tests/unit/models/test_vllm.py
git commit -m "feat: add direct vllm structured output client"
~~~

## Task 3: Strict graph candidate parsing and deterministic deduplication

**Files:**

- Create: `src/paper_agent/services/graph_extraction.py`
- Test: `tests/unit/services/test_graph_extraction.py`

**Interfaces:**

~~~python
class GraphExtractionError(ValueError): ...

@dataclass(frozen=True)
class NodeCandidate:
    local_id: str
    node_type: str
    name: str
    summary: str
    evidence_element_ids: tuple[str, ...]

@dataclass(frozen=True)
class EdgeCandidate:
    source_node_id: str
    target_node_id: str
    relation_type: str
    evidence_element_ids: tuple[str, ...]

def parse_node_candidates(
    payload: dict, *, stage: GraphStage, allowed_evidence_ids: frozenset[str]
) -> tuple[NodeCandidate, ...]: ...

def parse_edge_candidates(
    payload: dict, *, stage: GraphStage, allowed_node_ids: frozenset[str],
    allowed_evidence_ids: frozenset[str]
) -> tuple[EdgeCandidate, ...]: ...

def deduplicate_nodes(
    candidates: tuple[NodeCandidate, ...], stage: GraphStage
) -> tuple[GraphNode, ...]: ...
~~~

Use strict Pydantic request models with `extra="forbid"` only at this boundary.
A candidate must have a nonblank local ID/name/summary, use a stage-allowed type,
and cite a nonempty subset of the exact evidence IDs supplied to the model.
Edges must point to the exact persistent node IDs provided to their separate
edge request. Deduplicate by Unicode-normalized, case-folded
`(node_type, name)`; merge evidence IDs deterministically and keep the first
nonempty summary. Reject the whole model response on an invalid candidate rather
than silently retaining an unsupported claim.

- [ ] **Step 1: Write failing validation tests**

~~~python
def test_node_candidates_reject_hallucinated_evidence_ids():
    payload = {
        "nodes": [{
            "local_id": "n1", "node_type": "method", "name": "Router",
            "summary": "Routes tokens.", "evidence_element_ids": ["not-in-prompt"],
        }]
    }

    with pytest.raises(GraphExtractionError, match="evidence"):
        parse_node_candidates(payload, stage=GraphStage.core, allowed_evidence_ids=frozenset({"e1"}))
~~~

~~~python
def test_deduplicator_merges_evidence_for_casefolded_same_type_names():
    nodes = deduplicate_nodes(
        (_node("n1", "Token Router", "e1"), _node("n2", "token   router", "e2")),
        GraphStage.deep,
    )

    assert len(nodes) == 1
    assert nodes[0].evidence_element_ids == ("e1", "e2")
~~~

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/unit/services/test_graph_extraction.py -v`

Expected: FAIL because strict graph extraction helpers do not exist.

- [ ] **Step 3: Implement strict parsing and deduplication**

~~~python
allowed = set(allowed_evidence_ids)
if not evidence_ids or not set(evidence_ids).issubset(allowed):
    raise GraphExtractionError("candidate evidence must come from the supplied source elements")
~~~

Keep schema construction in `node_output_schema()` and
`edge_output_schema()` so the vLLM client receives the same shape that the
parser validates. Never use a model-returned page number, bounding box, or
free-form citation string.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/unit/services/test_graph_extraction.py -v`

Expected: PASS.

~~~bash
git add src/paper_agent/services/graph_extraction.py tests/unit/services/test_graph_extraction.py
git commit -m "feat: validate evidence-backed graph candidates"
~~~

## Task 4: Stage 2/3 graph construction service

**Files:**

- Create: `src/paper_agent/services/graph_construction.py`
- Modify: `src/paper_agent/storage.py`
- Test: `tests/unit/services/test_graph_construction.py`

**Interfaces:**

~~~python
class GraphBuildUnavailableError(RuntimeError): ...
class GraphBuildPrerequisiteError(RuntimeError): ...

class GraphConstructionService:
    def __init__(
        self, *, repository: PaperRepository, client: VllmStructuredClient | None
    ) -> None: ...
    def build_core(self, paper_id: str) -> PaperGraph: ...
    def build_deep(self, paper_id: str) -> PaperGraph: ...
~~~

`build_core` requires a paper with persisted Stage 1 `completed` status;
`build_deep` additionally requires Stage 2 `completed`. It records queued
then running processing rows for `stage2` or `stage3`, respectively. It
builds scopes from each section's `located` paragraph elements, falling back
to located Stage 0 `text_block` elements when that section has no locatable
paragraph. Each scope prompt contains exact source element IDs, kinds, section
title, and text. It calls `generate_json` once for nodes, deduplicates all
valid nodes, then calls `generate_json` separately for edges using the
persisted-in-memory node IDs and the same evidence IDs. A final cross-section
edge call receives only node IDs/names/summaries plus the union of their cited
source text, so cross-section relationships can be added without inventing
evidence.

On `VllmResponseError`, `GraphExtractionError`, `GraphReferenceError`, or
`SQLAlchemyError`, record a fixed stage-specific failure summary, mark the
paper aggregate status `partial`, and leave prior graph stages intact.
Unexpected programming errors must record the same safe failed/partial state
then re-raise. A missing client raises `GraphBuildUnavailableError` before a
processing row is written.

- [ ] **Step 1: Write the failing happy-path construction test**

~~~python
def test_core_build_persists_deduplicated_nodes_edges_and_exact_evidence(repository):
    paper = _paper_with_stage1_source(repository)
    client = FakeStructuredClient([
        {"nodes": [_node_payload("n1", "method", "Token Router", [paper.element_id])]},
        {"edges": []},
        {"edges": []},
    ])

    graph = GraphConstructionService(repository=repository, client=client).build_core(paper.id)

    assert [(node.node_type, node.name) for node in graph.nodes] == [("method", "Token Router")]
    assert graph.nodes[0].evidence_element_ids == (paper.element_id,)
    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
~~~

- [ ] **Step 2: Run the construction test and verify it fails**

Run: `python -m pytest tests/unit/services/test_graph_construction.py -v`

Expected: FAIL because the construction service does not exist.

- [ ] **Step 3: Implement scope prompts and node/edge sequence**

Use a literal system policy that instructs the model to return only the supplied
JSON schema, cite only listed source IDs, avoid external knowledge, and omit a
candidate when the source does not support it. Stage prompts must name the
allowed node types and relation types. Build node IDs in Python after parsing;
never allow a model to select a database UUID for nodes.

~~~python
node_payload = self.client.generate_json(
    system_prompt=NODE_SYSTEM_PROMPT,
    user_prompt=_node_prompt(stage, scope),
    schema_name="paper_graph_nodes",
    schema=node_output_schema(),
)
~~~

- [ ] **Step 4: Write failure-boundary tests**

~~~python
def test_deep_build_failure_keeps_completed_core_graph_and_marks_only_stage3_failed(repository):
    paper = _paper_with_completed_core_graph(repository)
    client = FakeStructuredClient([VllmResponseError("unavailable")])

    with pytest.raises(VllmResponseError):
        GraphConstructionService(repository=repository, client=client).build_deep(paper.id)

    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
    assert repository.get_latest_stage_status(paper.id, "stage3") == ProcessingStatus.failed
    assert repository.get_graph(paper.id).nodes
~~~

~~~python
def test_build_rejects_missing_reasoning_client_without_mutating_status(repository):
    paper = _paper_with_stage1_source(repository)

    with pytest.raises(GraphBuildUnavailableError):
        GraphConstructionService(repository=repository, client=None).build_core(paper.id)

    assert repository.get_latest_stage_status(paper.id, "stage2") is None
~~~

- [ ] **Step 5: Implement failure handling and run focused tests**

Run: `python -m pytest tests/unit/services/test_graph_construction.py -v`

Expected: PASS.

~~~bash
git add src/paper_agent/services/graph_construction.py src/paper_agent/storage.py tests/unit/services/test_graph_construction.py
git commit -m "feat: construct staged paper knowledge graphs"
~~~

## Task 5: Graph API, durable stage summaries, and app wiring

**Files:**

- Modify: `src/paper_agent/app.py`
- Modify: `src/paper_agent/schemas.py`
- Create: `src/paper_agent/routes/graph.py`
- Modify: `src/paper_agent/routes/__init__.py`
- Test: `tests/integration/test_papers_api.py`
- Test: `tests/integration/test_graph_api.py`

**Interfaces:**

~~~text
POST /api/papers/{paper_id}/graph/core
POST /api/papers/{paper_id}/graph/deep
GET  /api/papers/{paper_id}/graph
GET  /api/papers/{paper_id}/graph/nodes/{node_id}
GET  /api/papers/{paper_id}/graph/nodes/{node_id}/neighbors
GET  /api/papers/{paper_id}/graph/paths?source_id=...&target_id=...&max_depth=3
GET  /api/papers/{paper_id}/graph/subgraph?node_id=...&depth=1
~~~

Extend `PaperSummary` and `PaperSummaryResponse` with nullable
`stage2_status` and `stage3_status`, populated from durable processing rows.
Create DTOs that expose graph IDs, vocabulary values, summary text, and
`evidence_element_ids`, but never source paths, raw vLLM responses, or model
configuration secrets. Wire one shared `PaperRepository` into both ingestion
and graph services, then create `VllmStructuredClient` only when
`Settings.reasoning_model` is configured.

Map unknown papers/nodes to 404, invalid query parameters and malformed body
values to 422, missing Stage 1/2 prerequisites to 409, and missing reasoning
configuration to 503. A graph-stage failure that is safely persisted but
re-raised by the service must become a generic non-leaking 500.

- [ ] **Step 1: Write failing graph-route tests**

~~~python
def test_graph_queries_return_evidence_ids_for_the_same_uploaded_paper(client, uploaded_paper):
    _seed_completed_core_graph(client.app, uploaded_paper["id"])

    response = client.get(f"/api/papers/{uploaded_paper['id']}/graph")

    assert response.status_code == 200
    assert response.json()["nodes"][0]["evidence_element_ids"]
    assert str(client.app.state.settings.data_dir) not in response.text
~~~

~~~python
def test_core_graph_build_without_vllm_configuration_is_safe_503(client, uploaded_paper):
    response = client.post(f"/api/papers/{uploaded_paper['id']}/graph/core")

    assert response.status_code == 503
    assert response.json() == {"detail": "Reasoning model is not configured."}
~~~

- [ ] **Step 2: Run integration tests and verify they fail**

Run: `python -m pytest tests/integration/test_graph_api.py tests/integration/test_papers_api.py -v`

Expected: FAIL because graph routes and Stage 2/3 DTO fields do not exist.

- [ ] **Step 3: Implement schemas, thin routes, and app state wiring**

~~~python
@router.post("/{paper_id}/graph/core", response_model=PaperGraphResponse)
def build_core_graph(paper_id: str, request: Request) -> PaperGraphResponse:
    try:
        return PaperGraphResponse.from_graph(_service(request).build_core(paper_id))
    except GraphBuildUnavailableError as error:
        raise HTTPException(503, "Reasoning model is not configured.") from error
~~~

Keep all route decisions at the HTTP boundary; graph construction and repository
validation remain library-independent.

- [ ] **Step 4: Run graph integration tests and commit**

Run: `python -m pytest tests/integration/test_graph_api.py tests/integration/test_papers_api.py -v`

Expected: PASS.

~~~bash
git add src/paper_agent/app.py src/paper_agent/schemas.py src/paper_agent/routes tests/integration/test_graph_api.py tests/integration/test_papers_api.py
git commit -m "feat: expose evidence-backed paper graph APIs"
~~~

## Task 6: Local graph-processing runbook and full verification

**Files:**

- Modify: `README.md`
- Test: all tests

Document a local reasoning-vLLM configuration without hard-coding a model:

~~~powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8000/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
~~~

Document that the served model must support the selected chat template and
JSON-schema structured output, that graph processing remains unavailable with
no reasoning configuration while document reading still works, and show a
`POST /api/papers/{paper_id}/graph/core` example after upload. Keep future
tool-calling health validation and visual graph enrichment explicitly in the
Agent Runtime increment.

- [ ] **Step 1: Add the runbook section and update existing API response examples**

- [ ] **Step 2: Run a dependency installation check and the complete suite**

Run:

~~~bash
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest -v
python -m compileall -q src
uvicorn paper_agent.app:create_app --factory --version
~~~

Expected: all commands exit 0 and all tests pass.

- [ ] **Step 3: Commit**

~~~bash
git add README.md
git commit -m "docs: document local graph processing"
~~~

## Plan Self-Review

- **Spec coverage:** Task 1 supplies durable graph schema/evidence joins and same-paper integrity; Tasks 2 through 4 add direct vLLM structured extraction, node/edge separation, deterministic dedupe, independent Stage 2/3 state, and safe failures; Task 5 exposes future Agent/UI query surfaces; Task 6 documents local operation.
- **Scope:** This increment deliberately excludes chat orchestration, tool-calling health checks, embeddings, vision enrichment, and the React UI. Those require the Graph APIs produced here but do not block evidence-backed graph construction.
- **Placeholder scan:** Every task names exact files, interfaces, behavior tests, command, and commit boundary. No unbounded parser or provider abstraction is introduced.
- **Type consistency:** `GraphNode`/`GraphEdge` evidence IDs are the same `document_elements.id` values enforced by repository composite foreign keys; `GraphStage.core/deep` maps exactly to persisted stages `stage2/stage3` and to nullable public summary fields.
