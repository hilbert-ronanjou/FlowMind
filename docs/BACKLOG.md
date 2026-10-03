# Backlog

This file records ideas that are outside the active Sprint scope. Items here are not approved for implementation.

## V1.0 later-phase modules

- AI content parsing
- Course material knowledge base / RAG
- AI study planning

## Deferred platform work

- Beyond the M1-B HTTP delivery baseline: HTTPS/domain setup, CD, and managed cloud deployment (not authorized in M1-B)
- Object storage for future course materials
- Production observability and operational runbooks
- Document and Course deletion currently removes the local PDF before the database delete commit. A database commit failure can therefore leave a retained row whose file is missing. Keep this as a known limitation until a separately scoped consistency design; do not change the current upload, retry, download, delete, or stale-recovery behavior during M1-A.

## M4-B and later cost-protection work (not authorized by M4-A)

- PDF upload/retry quotas, maximum extracted pages/text/chunks, complete-job embedding reservation, and per-user/global background execution slots.
- Atomic background attempt claim/fencing and stale-attempt recovery, preventing duplicate embedding costs and old workers overwriting newer results.
- Response replay/caching, precise monetary billing/reconciliation, and higher-throughput quota buckets are deferred; they are not required for the foundational M4-A interactive operation ledger.

The current M4-A batch is documentation only. See [the Owner Review plan](M4_A_AI_PROTECTION.md); do not implement these deferred items without a separate scope.

## RAG quality observations

- `paraphrase_channel` and `paraphrase_headcount`: evidence was retrieved successfully at Top-1; the failure appears in the grounded answer / answerability stage, where Qwen conservatively abstained. Investigate semantic-paraphrase answer sensitivity only in a future explicitly scoped optimization milestone; do not change Ground Truth or add threshold/reranking/prompt tuning during Sprint 2.
- The measured median total query latency is approximately 9.35 seconds. Treat latency and perceived waiting time as a future UX optimization candidate, but do not modify the production Prompt, Top-K, Embedding, Chunking, Retrieval, Provider, or business pipeline during Sprint 2.

Each item requires its own scoped sprint, acceptance criteria, and architecture review before implementation.
