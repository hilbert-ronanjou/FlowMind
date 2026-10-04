# Sprint 3 / M4-A: Interactive AI Protection

Status: Round 1 and Round 2 implemented; Owner reports CI acceptance and production deployment before M4-B. Updated: 2026-10-04. Design frozen: 2026-10-03.

This is the frozen M4-A contract and implementation record. Round 1 implemented the PostgreSQL reservation ledger, migration `20261003_0003`, and concurrency-safe quota service. Round 2 integrated authenticated extraction and Course RAG queries, bounds, zero interactive retries, idempotency, emergency shutdown and conservative settlement. Both rounds passed local acceptance using a disposable PostgreSQL/pgvector database and mocked providers. The Round 2 baseline passed 250 backend tests (0 failed, 0 skipped), including the existing 30 PostgreSQL quota tests; a follow-up regression additionally covers an original AI failure combined with settlement failure.

Owner reports that M4-A passed CI and was deployed before authorizing M4-B; production was not accessed to independently verify that report in the M4-B task. Further rollout, paid smoke tests, commits and tags require separate authorization. M4-B now has its separate [minimal PDF protection contract](M4_B_PDF_PROTECTION.md). Sections 1-10 retain the original frozen M4-A design and implementation/acceptance plan; proposal/deferred wording and source anchors there describe the M4-A milestone boundary, not the current M4-B delivery status. This status update does not change any frozen rule.

Follow-up validation (2026-10-04): the new six-case mock-provider regression passed, preserving exact original error responses for extraction, RAG embedding and answer failures/timeouts while the real best-effort settlement handler encounters quota-storage failure. The complete Backend suite in that earlier follow-up returned 178 passed, 0 failed and 78 skipped because local Docker was unavailable; those skips were not PostgreSQL acceptance. The later M4-B acceptance supersedes that pending rerun: 297 passed, 0 failed, 0 skipped using disposable PostgreSQL 16.15/pgvector 0.8.6 and mock providers, including all 30 M4-A PostgreSQL cases. Sections 1-10 remain unchanged.

FlowMind is a foundational AI/Agent learning project, not the final job-search portfolio project. Prefer one small PostgreSQL service over new infrastructure or speculative abstractions.

## 1. Confirmed baseline and boundaries

| Existing surface | Confirmed implementation anchor | M4-A treatment |
| --- | --- | --- |
| JWT identity | `backend/app/core/security.py:30 decode_access_token`; `backend/app/api/deps.py:16 get_current_user`, `:34 CurrentUser` | Reuse verified `current_user.id`; no new authentication system |
| Extraction | `backend/app/api/routes/ai.py:31 extract_content`; `backend/app/services/ai/extractor.py:19 extract_with_client`, `:46 extract_course_content` | One protected interactive operation |
| Course question | `backend/app/api/routes/knowledge.py:76 _execute_query_course_knowledge`; `backend/app/services/embedding.py:107 embed_text`; `backend/app/services/knowledge/grounded_answer.py:85 generate_grounded_answer` | One protected operation covering question embedding and optional Qwen answer |
| Ownership/retrieval | `backend/app/api/routes/courses.py:17 owned_course_or_404`; `backend/app/services/knowledge/retrieval.py:36 search_ready_chunk_candidates` | Preserve ownership checks before paid dispatch and User/Course/READY retrieval filtering |
| PDF background work | `backend/app/api/routes/documents.py:75 upload_document`, `:298 retry_document`; `backend/app/services/knowledge/ingestion.py:315 process_document`, `:362 recover_stale_processing_documents` | Quotas and attempt fencing deferred to M4-B; shared emergency switch only |
| Database | `backend/app/database.py:14 engine`, `:15 SessionLocal`; repository migration head `20260920_0002` | Reuse PostgreSQL, SQLAlchemy and Alembic; one new ledger after approval |
| Metrics | `backend/app/core/observability.py:164 observe_ai_call` | Preserve existing bounded metrics; never use process counters as the quota authority |

