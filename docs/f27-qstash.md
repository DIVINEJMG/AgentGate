# F27 QStash

QStash is the scheduling and queued-delivery authority.

Scheduled messages target signed internal FastAPI endpoints. /internal/v1/runtime/dispatch validates the QStash signature and canonical dispatch payload before creating a PostgreSQL WorkItem.

Idempotency is enforced by (organization_id, idempotency_key). F27.21 live staging verification delivered a scheduled message and a deliberate duplicate while producing exactly one WorkItem.

QStash never bypasses the Action Gateway and never directly performs provider side effects.
