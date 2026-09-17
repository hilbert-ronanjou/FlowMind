# FlowMind AI Cloud Development Rules

1. Read `docs/SCOPE.md` before modifying code.
2. Do not implement business functionality that is not defined in `docs/SCOPE.md`.
3. Record new ideas in `docs/BACKLOG.md`; do not implement them directly.
4. Prefer simple implementations and avoid premature optimization.
5. Do not create abstraction layers without current business value.
6. Keep the number of database tables deliberately small.
7. Explain the current business reason for every database schema change.
8. Run the relevant tests after every completed task.
9. Frontend verification must include lint, TypeScript checking, and a production build.
10. Backend verification must include pytest.
11. Never hard-code passwords, secrets, or API keys.
12. Put all environment-specific configuration in environment variables.
13. Every new feature must have explicit acceptance criteria.
14. Sprint 0 must not implement AI, RAG, document upload, study planning, or cloud-service features.
15. Do not delete or weaken tests to make a failing build pass.