Source anchors describe the audited baseline; implementation will move line numbers. Existing registration can create multiple accounts, so the global cap is essential. Login/token refresh must not reset a user's quota.

No existing rate/quota ledger was found. Course document limits and file uniqueness are not interactive quotas. One FastAPI worker still allows concurrent requests; do not rely on Python counters, a process mutex, or worker count for correctness.

## 2. Frozen quota contract

The two endpoints share one combined interactive allowance, not separate allowances per endpoint. A reservation represents one admitted operation, not one HTTP attempt to the provider and not a monetary amount.

| Proposed Backend runtime setting | Initial value | Meaning |
| --- | --- | --- |
| `AI_PAID_OPERATIONS_ENABLED` | `true` | Shared emergency dispatch switch |
| `AI_INTERACTIVE_USER_WINDOW_LIMIT` | `5` | Operations per user in the rolling window |
| `AI_INTERACTIVE_WINDOW_SECONDS` | `60` | Rolling window duration |
| `AI_INTERACTIVE_USER_DAILY_LIMIT` | `20` | Combined operations per user per UTC day |
| `AI_INTERACTIVE_GLOBAL_DAILY_LIMIT` | `100` | Combined operations across all users per UTC day |
| `AI_EXTRACTION_MAX_INPUT_CHARS` | `10000` | Extraction text character cap |
| `AI_RAG_MAX_PROMPT_CHARS` | `24000` | Sum of complete system/user message-content lengths |
| `AI_QWEN_MAX_OUTPUT_TOKENS` | `4096` | Generation cap passed to both interactive Qwen calls |

All numeric settings must be positive validated integers. No unlimited/zero bypass mode. All Backend processes sharing the database must use the same settings; coordinated restart/recreation applies changes. Do not expose these values through `NEXT_PUBLIC_*` or add an operator UI.

Counting rules:

- Verify JWT, request bounds, Course ownership, provider configuration and emergency switch before admission where possible. Invalid/unauthenticated/foreign-Course requests make no reservation and no provider call.
- Extraction costs one operation once admitted.
- RAG with no READY material returns the existing no-answer response without admission or provider calls. RAG admitted before question embedding costs one operation, including empty retrieval, unanswerable results, and embedding-only failures.
- Qwen plus question embedding is still one RAG operation. No extra decrement between stages.
- Every committed reservation counts, regardless of its later status. Failures, cancellations and uncertain calls do not receive automatic refunds. This deliberately conservative operation policy avoids refund races and failure-loop abuse.
- No row is inserted for a rejected quota request. Return safe HTTP 429 with a bounded reason and `Retry-After` computed from the expiration of enough counted records to admit the next operation under the current limits, including after a quota reduction. For rolling-window count `N >= L`, order the counted records by `created_at` ascending and wait for record `k = N - L + 1` to expire at `created_at[k] + window` (1-based), not always the oldest record. Exhausted UTC-day records expire together at next UTC midnight. Take the latest required eligibility time across the violated limits; use integer seconds rounded up, at least one second while rejected. This is an estimate without intervening admissions, not a reserved future slot. Do not expose global usage or another user's data.
- Emergency disable or quota-storage/lock failure returns safe HTTP 503 before paid dispatch. Preserve existing safe provider-error mappings, not provider exception details.
- Health, metrics, CRUD, source-file access and `POST /api/v1/ai/import` are not charged. PDF upload/retry/background embedding do not consume this interactive ledger in M4-A.

The 100/day cap is not an account-wide spending guarantee: PDF ingestion and scripts constructing their own provider clients are not counted. An admitted RAG may send two provider requests. There is no token-price conversion or monetary billing.

## 3. One PostgreSQL ledger

Proposed table: `ai_usage_reservations`. One row equals one operation; no balance, bucket, invoice, subscription, token-ledger or response-cache table.

