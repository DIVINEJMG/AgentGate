# Aduoryn Python backend

This directory is the active Python backend introduced by F26.

The TypeScript implementation is preserved under `reference/typescript-backend/` and in the frozen
F25 checkpoint for behavioral parity evidence. Python/FastAPI is the migration target; domains are
considered complete only after contracts, persistence, security invariants, and parity checks pass.
