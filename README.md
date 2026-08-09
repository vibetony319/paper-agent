# paper-agent

A local-first workspace for reading AI/ML papers. A local FastAPI service can
accept a PDF, parse and persist its document data, serve the original source
PDF and page images, store paper-scoped notes, and build an evidence-backed
knowledge graph with an optional local reasoning model. The Agent Runtime and
web UI increments are not included yet.

## Requirements and local installation

Use Python 3.12 or newer. From the repository root, create and activate a
virtual environment if desired, then install the project and its development
tools:

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

## Minimal upload and read workflow

With the server running, upload a local PDF:

```bash
curl -F "file=@/absolute/path/to/paper.pdf;type=application/pdf" http://127.0.0.1:8000/api/papers
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

## Optional local reasoning configuration and graph processing

Paper upload, document retrieval, source retrieval, page rendering, and notes
work without a reasoning-model configuration. Graph construction is optional:
when no reasoning model is configured, a graph-build request returns
`503 {"detail":"Reasoning model is not configured."}`; it does not start or
record a graph-processing run.

To enable local graph construction, point the service at an already-running
OpenAI-compatible vLLM server. In PowerShell, set these variables before
starting the service:

```powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8000/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
```

Do not hard-code the model name: `PAPER_AGENT_REASONING_MODEL` must be the
name exposed by the vLLM server. `PAPER_AGENT_REASONING_BASE_URL` and
`PAPER_AGENT_REASONING_MODEL` must either both be set or both be absent. The
API key defaults to `EMPTY` when omitted, which is suitable for a local vLLM
server that does not require authentication.

The served model must support the chat template selected for that server and
strict JSON-schema structured output. During graph construction, paper-agent
sends system and user chat messages and requests a strict `json_schema`
response format; configure vLLM with a chat template compatible with the
served model and verify that its structured-output support can satisfy this
contract. This runbook does not make a model request.

After configuring and starting vLLM, start paper-agent normally, upload a PDF,
and use the returned ID to build the core graph:

```bash
curl -F "file=@/absolute/path/to/paper.pdf;type=application/pdf" http://127.0.0.1:8000/api/papers
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

Tool-calling health checks for vLLM and visual graph enrichment are explicitly
deferred to the Agent Runtime increment.