| Column | Proposed definition |
| --- | --- |
| `id` | UUID primary key, generated by application |
| `user_id` | Non-null FK to `users.id`, `ON DELETE RESTRICT` |
| `operation` | Bounded string checked to `extraction` or `rag_query` |
| `idempotency_key` | Non-null string, at most 128 ASCII characters |
| `request_hash` | Non-null 64-character SHA-256 hex of canonical validated operation inputs |
| `created_at` | Non-null `TIMESTAMPTZ`, admission time obtained from PostgreSQL after locking |
| `state` | Bounded checked string: `RESERVED`, `DISPATCHED`, `SUCCEEDED`, `FAILED`, `UNCERTAIN`, `CANCELLED` |
| `dispatched_at` | Nullable `TIMESTAMPTZ`, committed before first provider dispatch |
| `finished_at` | Nullable `TIMESTAMPTZ`, terminal-state time |

Constraints/indexes: `UNIQUE(user_id, operation, idempotency_key)`, index `(user_id, created_at)` and index `(created_at)`, plus database CHECK constraints with explicit NULL predicates:

- Non-terminal `RESERVED` and `DISPATCHED` require `finished_at IS NULL`.
- Terminal `SUCCEEDED`, `FAILED`, `UNCERTAIN` and `CANCELLED` require `finished_at IS NOT NULL`.
- `DISPATCHED`/`SUCCEEDED`/`FAILED`/`UNCERTAIN` require `dispatched_at IS NOT NULL`; `RESERVED`/`CANCELLED` require `dispatched_at IS NULL`.
- If `dispatched_at` exists, require `dispatched_at >= created_at`. If `finished_at` exists, require `finished_at >= created_at` and, when dispatched, `finished_at >= dispatched_at`. Equal timestamps are valid. Use explicit `IS NULL OR ...` chronological checks together with the state-dependent NULL constraints; do not rely on a nullable comparison alone, because a SQL CHECK can accept UNKNOWN.

All states count equally for quota. These constraints are part of the proposed single-ledger migration/model, not a migration executed in this batch.

Keep global usage rows if a user is deleted; do not cascade them away and create global allowance. Account deletion is not currently a business endpoint; no deletion workflow is added. No relationship/cascade change to existing User/Course/Document models is needed. Do not store source text, questions, prompts, model responses, secrets, token estimates or provider exception strings. Request hashes are internal fingerprints, not anonymization guarantees; never expose them.

No scheduled cleanup is needed at this scale (initial cap bounds normal admission to 100 rows/day). Any future retention/delete policy must preserve active windows and idempotency semantics and needs separate review.

## 4. Exact transaction and locking protocol

Use one reserved, fixed PostgreSQL transaction advisory-lock pair: `(117947, 4)`, named `INTERACTIVE_AI_QUOTA_LOCK` in code. All admission and ledger-state mutations use the same pair. Advisory locks are cooperative: any bypass writer invalidates the guarantee. No per-user-lock/global-lock ordering problem is introduced.

