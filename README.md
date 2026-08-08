# paper-agent

A local-first workspace for reading AI/ML papers. This repository currently
implements the parsing and persistence increment: a local FastAPI service can
accept a PDF, parse and persist its document data, serve the original source
PDF and page images, and store paper-scoped notes. The knowledge graph, agent
runtime, and web UI increments are not included yet.

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
