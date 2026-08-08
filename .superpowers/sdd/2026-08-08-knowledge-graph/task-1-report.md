# Task 1 Report: Evidence-backed graph domain and SQLite repository

## Status

DONE

## Files changed

- `src/paper_agent/domain.py`: Added immutable `GraphNode`, `GraphEdge`, and
  `PaperGraph` value objects; `GraphStage`; controlled node/relation
  vocabularies; strict validation for graph values and evidence IDs.
- `src/paper_agent/database.py`: Added `graph_nodes`, `graph_edges`,
  `graph_node_evidence`, and `graph_edge_evidence` with per-paper composite
  foreign keys and normalized-name uniqueness.
- `src/paper_agent/storage.py`: Added graph reference validation, atomic stage
  replacement, graph reads, neighbors, breadth-first subgraphs, and directed
  path discovery.
- `tests/unit/test_domain.py`: Added graph-domain validation coverage.
- `tests/unit/test_storage.py`: Added evidence ownership/location, atomic
  rollback, stage replacement, normalized-name, and traversal coverage.

## TDD red/green evidence

1. Added graph-domain tests before graph production code. The first focused
   run failed during collection with `ImportError: cannot import name
   'GraphEdge'`, because the graph value objects did not yet exist. After the
   minimal domain implementation, the domain suite passed (7 tests).
2. Added graph-storage tests before graph table/storage production code. The
   first focused run failed during collection with `ImportError: cannot import
   name 'graph_nodes'`, because the graph schema did not yet exist. After the
   schema and repository implementation, the storage suite passed (28 tests).
3. The subsequent focused regression passed all 35 domain and storage tests.

The initial command using the global `python` failed only because that
interpreter lacks `pytest`; all test evidence above uses the repository's
`.venv` interpreter.

## Test commands and results

- `.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_domain.py -v` —
  PASS, 7 passed.
- `.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_storage.py -v` —
  PASS, 28 passed.
- `.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_domain.py
  tests/unit/test_storage.py -v` — PASS, 35 passed.
- `.\\.venv\\Scripts\\python.exe -m pytest -v` — PASS, 75 passed.
- `git diff --check` — PASS, no whitespace errors.

## Commits

- `feat: persist evidence-backed paper graphs`

## Self-review

- Preserved existing `PaperRepository`, `DocumentElement`,
  `ProcessingStatus`, `document_elements`, and source/page ownership behavior.
- Kept graph nodes and edges frozen domain objects; normalized names are
  persisted only in `graph_nodes` as case-folded values.
- Enforced located, same-paper evidence in repository validation and via
  composite SQLite foreign keys for graph evidence and endpoints.
- Confirmed stage replacement is one transaction and that core replacement
  deletes deep records before core records; rollback preserves the prior graph.
- Kept scope to domain, database, storage, tests, and this required report;
  added no HTTP routes, agents, or vLLM work.

## Concerns

- No material implementation concerns. A separate code-review agent was not
  available in this environment; the change received manual requirement
  review, `git diff --check`, focused tests, and the full test suite instead.