1. Capture the verified integer user ID. Complete read-only request/ownership preflight. End request read transactions before outbound provider I/O; do not keep a request Session connection pinned through long calls. Never commit unrelated business changes in quota code.
2. Open a fresh quota Session/connection with explicit `READ COMMITTED` isolation. Begin a short transaction. Set local `lock_timeout = '2s'` and `statement_timeout = '5s'`.
3. Execute `SELECT pg_advisory_xact_lock(117947, 4)` as its own statement. Do not combine lock acquisition and counts into one statement/CTE; the count must have a fresh post-lock snapshot.
4. After acquiring the lock, execute `SELECT clock_timestamp()` and capture one `t`. Convert this aware instant to UTC and derive UTC midnight boundaries explicitly, independently of the database session timezone (do not use session-local `date_trunc('day', t)` or a local date cast). Do not use transaction-start `now()` captured before a lock wait. A waiter crossing UTC midnight must count and timestamp its admission in the new day using the post-lock `t`.
5. Check for an existing `(user_id, operation, idempotency_key)` first. Existing key means no new reservation or provider dispatch; use the duplicate policy below.
6. Count all committed rows for that user in `(t - window, t]`, that user in `[UTC midnight, next UTC midnight)`, and all users in the same daily interval. Both operations are included. Reject if any count is already at its configured limit.
7. Insert one `RESERVED` row with `created_at = t`, then commit. Close the quota Session. Proceed only after successful commit acknowledgement; uncertain/failed commit means no provider dispatch.
8. Before the first SDK call, use another short transaction with the same advisory lock and conditional `RESERVED -> DISPATCHED` update. Recheck the emergency switch, commit `dispatched_at`, and close the Session. Only the invocation that changed that row may dispatch. If this commit fails or is uncertain, do not call the provider.
   If processing stops before dispatch and it is known that no paid provider attempt was sent, atomically update `RESERVED -> CANCELLED` under the same lock, with a predicate equivalent to `WHERE id = :id AND state = 'RESERVED' AND dispatched_at IS NULL`; set `finished_at` to a chronologically valid database timestamp and require exactly one affected row. Cancellation competes with dispatch marking: a zero-row update must not overwrite the winning state. Only the dispatch-transition winner may send; only the cancellation-transition winner may cancel. This is not a refund. Never transition `DISPATCHED -> CANCELLED`, even when a conservative pre-send dispatch marker might have consumed an unused operation.
9. Call the provider with no database transaction or lock held. For RAG, perform retrieval in a separate short read Session after embedding, close it, then call Qwen. Recheck the emergency switch before each provider stage.
10. Finish dispatched work with another short locked transaction, conditionally updating only `DISPATCHED` to `SUCCEEDED`, `FAILED` or `UNCERTAIN`, with chronologically valid timestamps. No refund/count deletion. If a valid successful operation result has already been obtained but the final ledger update/commit fails, roll back/close that settlement transaction, preserve the already committed counted reservation, return the successful result with the normal HTTP 200 response, and log a safe settlement failure. Do not replace success with an error that encourages a second paid call. If the update committed but its acknowledgement was lost, do not overwrite or delete it; either persisted state remains counted. No automatic second provider call or response cache. For an already-failed operation, preserve its safe original failure response if settlement also fails.

The lock releases at commit/rollback. Lock timeout/database failure aborts admission and returns safe 503; do not fall back to in-memory allowance. Do not automatically replay the whole operation after SQL/commit errors. Provider latency must not extend lock lifetime, and two admitted provider calls may overlap.

`READ COMMITTED` gives each statement a new snapshot, allowing the waiter to see the preceding admission's committed row after obtaining the lock. References: [PostgreSQL transaction advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS) and [Read Committed isolation](https://www.postgresql.org/docs/16/transaction-iso.html#XACT-READ-COMMITTED).

This global lock serializes only a few small indexed queries/writes; it does not serialize provider work. A higher-throughput bucket design is intentionally deferred.

## 5. Retry, duplicate and uncertain-call policy

