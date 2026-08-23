# paper-agent

> 中文开发入口：[文档导航](docs/README.md) · [当前开发交接](docs/developer-handoff.md) · [贡献与开发指南](CONTRIBUTING.md)

多模型 vLLM 配置与能力说明：[docs/model-services.md](docs/model-services.md)。

A local-first workspace for reading AI/ML papers. A local FastAPI service can
accept a PDF, parse and persist its document data, serve the original source
PDF and page images, store paper-scoped notes, and build an evidence-backed
knowledge graph with an optional local reasoning model. The Agent Runtime
supports citation-aware, tool-calling paper chat, and the browser workbench is
included for local reading.

## Requirements and local installation

Use Python 3.12 or newer. The browser workbench requires Node.js 22.14 or
newer on the Node 22 release line, or Node.js 24 or newer. From the repository
root, create and activate a virtual environment if desired, then install the
project and its development tools:

```bash
python -m pip install -e ".[dev]"
```

## Local data

Unless an application instance is constructed with custom settings, the service
uses `.paper-agent` under the directory from which it is started. It creates:

- `.paper-agent/paper-agent.db` — the local SQLite database.
- `.paper-agent/papers/` — the uploaded source PDFs, stored under generated
  IDs rather than their original names.

Uploads must have a `.pdf` name and PDF bytes. The service retains the original
uploaded PDF bytes as its source file; parsed document records are stored in
SQLite and do not replace that source file.

## Test and run

Run the full test suite with:

```bash
python -m pytest -v
```

Start the local development server from the repository root:

```bash
uvicorn paper_agent.app:create_app --factory --reload
```

The service listens on `http://127.0.0.1:8000` by default. `GET /health`
returns `{"status":"ok"}` when it is running.

```bash
curl http://127.0.0.1:8000/health
```

## Browser workbench

Run the backend and browser development server as two local processes from the
repository root:

1. Start paper-agent: `.venv\Scripts\uvicorn.exe paper_agent.app:create_app --factory --port 8000`
2. In a second terminal: `cd web; npm install --cache .npm-cache; npm run dev`
3. Open the Vite URL printed by the browser server, normally
   `http://127.0.0.1:5173`.

The Vite development server proxies only `/api` requests to the local
paper-agent backend. The workbench has three panes: the left pane renders the
original PDF and source highlights, the center pane shows the paper's evidence
graph, and the right pane contains the Agent and Notes tools. Use **Build core
graph** after document processing completes, then **Build deep graph** after
the core graph is available. Selecting a graph node reveals its evidence;
clicking a located evidence item or Agent citation selects the source element,
jumps the reader to its page, and highlights its bounding box.

The Agent separates `paper_only` from `external_knowledge`. `paper_only`
returns only the citation-validated paper answer. `external_knowledge` keeps
background knowledge in a separate explanation while paper-supported content
stays in the citation-validated answer. The UI displays complete
citation-validated responses and does not stream unverified model text.

## Minimal upload and read workflow

With the server running, upload a local PDF:

```bash
curl -F "file=@paper.pdf;type=application/pdf" http://127.0.0.1:8000/api/papers
```

The response contains the new paper's `id`. Substitute that value for
`<paper-id>` to retrieve its summary, parsed document, or original source PDF:

```bash
curl http://127.0.0.1:8000/api/papers/<paper-id>
curl http://127.0.0.1:8000/api/papers/<paper-id>/document
curl -o source.pdf http://127.0.0.1:8000/api/papers/<paper-id>/source
```

An upload or summary response includes the independent graph-stage fields in
addition to the paper's overall ingestion status:

```json
{
  "id": "<paper-id>",
  "original_filename": "paper.pdf",
  "status": "completed",
  "stage0_status": "completed",
  "stage1_status": "completed",
  "stage2_status": null,
  "stage3_status": null,
  "error": null
}
```

`stage2_status` tracks the core graph and `stage3_status` tracks the deep
graph. They remain `null` until the corresponding graph build is attempted.

## Optional local reasoning configuration, graph processing, and paper chat

Paper upload, document retrieval, source retrieval, page rendering, and notes
work without a reasoning-model configuration. Graph construction is optional:
when the prerequisites are satisfied but no reasoning model is configured, a
core build (completed Stage 1) or deep build (completed Stages 1 and 2) returns
`503 {"detail":"Reasoning model is not configured."}`. A build that does not
meet those prerequisites returns `409 {"detail":"Paper graph prerequisites
are not complete."}` instead; neither response creates a processing row.

