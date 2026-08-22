# Task 5 Report: Agent request model selection and snapshots

## Status

Implemented and verified. Agent requests now require a model profile and request ID, resolve a request-scoped vLLM tools client, persist a secret-free immutable model snapshot on both durable messages, and replay complete duplicate requests without provider/model invocation.

## Implementation

- Added nullable `model_profile_id`, `model_snapshot`, and `request_id` fields to `ConversationMessage`; legacy rows remain readable with `model_snapshot=None`.
- Added strict allow-list snapshot JSON serialization/parsing. Invalid UUIDs, types, revisions, extra fields, malformed JSON, and credential-bearing URLs are never materialized into public snapshots.
- Persisted identical request/model audit metadata on the user and assistant rows while preserving paper scope, conversation ownership, sequence allocation, and citation ownership checks.
- Preserved `get_agent_turn_by_request(paper_id, request_id) -> complete pair | None` and added narrow `get_agent_user_message_by_request(...)` recovery lookup.
- Removed the fixed tools client from `PaperAgentRuntime`; `ask(...)` now accepts request-resolved `client`, `model_snapshot`, and `request_id`.
- Added a process-local lock keyed by `(paper_id, request_id)`. Runtime rechecks complete and partial durable state inside the lock before writes or model calls.
- Complete duplicates rebuild the stored turn and citations. User-only retries reuse the original row and require unchanged content, conversation/mode, profile ID, and exact current snapshot; conflicts direct callers to a new request ID.
- Agent route performs the complete-pair lookup before provider resolution, resolves the original profile for a partial retry, gates DB profiles on structured output plus tool calling, and safely maps resolution/runtime failures.
- Environment fallback remains directly usable for compatibility. Agent tool health now resolves a current default/fallback client per request; Graph retains the startup structured client.
- Added `ModelSnapshotResponse`; Agent message responses and conversation history expose `model`, with legacy/corrupt rows returning `null`.

## TDD evidence

- Baseline: `289 passed in 42.89s`.
- Storage RED: 11 failures because `ConversationMessage` lacked model metadata; GREEN: `44 passed`.
- Runtime RED: 26 failures after removing the constructor client and requiring request-scoped arguments; GREEN: `26 passed`.
- API RED: requests were rejected because required model/request fields and model response fields were absent; after app wiring was advanced, 11 behavioral failures remained; GREEN: initial `14 passed`, final expanded API suite `19 passed`.
- Security RED/GREEN: credential-bearing snapshot URL was materialized, then rejected (`1 failed` -> `1 passed`); unexpected provider exception returned 500, then sanitized to 503 (`1 failed` -> `1 passed`).
- First full run after implementation: `302 passed, 2 failed`; both failures were stale assertions requiring the deliberately removed fixed runtime client. Updated them to assert no runtime client while preserving Graph compatibility; affected suite: `12 passed`.

## Final verification

- Focused storage/runtime/API: `89 passed in 23.40s` with `-W error`.
- Full backend: `309 passed in 49.29s` with `-W error`.
- `git diff --check`: clean apart from Git's existing LF-to-CRLF checkout notices.
- No `web/`, Graph implementation, `sources/`, migration schema, or `AGENTS.md` changes.

## Files

- `src/paper_agent/domain.py`
- `src/paper_agent/storage.py`
- `src/paper_agent/services/agent_runtime.py`
- `src/paper_agent/routes/agent.py`
- `src/paper_agent/schemas.py`
- `src/paper_agent/app.py`
- `tests/unit/test_storage.py`
- `tests/unit/services/test_agent_runtime.py`
- `tests/integration/test_agent_api.py`
- `tests/integration/test_model_profiles_api.py` (updated stale fixed-client wiring assertions)
- `.superpowers/sdd/2026-08-21-model-profiles-provenance/task-5-report.md`

## Self-review

- Pair/partial idempotency: complete pairs replay first; only an exact user-only state is recoverable.
- Concurrent same-key calls: covered with two threads; one assistant ID and one model/final call.
- Provider-before-write and capability gating: missing, disabled, deleted, insufficient, and unexpected-failure paths leave chat counts unchanged.
- Conversation semantics: paper ownership and mode remain enforced; model switching keeps the same conversation.
- Partial state immutability: content, conversation, model, mode, and snapshot changes return stable 409/new-request-ID guidance; no second user row is appended.
- Audit parity: user/assistant rows share request ID, profile ID, and exact snapshot; raw JSON uses only five public fields.
- Snapshot secrecy: extra-key and credential-URL corruption becomes `model=null`; raw payloads do not reach domain/API representations.
- Retry behavior: provider/model and unexpected guard failures leave a recoverable user-only state; Citation Guard fallback remains a verified assistant response.
- Replay behavior: provider and model are skipped and stored citation geometry is returned unchanged.
- Health/startup compatibility: no fixed Agent client; provider-resolved default/fallback health remains functional; Graph startup client behavior is unchanged.
- Tests complete without warnings under `-W error`.

## Concerns

No open implementation concerns. Per the approved deployment constraint, idempotency is guaranteed only inside this single service process; no database uniqueness migration was added.