- Explicitly set SDK `max_retries=0` for extraction, RAG Qwen and RAG question embedding. Keep existing 60-second Qwen and 30-second embedding SDK timeouts; these are not a guaranteed whole-operation deadline. No endpoint/service-level automatic retries.
- Shared embedding helpers may accept a small keyword retry argument. The knowledge-query route explicitly selects zero; `embed_texts` retains its existing default of one for PDF ingestion. Do not accidentally change PDF retry behavior globally.
- Accept an optional `Idempotency-Key` header on the two protected endpoints, bounded to 1-128 ASCII characters. If absent, generate a new UUID key per HTTP invocation; repeated unkeyed submissions are separate operations and consume separate allowance. No Frontend change is required in M4-A.
- Scope keys by trusted user and operation. Hash canonical validated inputs, including Course ID for RAG. Same key with different input returns safe 409. Same key/same input also returns safe 409 (in progress/already processed); no response replay and no new charge. Existing accepted keys do not call again after restart or midnight.
- Mark dispatch before entering the SDK. A crash after marking but before transmission may conservatively consume an unused operation; this is acceptable. Do not claim exactly-once execution at the remote provider.
- Successful operation becomes `SUCCEEDED`; known failed provider responses/invalid output and known local failures after dispatch become `FAILED`; ambiguous connection/timeout/interruption where provider execution or billing cannot be established becomes `UNCERTAIN`. All remain charged. A local failure/disable after admission can become `CANCELLED` only through the atomic `RESERVED -> CANCELLED` transition when no paid attempt was sent.
- Once RAG question embedding has been sent, later retrieval failure, internally assembled prompt overflow or second-stage shutdown must never cancel the reservation. After a confirmed embedding result, these are local post-dispatch failures (`FAILED`); if an earlier provider attempt itself was ambiguous, preserve `UNCERTAIN` instead. No second Qwen call is made on these paths.
- Preserve enough bounded internal error classification through existing helper wrapping to distinguish a timeout/connection uncertainty from a known provider response or local validation/processing failure. For example, `EmbeddingProviderError` must retain an internal uncertainty category or its typed exception cause (`raise ... from exc`), rather than losing it in one generic error string. Missing/ambiguous classification after a started attempt is conservatively `UNCERTAIN`. No new ledger column or public exception detail is required; never classify billing certainty from the public HTTP status alone.
- Safe response policy: internally assembled RAG prompt overflow returns HTTP 502, second-stage emergency disable returns HTTP 503, and known retrieval failures retain the existing safe server-error contract. Successful result plus settlement failure returns the successful HTTP 200 result as specified in step 10. Log settlement failure with a bounded event/reason and existing request correlation only; exclude source text, questions, prompts, responses, credentials, provider payloads, and raw SQL/exception dumps. Settlement logs do not add metric labels or a new observability product.
- If Backend exits with `RESERVED` or `DISPATCHED` rows, leave them counted and non-replayable. No expiry/refund or automatic resume is required. Manual retries with new keys require a new reservation. Do not add a recovery job or modify PDF stale recovery.
- Admission UTC day owns the whole operation, even if it finishes after midnight. Do not move its debit, recount later stages as new operations or erase old in-flight rows. This is an admission quota, not a per-dispatch/calendar spending statement.

## 6. Input/output bounds without algorithm changes

- Reject extraction text above 10,000 characters before admission/Qwen, never silently truncate it. Preserve existing trim/nonblank validation and existing Confirm Import validation.
- Keep the existing RAG question maximum of 2,000 characters. Measure the complete constructed Qwen system/user message content, including serialized context/IDs, against 24,000 characters before Qwen. Internally assembled prompt overflow returns safe HTTP 502, not a client-input validation error; do not drop chunks, truncate evidence, change Top-K or rebuild the prompt. The earlier embedding still consumed its admitted operation, so finish as `FAILED`, never `CANCELLED`. Second-stage emergency disable returns safe HTTP 503 with the same non-cancellation rule.
- Pass `max_tokens=4096` to both Qwen structured calls. This bounds generation, unlike post-response character validation. Keep existing structured extraction fields and grounding/citation validation. A length-truncated/invalid structured response is a charged safe provider-output failure, not an excuse to retry automatically.
- Keep the existing RAG answer cap (8,000 characters), at most five used chunk IDs, and vector length/finiteness validation. No extraction-output schema redesign: the provider generation cap is the new production output budget.
- These input/output values are proposed defaults for Owner approval, not measured provider token equivalences. During implementation verify `max_tokens` with the configured Qwen model/region using the [official Model Studio API documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-api-reference/) and deterministic SDK argument tests. Do not silently omit the bound if unsupported; an explicitly authorized real-provider smoke test is a separate gate.

## 7. Emergency shutdown contract and limitations

