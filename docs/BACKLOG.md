# Backlog

This file records ideas that are outside the active Sprint scope. Items here are not approved for implementation.

## V1.0 later-phase modules

- AI content parsing
- Course material knowledge base / RAG
- AI study planning

## Deferred platform work

- Complete production ingress/deployment, CI/CD, and managed cloud services beyond the M1-A container foundation
- Object storage for future course materials
- Production observability and operational runbooks
- Document and Course deletion currently removes the local PDF before the database delete commit. A database commit failure can therefore leave a retained row whose file is missing. Keep this as a known limitation until a separately scoped consistency design; do not change the current upload, retry, download, delete, or stale-recovery behavior during M1-A.

## RAG quality observations

- `paraphrase_channel` and `paraphrase_headcount`: evidence was retrieved successfully at Top-1; the failure appears in the grounded answer / answerability stage, where Qwen conservatively abstained. Investigate semantic-paraphrase answer sensitivity only in a future explicitly scoped optimization milestone; do not change Ground Truth or add threshold/reranking/prompt tuning during Sprint 2.
- The measured median total query latency is approximately 9.35 seconds. Treat latency and perceived waiting time as a future UX optimization candidate, but do not modify the production Prompt, Top-K, Embedding, Chunking, Retrieval, Provider, or business pipeline during Sprint 2.

Each item requires its own scoped sprint, acceptance criteria, and architecture review before implementation.
