# M4-B: Minimal PDF background processing protection

Status: implemented and locally accepted on 2026-10-04; production deployment is not part of this task.
Owner reports that M4-A is deployed. Its ledger and frozen interactive rules are unchanged.

## Bounds and accounting

Keep measured 20 MiB upload and 20 READY/PROCESSING Documents per Course.
Add positive Backend runtime defaults: `DOCUMENT_MAX_PAGES=80`,
`DOCUMENT_MAX_TEXT_CHARS=150000`, `DOCUMENT_MAX_CHUNKS=200`,
`DOCUMENT_USER_DAILY_LIMIT=2`, `DOCUMENT_GLOBAL_DAILY_LIMIT=20`.
Count normalized extracted text characters across all pages (not tokens or bytes).
Page count is checked before extraction, running text total after each normalized
page, and complete chunk count after the unchanged 1200/200 page-local chunker.
All three checks finish before the first paid embedding. Do not truncate content.
Over-limit PDFs become FAILED with a safe split-file explanation in the existing UI.
The text/page guards are not a sandbox or a hard PDF parser CPU/memory limit.

One accepted upload or accepted manual Retry reserves one whole processing attempt.
Every committed attempt counts against its admission UTC day, even if parsing fails,
processing times out, the emergency switch closes, or it crosses midnight. No refunds.
File/signature/ownership/duplicate/Course-limit rejection and rejected quota requests
create no attempt. An existing PROCESSING or READY Document cannot normally be retried.
FAILED retry gets a new UUID and new daily debit. Duplicate worker invocation never
gets another claim or calls the provider. No automatic SDK/job retries: this ingestion
path explicitly uses `max_retries=0`; the shared helper default and M4-A are unchanged.

There is one active admission per authenticated `CurrentUser.id`, across Courses.
Daily limits count every PDF ledger state, independently of the interactive 100/day.
429 uses a bounded safe reason and Retry-After: UTC midnight for daily exhaustion,
lease expiry for the user slot, or 60 seconds when an old execution still owns the
Document lock. These are retry estimates, not reserved future slots. DB/lock failure
returns safe 503 and never admits work for free. No global totals are disclosed.

## Minimal schema and migration

`20261004_0004` follows `20261003_0003`. It adds nullable
`documents.current_attempt_id` and one infrastructure table
`document_processing_attempts`: UUID id, verified user admission identity,
nullable Document FK, state, created_at, expires_at, dispatched_at, finished_at.
Document ownership remains exclusively `documents -> courses.user_id`; the attempt's
user_id is a quota debit, never an authorization source.
User FK is RESTRICT; Document FK is SET NULL, so deleting a PDF/Course never refunds
its daily/global debit. Unique partial indexes on unfinished user and Document enforce
one active admission. Timestamp/state CHECK constraints distinguish pre-dispatch
from dispatched terminal outcomes and enforce chronology. No content/secrets are stored.

READY/FAILED rows, PDF keys, chunks, vectors and M4-A rows are preserved.
Old PROCESSING rows have no valid admission: migration marks them FAILED/interrupted,
without automatically charging or reprocessing them. Stop the old Backend before
migration so old unfenced code cannot keep writing. Explicit Retry then reserves normally.
Downgrade drops only the new ledger/token, loses its accounting, and does not undo
interrupted statuses; never use automatic production downgrade as application rollback.

## Locking, dispatch and fencing

1. End API ownership preflight read transaction. Start READ COMMITTED; take the
   transaction advisory lock `(117947, 5)` FIRST (before Course/Document row locks).
   Use 2s lock / 5s statement timeouts. Read `clock_timestamp()` AFTER the lock and
   derive UTC bounds, independent of DB timezone/transaction-start time.
2. Recover expired attempts in the same protected transaction, recheck ownership,
   duplicate/Course/status limits, user slot and daily counts. Check the Document
   execution lock without waiting. Insert RESERVED and update the Document token
   in the SAME commit; acknowledge commit before scheduling BackgroundTasks.
3. Worker takes non-blocking session advisory locks `(117949, user_id)` then
   `(117948, document_id)`
   on an AUTOCOMMIT connection. This prevents another worker/Retry starting a second
   local paid chain even if the first lease expired, including another Document
   for the same user. Admission uses the matching
   non-blocking transaction lock; it rejects rather than waiting on the worker.
   No global quota lock, row lock or DB transaction is held over provider I/O.
   The two execution locks share ONE connection per running PDF until exit.
   Always release it before returning the connection to the pool; session death
   releases it in PostgreSQL. No new executor/queue/infrastructure is added.
4. Under a separate short quota transaction, claim only current RESERVED -> RUNNING.
   Duplicate/stale tokens fail. Extract and bound everything with no quota transaction.
5. Before EVERY batch, verify the execution connection is still valid, recheck the
   emergency switch, current token/status/state and unexpired lease. Commit DISPATCHED
   before calling the SDK, and refresh expiry by DOCUMENT_PROCESSING_STALE_SECONDS
   (existing default 1800). SDK still checks the shared emergency switch at its boundary.
6. Finish under the quota lock and Document row lock. Require the current token,
   PROCESSING state, active attempt and lease. Chunks, READY and SUCCEEDED share one
   commit. FAILED cleanup/state and terminal attempt also share one commit. Late old
   success/failure cannot delete chunks or overwrite any newer READY/FAILED state.

No blocking execution-lock acquisition exists, so the worker's execution->quota
order cannot deadlock with admission's quota->try-execution order. M4-A uses its
independent `(117947, 4)` and does not take PDF execution locks.