`AI_PAID_OPERATIONS_ENABLED=false` blocks new application-managed Qwen and embedding dispatches, including PDF embedding. Put the small check at actual shared provider boundaries, not just HTTP routes; also check before interactive admission. Keep JWT, CRUD, Confirm Import, health and metrics working. A PDF job reaching embedding while disabled follows its existing safe FAILED handling; no new queue or paused state.

Settings are currently cached (`backend/app/core/config.py:get_settings`). Operators must update their private runtime configuration and restart/recreate Backend to apply the switch. Document this exact limitation rather than claiming hot reload. It does not cancel already-sent remote requests or prove they were unbilled. No actual environment change or restart is authorized in this batch.

Direct scripts with their own SDK clients or independent keys are not controlled by an HTTP reservation. Shared FlowMind helpers should respect the switch, but this is not provider-account revocation. If immediate account-wide shutdown is needed, the operator must use the provider credential/account controls; no such cloud action is part of M4-A.

## 8. Exact proposed migration and code file plan

No file in this section is created/modified as application code during the documentation batch.

| File | Planned change after approval |
| --- | --- |
| `backend/alembic/versions/20261003_0003_ai_usage_reservations.py` (new) | Revision `20261003_0003`, `down_revision='20260920_0002'`; create only ledger/constraints/indexes; downgrade drops only ledger. Reconfirm head before coding if another approved migration intervenes |
| `backend/app/models/ai_usage_reservation.py` (new) | Small ledger model and bounded operation/state definitions, matching migration |
| `backend/app/models/__init__.py` | Register/export model; existing Alembic `env.py` already imports this module |
| `backend/app/services/ai_quota.py` (new) | Admission, fingerprint/key validation, conditional dispatch/cancellation/finalization, safe quota exceptions, sufficient-record Retry-After and UTC/window calculation; fixed lock protocol and best-effort settlement preserving successful responses |
| `backend/app/services/ai_guard.py` (new) | Small shared emergency guard/exception; no provider abstraction framework |
| `backend/app/core/config.py` | Validated runtime settings in section 2 |
| `backend/app/api/routes/ai.py` | Use current user, optional key, preflight/admission/dispatch/finalization; preserve `/import` |
| `backend/app/api/routes/knowledge.py` | Same guard around full RAG operation; preserve ownership/no-READY branches; explicit zero retry for question embedding and short read Sessions |
| `backend/app/services/ai/schemas.py` | Extraction request length validation only; no unrelated Import/result changes |
| `backend/app/services/ai/extractor.py` | Dispatch guard, zero retry, Qwen generation bound |
| `backend/app/services/knowledge/grounded_answer.py` | Dispatch guard, zero retry, full-message character check, Qwen generation bound |
| `backend/app/services/embedding.py` | Dispatch guard and explicit per-call retry keyword; retain typed uncertainty through error wrapping; preserve PDF default, model, dimensions and validation |
| `.env.example`, `.env.production.example` | Safe examples of runtime controls; no real values/secrets |
| `docs/DEPLOYMENT.md` | Operation limits, fail-closed behavior, coordinated settings changes and restart-based kill switch |

Do not modify requirements/constraints, Frontend, existing migrations, Compose/Nginx/SLS/monitoring, prompts, retrieval, chunking, Document model/routes/ingestion, or `main.py`. No new API route, quota dashboard or provider call in readiness. Existing providers/metrics remain the integration surfaces.

## 9. Exact proposed tests and acceptance gates