To enable local graph construction and citation-aware paper chat, run the
OpenAI-compatible vLLM reasoning server on port `8001` and paper-agent on port
`8000`. They are separate local services and must not share a port. Start
vLLM with a parser that matches the served model:

```text
vllm serve <your-model> --port 8001 --enable-auto-tool-choice --tool-call-parser <parser-for-your-model>
```

The served model needs a tool-compatible chat template, auto tool choice, the
appropriate vLLM tool-call parser, and strict JSON-schema support. Select the
chat template and parser for the model you serve; do not copy a parser or model
name from this runbook. The runtime uses strict JSON-schema output for final
answers and strict tool parameter schemas. See the [vLLM tool-calling
documentation](https://docs.vllm.ai/en/stable/features/tool_calling/) for
model/parser compatibility and chat-template configuration.

In PowerShell, set these variables before starting paper-agent:

```powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8001/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
```

Do not hard-code the model name: `PAPER_AGENT_REASONING_MODEL` must be the
name exposed by the vLLM server. `PAPER_AGENT_REASONING_BASE_URL` and
`PAPER_AGENT_REASONING_MODEL` must either both be set or both be absent. The
API key defaults to `EMPTY` when omitted, which is suitable for a local vLLM
server that does not require authentication.

During graph construction and agent chat, paper-agent sends OpenAI-compatible
chat messages and requests a strict `json_schema` response format. This
runbook documents the required configuration but does not start a vLLM server;
the full local test suite runs without a configured vLLM endpoint.

Start paper-agent on its separate port:

```bash
uvicorn paper_agent.app:create_app --factory --port 8000
```

With vLLM configured and paper-agent running, upload a PDF and use the
returned ID to build the core graph:

```bash
curl -F "file=@paper.pdf;type=application/pdf" http://127.0.0.1:8000/api/papers
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/graph/core
```

A successful core build returns graph nodes and edges with their evidence IDs;
all returned core entities use `"stage": "stage2"`. A subsequent paper
summary then reports the completed core stage while leaving the deep stage
unbuilt:

```json
{
  "id": "<paper-id>",
  "original_filename": "paper.pdf",
  "status": "completed",
  "stage0_status": "completed",
  "stage1_status": "completed",
  "stage2_status": "completed",
  "stage3_status": null,
  "error": null
}
```

Core graph construction requires completed document ingestion. Deep graph
construction additionally requires a completed core graph; otherwise the
build endpoint returns `409 {"detail":"Paper graph prerequisites are not
complete."}`.

Visual graph enrichment remains outside the Agent Runtime increment.

## Local tool-calling runbook

With vLLM on `http://127.0.0.1:8001` and paper-agent on
`http://127.0.0.1:8000`, explicitly validate tool calling before relying on
agent chat. This endpoint asks the configured reasoning server for one
no-argument health tool call; it is not the service's general `GET /health`
endpoint.

```bash
curl -X POST http://127.0.0.1:8000/api/agent/health
```

A correctly configured server returns:

```json
{"status":"ok"}
```

If reasoning is not configured or explicit tool-calling validation fails, the
endpoint returns `503` with `{"detail":"Reasoning model tool calling is
unavailable."}`. Do not treat a passing unit test as evidence that a particular
local vLLM model and parser work together; run the explicit health request after
starting those services.

After an upload has parsed successfully (the upload response reports
`"status": "completed"` and `"stage1_status": "completed"`), submit a
non-streaming chat request using its returned paper ID:

```bash
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/agent/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"What problem does this paper solve?","mode":"paper_only"}'
```

The response is an ordinary JSON object, not a stream. Its `conversation_id`
can be included in a later request in the same mode to continue the
conversation.

The non-streaming Citation Guard validates the final model payload against
source-element IDs returned by tools during the current request. `paper_only`
returns only a citation-validated `paper_answer`. If the runtime cannot
validate the evidence, it returns `status: "insufficient_evidence"` and does
not return model prose as a paper claim. `external_knowledge` places model
background only in `background_explanation`; paper-supported content remains
in the separately citation-validated `paper_answer`.

Current exclusions from this increment are Web UI streaming, vision
enrichment, embeddings, and tool-calling retries beyond the bounded runtime
loop.
