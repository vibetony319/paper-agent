# Task 5B review fix round 1 report

Base commit: `93a5eb5`

Commit: `fix: harden paper API error contracts`

## TDD evidence

### Red

Command:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/integration/test_papers_api.py::test_page_image_invalid_page_paths_are_not_found_after_upload tests/integration/test_papers_api.py::test_notes_accept_same_paper_page_and_reject_nonexistent_page tests/integration/test_papers_api.py::test_paper_summary_maps_unknown_persisted_error_to_safe_message -v
```

Result: 2 failed, 1 passed. The page-image test received 422 for the non-integer segment, and the summary test returned the persisted local path. The page-targeted note test already passed, confirming its existing service-level ownership validation before adding regression coverage.

### Green

Command:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/integration/test_papers_api.py::test_page_image_invalid_page_paths_are_not_found_after_upload tests/integration/test_papers_api.py::test_notes_accept_same_paper_page_and_reject_nonexistent_page tests/integration/test_papers_api.py::test_paper_summary_maps_unknown_persisted_error_to_safe_message -v
```

Result: 3 passed.

### Full suite

Command:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -v
```

Result: 40 passed.

## Files

- `src/paper_agent/routes/papers.py` — parse page-image path segments at the route boundary and map malformed values to 404.
- `src/paper_agent/services/ingestion.py` — allowlist current public processing errors and map other persisted text to a fixed public message.
- `tests/integration/test_papers_api.py` — cover invalid page paths, page-targeted notes, and persisted-error sanitization.
- `.superpowers/sdd/2026-08-08-parsing-persistence/task-5b-fix-1-report.md` — this report.