| File | Required coverage |
| --- | --- |
| `backend/tests/test_ai_quota.py` (new) | Settings bounds, UTC/rolling calculations, sufficient-record Retry-After after quota reduction, fingerprints/keys, error classification and conditional state transitions using explicit test settings; no claim of PostgreSQL locking from SQLite |
| `backend/tests/test_ai_quota_api.py` (new) | Combined extraction/RAG limits, unauthenticated and foreign-Course denial, no-READY exemption, normal/empty/unanswerable paths, safe 429/502/503, failure charging, duplicate keys, shutdown and success despite settlement failure |
| `backend/tests/test_ai_quota_postgres.py` (new) | Real PostgreSQL admission/concurrency, cancellation race, timestamp CHECK constraints, non-UTC timezone and time-boundary suite described below |
| `backend/tests/test_ai_api.py`, `backend/tests/test_ai_regressions.py` | Extraction cap, one Qwen attempt, `max_tokens`, charged parse/refusal/timeout failures; retain confirmed-import behavior |
| `backend/tests/test_knowledge_query.py` | One operation spanning embedding+Qwen, question/context limits, zero query retries, no-READY/empty retrieval, grounding and output-cap arguments |
| `backend/tests/test_knowledge_foundation.py` | Shared embedding guard and query retry argument; unchanged PDF embedding defaults/dimensions |
| `backend/tests/conftest.py` | Inject a quota Session factory/test settings; isolate and reset ledger fixtures without weakening existing assertions |

Real PostgreSQL acceptance must use an explicitly disposable local/test database selected through `TEST_POSTGRES_DATABASE_URL`, never production. Apply the future migration only there, after implementation authorization. Use committed setup, independent connections/Sessions and a start barrier; a single connection/savepoint or SQLite cannot prove the lock protocol. Mock every provider, never run paid evaluation from pytest.

Minimum scenarios:

