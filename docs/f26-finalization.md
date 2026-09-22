# F26 finalization — F26.31 to F26.34

## F26.31 — Shadow mode

Shadow comparison is deterministic/read-only only. `ShadowModeService` refuses comparisons marked
as side effects. The preserved TypeScript implementation remains reference evidence and is not
deployed as a second production writer.

## F26.32 — Progressive cutover

`CutoverController` implements:

frontend → system → authentication → organizations → workforce → jobs → results → governance →
scheduler → runtime → integrations.

Scheduler and worker processes enforce the applicable stage before doing work.

## F26.33 — Retired-platform removal

CI runs `scripts/verify_no_legacy_platform.py` and requires zero retired-platform references across
active production paths: frontend, backend, infrastructure, GitHub workflows, and active root
project docs.

Historical references under `reference/` and `docs/` remain permitted for migration evidence.

## F26.34 — Production architecture

The final target is documented in `docs/f26-production-architecture.md`; rollback is documented in
`docs/f26-rollback.md`.

The target is GitHub → Vercel frontend + Render FastAPI/API/worker/scheduler + PostgreSQL + Redis +
object storage. Render compute remains deliberately unprovisioned until a paid compute plan is
explicitly selected, so the overall F26 production cutover is not yet fully complete.