## Failure and recovery

Pre-paid failure finishes CANCELLED (null dispatched_at), still counted.
Known provider/local failures after dispatch finish FAILED; wrapped timeout/connection
errors or unclassified provider outcomes finish UNCERTAIN. Never DISPATCHED -> CANCELLED.
Dispatch/claim commit failure or lost acknowledgement sends no new provider request.
Failure-state persistence errors emit only a bounded safe event and preserve the debit.
An acknowledged READY commit is never undone by a late failure callback; uncertain
success-persistence failure is not automatically replayed.

Startup recovery and upload/Retry admission recover expired leases: dispatched
attempt -> UNCERTAIN, unsent attempt -> CANCELLED; matching PROCESSING Document -> FAILED.
Only explicit Retry starts new work. An old still-live executor continues to block Retry
until it exits; its result is fenced after expiry. Obsolete worker exit also attempts
safe recovery so polling can see FAILED. DB outages can postpone that recovery.
BackgroundTasks is not durable: crash between commit and scheduling remains counted
and needs stale recovery/manual Retry, not automatic replay.

The application cannot recall a remotely sent request or guarantee exactly-once
billing after a lost DB session/process. Fencing protects persisted results; conservative
debits bound admissions, not precise money. Execution locks protect normally live local
chains, not independent external clients. Keep the existing single Backend worker and
consistent settings. Choose lease duration comfortably above the 30s SDK timeout;
there is no strict whole-job deadline or remote cancellation guarantee.

## Local acceptance

Use only an explicitly disposable loopback PostgreSQL 16 + pgvector database whose
name contains `test`, via TEST_POSTGRES_DATABASE_URL. Mock all providers. Run the
complete backend suite including the unchanged 30 M4-A PG tests, new PDF PG races,
upgrade/downgrade/upgrade and `alembic check`. Frontend lint/typecheck/build validate
only existing error displays. Never substitute skipped database tests for acceptance.
No ECS, production DB, real provider, tag or deployment is authorized here. The
initial implementation batch did not authorize submission; Owner subsequently
authorized final review, small defect fixes, one scoped commit/push and CI acceptance.

### Executed acceptance

- Disposable PostgreSQL 16.15 / pgvector 0.8.6, loopback port 55438, tmpfs data,
  no project/production volume; no real provider calls.
- Complete Backend: **297 passed, 0 failed, 0 skipped**. M4-B: 41 cases
  (36 real-PG scenarios and five Settings-bound tests). M4-A quota regression:
  29 unit + 62 API + 30 PostgreSQL cases, all passed. Existing ingestion: 28;
  real vector integration: two, both passed.
- Real Alembic empty-database upgrade; `0004 -> 0003 -> 0004` CLI roundtrip;
  current `20261004_0004 (head)`; `alembic check`: no new upgrade operations.
  Isolated migration tests preserve original business rows/vectors/M4-A ledger.
- Syntax/import validation: 70 Python files. Frontend lint, typecheck and
  production build passed (Next.js unchanged at 16.3.5). `git diff --check` passed.
- Two existing test-library deprecation warnings (Starlette/httpx/AnyIO), no
  dependency changes or suppressed tests. M4-A frozen sections 1-10 unchanged.

### Changed files

New:

- `backend/alembic/versions/20261004_0004_document_processing_attempts.py`
- `backend/app/models/document_processing_attempt.py`
- `backend/app/services/document_protection.py`
- `backend/tests/test_document_protection.py`
- `docs/M4_B_PDF_PROTECTION.md`

Modified:

- `backend/app/api/routes/documents.py`
- `backend/app/core/config.py`
- `backend/app/models/document.py`
- `backend/app/models/__init__.py`
- `backend/app/services/knowledge/ingestion.py`
- `backend/tests/conftest.py`
- `backend/tests/test_document_ingestion.py`
- `backend/tests/test_vector_integration.py`
- `backend/tests/test_observability.py`
- `backend/tests/test_ai_quota_postgres.py` (extend full-chain migration test;
  preserve all original assertions and quota safety guards)
- `frontend/components/course-knowledge-workspace.tsx` (existing error messages only)
- `.env.example`
- `.env.production.example`
- `docs/SCOPE.md`
- `docs/ARCHITECTURE.md`
- `docs/BACKLOG.md`
- `docs/DEPLOYMENT.md`
- `docs/M4_A_AI_PROTECTION.md` (delivery status only, frozen rules unchanged)

The task comprises 18 modified + five new files. Existing untracked `.idea/`
is unchanged and excluded from submission. No CI, Docker, Nginx, SLS or monitoring changes.

### Final review follow-up

Execution connection setup is now inside its cleanup context: a failure while
selecting AUTOCOMMIT must return the connection even when the exception traceback
remains alive. The new regression reproduced a checked-out connection before the
fix. Three additional real-PG cases verify cleanup after body failure, partial
lock acquisition and unlock failure (which invalidates the DB session).
These changes do not alter admission, dispatch, fencing or accounting rules.

Final local rerun: **301 passed, 0 failed, 0 skipped**, including 45 M4-B cases
(39 real-PG scenarios and six unit cases), all 30 M4-A PostgreSQL tests and both
vector integration tests. DATABASE_URL and TEST_POSTGRES_DATABASE_URL pointed to
the same disposable loopback DB, matching CI's contract. Frontend lint/typecheck/
production build and actual Alembic upgrade/downgrade/upgrade/check passed again.
The two existing test-library warnings remain; no tests or guards were suppressed.