1. No/invalid JWT: 401, zero ledger rows, zero provider calls. Same user's new token retains allowance; different users do not share the per-user window.
2. Normal extraction and RAG share the same 5/60s and 20/day counts. No READY, invalid input and foreign Course do not reserve. Empty retrieval after embedding counts once.
3. Sixth rolling-window operation and twenty-first user-day operation return 429 without provider calls; window expiry/UTC midnight allow new admissions. Check exact boundaries using controlled test clock input, not minute-long sleeps.
4. Global 101st operation fails across different users; quotas cannot be bypassed by switching operation or token.
5. Two concurrent requests with one remaining user allowance: exactly one committed reservation/provider invocation, one 429. Repeat independently for rolling, user-day and global-day exhaustion; use different users for the global race.
6. Prove lock release before provider: block a mocked provider with an event while a separate connection successfully reserves another allowed operation. No lingering quota lock during embedding or Qwen.
7. Timeout/network uncertainty, provider error, invalid vectors, parse/grounding failures and successful unanswerable responses all retain one operation. Assert wrapped embedding timeout/connection errors settle as `UNCERTAIN`, while known provider errors/local validation failures settle as `FAILED`; unknown post-attempt classification remains `UNCERTAIN`. No SDK retry or hidden endpoint retry. RAG must not continue to Qwen after embedding failure.
8. Simultaneous same-key requests: one reservation/dispatch, duplicate 409; changed body/key reuse 409. Repeating the key after a service restart or UTC rollover never dispatches again. Unkeyed/new-key retries count as new operations.
9. Database/lock timeout and unacknowledged admission/dispatch commit prevent paid dispatch. When a valid extraction/RAG result succeeds but settlement update/commit fails, assert HTTP 200 with the unchanged result, a retained counted reservation, one safe settlement-failure log without sensitive content, and no second paid call. Cover known rollback and lost-commit-acknowledgement cases; ledger state may remain DISPATCHED or already be SUCCEEDED, but it must never be removed/refunded. Original failed responses remain unchanged if their settlement also fails. Persisted RESERVED/DISPATCHED rows remain counted after restart; no automatic recovery dispatch.
10. False emergency switch: zero new Qwen/query-embedding/PDF-embedding calls; existing health/CRUD/import work. With switch true, PDF retry defaults and ingestion behavior remain unchanged. After confirmed RAG embedding, disable before Qwen: HTTP 503, zero Qwen calls, `FAILED` with non-null dispatch/finish timestamps and one counted operation, never `CANCELLED`.
11. At/over extraction and prompt caps, generation limit forwarded to both Qwen calls, length-truncated structured results fail safely. After confirmed embedding, assembled prompt overflow returns HTTP 502, zero Qwen calls and `FAILED` (not `CANCELLED`); retrieval failure also retains a dispatched/charged reservation. Existing question/vector/grounding/citation contracts remain valid.
12. Future migration upgrade -> downgrade -> upgrade and Alembic check pass on the disposable PostgreSQL database; existing five tables/rows and vector dimensions are unchanged. Backend full suite and existing document/config/observability tests remain green.
13. Atomic cancellation before any paid attempt: `RESERVED -> CANCELLED` sets `finished_at`, leaves `dispatched_at` NULL and remains charged. Race cancellation against dispatch on independent PostgreSQL connections: exactly one transition wins; cancellation winner sends nothing, dispatch winner cannot later be cancelled. Duplicate cancellation or cancellation of DISPATCHED/terminal rows changes zero rows.
14. Real PostgreSQL rejects non-terminal rows with `finished_at`, terminal rows without it, invalid state/dispatch NULL combinations, `dispatched_at < created_at`, `finished_at < created_at`, and `finished_at < dispatched_at`. Valid equal timestamps pass. Use isolated transactions/savepoints for negative constraint cases without obscuring independent-connection concurrency tests.
15. Non-UTC PostgreSQL session timezone (for example `Asia/Shanghai`): admissions, UTC-day counts and `Retry-After` match an equivalent UTC session at the same aware instant. Do not let local midnight reset daily allowance.
16. Exact rolling 60-second boundary: a record at `t - 60s` is excluded, one just newer is included, and a record at `t` is included. Verify admission and rounded-up integer `Retry-After` at/before/after expiry.
17. UTC midnight boundary: records immediately before midnight belong only to the previous day; records exactly at midnight belong to the new day. An in-flight old-day reservation stays charged to admission day; an accepted key remains non-replayable across the boundary.
18. Advisory-lock wait crossing UTC midnight: synchronize independent connections and use a controlled clock read after lock acquisition; assert admission/counting/created_at and daily `Retry-After` use the new day, not transaction-start time. Separately verify the production database clock path and non-UTC timezone behavior; no minute-long sleeps or production database.
19. Quota reduction: with five rolling records ordered at `t-50s`, `t-40s`, `t-30s`, `t-20s`, `t-10s` and current limit two, three expirations are insufficient; the fourth expiry at `t+40s` allows one next admission. Assert `Retry-After=40`, not 10, ties/fractional rounding, and concurrent daily restrictions taking the longer wait. Repeat reductions of user/global daily limits: all counted daily rows expire at next UTC midnight; rejecting requests add no new row.

## 10. Implementation order and Owner decisions

1. Owner reviews the shared 5/60s, 20/day, 100/day defaults, conservative no-refund admission policy, optional-key 409 behavior, proposed input/output caps, zero interactive retry, and restart-based all-provider emergency switch.
2. After separate coding authorization: create one model/migration and quota service; validate locking first with real PostgreSQL/mock providers.
3. Add runtime settings/guard, extraction wiring and bounds, then the complete RAG operation including its embedding. Preserve ownership/preflight ordering and output contracts.
4. Add API/retry/uncertain/kill-switch regressions, run the full suite and disposable-database migration/concurrency acceptance. Update examples/runbook; no paid smoke test without explicit authorization.
5. Return diff and validation evidence for Owner Review. Commit/tag/deployment remain separately approval-gated. Do not start M4-B automatically.

M4-B will separately address PDF work quotas, page/text/chunk limits, reservation of whole-job embedding work, per-user/global background slots, attempt claiming/fencing and stale-worker recovery. Defer payment/subscriptions/billing, Redis/Celery/queues, response caches, dashboards, new observability, Agent features and new infrastructure. Do not extend the seven-module V1.0 boundary.
