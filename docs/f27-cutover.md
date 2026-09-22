# F27 Cutover

Cutover order:
1. Neon PostgreSQL.
2. Upstash Redis.
3. Upstash Blob.
4. FastAPI API.
5. QStash scheduling.
6. Results/artifacts.
7. Scheduler authority.
8. Runtime authority.
9. Integrations.

Staging reached CUTOVER_STAGE=runtime after QStash scheduling verification. RUNTIME_EXECUTION_ENABLED=false remains mandatory until runtime parity is explicitly approved.

Production cutover is separate from staging and must not reuse staging resources.
