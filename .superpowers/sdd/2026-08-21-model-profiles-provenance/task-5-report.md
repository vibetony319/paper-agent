# Task 5 Report: Agent request model selection and snapshots

## Status

The initial implementation was committed at `c109425`. Its independent review found five Important issues, and fix round 1 closed all five. The fix round received a fresh independent re-review on 2026-08-23 and was committed as `fix: harden agent model provenance replay`. Task 5 is complete.

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

## Fix round 1 resolution — 2026-08-23

Review findings being addressed:

1. Exact replay must include background explanation, citation order and original geometry.
2. Material model-profile edits must invalidate capability checks, and request start must be protected from concurrent deletion.
3. An unusable original profile during partial retry must produce the stable 409/new-request-ID conflict before model execution or another write.
4. The process-local request-lock registry must release idle keys without breaking same-key waiters.
5. Row/snapshot and user/assistant provenance mismatches must expose `model=null`.

Working-tree implementation adds frozen migration 2 response snapshot columns, strict citation snapshot parsing, provenance parity checks, material-edit capability reset, a process-local model-profile usage lease, stable partial-retry conflict mapping, and reference-counted request-lock cleanup.

Fresh verification on the uncommitted working tree:

- Focused migrations/storage/runtime/profile/API suites: `164 passed in 31.72s` with `-W error -o pythonpath=src`.
- Full backend: `329 passed in 51.15s` with `-W error -o pythonpath=src`.

Independent re-review outcome (2026-08-23): clean, no new findings. The five review points resolve as follows:

1. Frozen migration 2 adds nullable `background_explanation` plus citation `ordinal` and allow-listed `citation_snapshot_json`; migration 1 is unchanged. Replay rebuilds the stored response before provider resolution, so original explanation, order, and geometry are returned unchanged.
2. Material profile edits reset capabilities in the same revision; `usage_lease()` registers request use under the mutation lock before a request resolves its model, and deletes conflict while edits are allowed to finish existing frozen requests.
3. Partial retries resolve the original profile inside the lease. Missing, deleted, disabled, unresolvable, or changed-snapshot profiles map to the stable 409/new-request-ID conflict before any model call or write.
4. Request locks use waiter-safe reference counting and remove their key only after owner and waiters leave, so the registry cannot grow without bound.
5. Both read paths sanitize mismatched or incomplete user/assistant provenance pairs to `model: null`; corrupt citation snapshots fall back to immutable element data.

## Concerns

- Per the approved deployment constraint, idempotency, secret locking and profile usage leases are guaranteed only inside a single service process; no database uniqueness migration or cross-process lock was added.
