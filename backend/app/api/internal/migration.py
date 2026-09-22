from fastapi import APIRouter, Header, HTTPException, Request, status

from app.bootstrap.settings import settings
from app.infrastructure.qstash.verifier import QStashSignatureVerifier
from app.migration.provider_cutover import migrate, shadow_verify

router = APIRouter(prefix="/internal/v1/migration", tags=["internal-migration"])


def _verify_qstash(request: Request, body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )
    try:
        QStashSignatureVerifier().verify(
            body=body.decode("utf-8"),
            signature=signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc


@router.post("/shadow")
async def shadow(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    body = await request.body()
    _verify_qstash(request, body, upstash_signature)
    if settings.legacy_database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LEGACY_DATABASE_URL is not configured.",
        )
    report = await shadow_verify()
    return {"status": "ok", "tables": report}


@router.post("/run")
async def run_migration(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    body = await request.body()
    _verify_qstash(request, body, upstash_signature)
    if settings.legacy_database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LEGACY_DATABASE_URL is not configured.",
        )
    if not settings.migration_execution_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Migration execution is disabled.",
        )

    migration = await migrate()
    shadow = await shadow_verify()
    return {
        "status": "completed",
        "migration": migration,
        "shadow": shadow,
        "allShadowTablesMatched": all(
            bool(result["matched"]) for result in shadow.values()
        ),
    }
