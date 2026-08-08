# Paper Agent MVP Design

## Purpose

`paper-agent` is a local-first research workspace for deeply reading one AI/ML paper at a time. Its job is not merely to summarize a PDF: users can read the source, browse an evidence-backed paper graph, ask questions, and jump from every answer citation to the precise source paragraph, figure, table, or equation.

## Product Scope

The MVP supports local PDF upload for a single personal user. It targets the common structure of AI/ML/NLP/CV papers and prioritizes accurate source navigation, method understanding, result comparison, and ablation analysis.

The intended finished experience has three panes:

1. **PDF reader** — original PDF, page rendering, selection, and citation highlighting.
2. **Paper graph** — a navigable visualization of the paper's problem, methods, claims, experiments, and evidence.
3. **Agent** — paper-grounded question answering and explanations with source citations.

The first implementation increment intentionally delivers only the durable foundation required by all three panes: parsed document data, durable storage, and source asset access.

## Accepted Architectural Decisions

### Local-first and vLLM-only

Paper files, extracted document data, notes, and configuration remain local by default. The application integrates only with OpenAI-compatible vLLM endpoints; it does not introduce a provider gateway or multi-provider abstraction.

Model configuration is role-based:

- `reasoning` is required for future graph extraction and agent work.
- `vision` is optional for figures and parser failures.
- `embedding` is optional for future search.

The application must remain useful without a configured vision model. Visual interpretation degrades to captions and surrounding source text, rather than claiming visual facts it has not observed.

### Parser stack

The document parser is deliberately split by responsibility:

```text
PDF
 ├─ MarkItDown ── semantic Markdown: headings, prose, lists, simple tables
 └─ PyMuPDF ───── geometry: pages, blocks, words, images, bounding boxes, rendering
                         │
                    alignment layer
                         │
                    PaperDocument
```

MarkItDown supplies LLM-friendly semantic text. PyMuPDF is the authoritative source for PDF geometry and original rendered assets. No business component depends directly on either library's result schema.

The alignment layer normalizes semantic and geometric text, matches Markdown paragraphs to PyMuPDF blocks, and records the matched element's page and normalized bounding box. An unmatched semantic element is preserved with an explicit `unlocated` state; it is never assigned a guessed location.

### Evidence is the source of truth

The paper graph and Agent will navigate source material, but final claims about a paper must cite source elements. A citation target is one of:

- paragraph
- figure and caption
- table and caption
- equation

An answer without validated source targets must not be streamed to the UI as if it were paper-grounded. Where evidence is unavailable, the Agent must say so.

### Knowledge graph model

The graph uses a fixed semantic skeleton plus dynamic paper concepts. Stable node types are:

`Paper`, `Problem`, `Contribution`, `Claim`, `Method`, `Component`, `Concept`, `Experiment`, `Dataset`, `Metric`, `Result`, `Ablation`, and `Limitation`.

The initial relation vocabulary is:

`addresses`, `part_of`, `uses`, `compares_with`, `evaluated_on`, `measured_by`, `produces`, `tests`, `supports`, `contradicts`, `defines`, `illustrates`, and `related_to`.

Each graph node and edge must reference one or more document elements. The MVP does not expose editable graph authoring, but its data model leaves room for it later.

### Progressive processing

Paper processing is incremental so a reader does not wait for deep semantic extraction:

```text
Stage 0 — source geometry and assets
  pages, blocks, words, candidate figures/tables, PDF rendering

Stage 1 — document structure
  title, sections, paragraphs, captions, references, MarkItDown alignment

Stage 2 — core graph (later increment)
  problem, contribution, method, claim, experiment

Stage 3 — deep graph (later increment)
  components, concepts, equations, results, ablations, limitations
```

Stage 0 and Stage 1 are the first deliverable. Their status and failures are persisted and queryable independently.

## Data Model

The application owns a library-independent `PaperDocument` model:

```text
PaperDocument
├─ Paper metadata
├─ Page[]
├─ Section[]
├─ DocumentElement[]
│  ├─ paragraph
│  ├─ figure
│  ├─ table
│  ├─ equation
│  ├─ caption
│  └─ reference
└─ processing state
```

Every locatable document element records a 1-based `page_number` and a normalized bounding box `{x0, y0, x1, y1}`, all coordinates in `[0, 1]`. Pixel coordinates are derived only when rendering a page. IDs are stable UUIDs, not parser-index positions.

SQLite is the persistence layer for the MVP. Uploaded original PDFs and optional page renderings are stored in a local application-data directory and referenced by immutable file names. The first database schema includes papers, processing runs, pages, sections, document elements, and notes. Graph tables, conversations, messages, and model configuration follow in later increments.

## Increment 1: Parsing and Persistence

### Responsibilities

- Initialize a FastAPI application and SQLite database.
- Accept a PDF upload and store its original file locally.
- Run Stage 0 with PyMuPDF to persist page geometry and text blocks.
- Run Stage 1 with MarkItDown plus the alignment layer to persist sections and paragraph elements.
- Detect candidate figure/table/image regions through PyMuPDF block metadata; preserve their source locations even when no semantic understanding is available.
- Expose status, parsed document metadata, and original-PDF asset endpoints.
- Support manual notes attached to a paper or source element.

### API surface

```text
GET  /health
POST /api/papers                  upload a PDF and create a processing run
GET  /api/papers/{paper_id}       paper metadata and processing state
GET  /api/papers/{paper_id}/document
GET  /api/papers/{paper_id}/source
GET  /api/papers/{paper_id}/pages/{page_number}/image
POST /api/papers/{paper_id}/notes
GET  /api/papers/{paper_id}/notes
```

The upload endpoint may process synchronously in the first local-only cut, but its state machine must distinguish `queued`, `running`, `completed`, and `failed`, so a background worker can replace the synchronous execution without changing API responses.

### Failure behavior

- A non-PDF upload is rejected before storage.
- A malformed or encrypted PDF ends in a persisted failed processing run with a safe human-readable error.
- A MarkItDown conversion failure does not erase Stage 0 geometry; the paper is reported as partially processed and continues to serve its original PDF and extracted blocks.
- An unmatched Markdown paragraph remains stored with `location_status = "unlocated"`.
- Neither parser output nor model-generated text can overwrite original uploaded bytes.

## Deferred Increments

### Increment 2: Knowledge graph

Extract nodes per section, deduplicate them, then extract edges separately. Store evidence source-element IDs with every node and edge. Provide graph query APIs and incremental Stage 2/3 status.

### Increment 3: Agent runtime

Add paper and graph tools, vLLM configuration validation, model-tool-call orchestration, and a citation guard. In `Paper only` mode, factual statements about the paper require evidence. An optional external-knowledge mode partitions paper claims from background explanation.

### Increment 4: Web UI

Add a React-based local web application with the PDF reader, a focused graph rather than an unbounded spider-web, notes, and the citation-aware Agent. Citation clicks coordinate PDF page navigation and highlight an element's normalized bounding box.

## Non-goals for the MVP

- User accounts, teams, sharing, billing, or cloud-hosted storage.
- arXiv/DOI/web ingestion.
- Full scientific-layout reconstruction or equation OCR benchmark leadership.
- A general model-provider gateway.
- Neo4j or another graph database.
- Multi-agent orchestration.

## Quality Requirements

- All Python domain and persistence behavior is tested before implementation.
- Parsing code uses parser adapters, not parser-specific values, outside the adapter package.
- Public responses expose normalized source locations and never raw local storage paths.
- Tests must cover a normal PDF, invalid input, partial parser failure, and an unlocated semantic element.
- The repository is structured so the backend can be tested without a configured vLLM endpoint or a browser UI.
