from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select

from app.domain.artifacts.tenant_storage import TenantObjectStorage
from app.execution.redaction import redact_url
from app.infrastructure.database.models import Artifact
from app.infrastructure.database.session import session_factory
from app.infrastructure.storage.provider import object_storage_from_settings


@dataclass(frozen=True, slots=True)
class BrowserArtifactReference:
    id: UUID
    kind: str
    media_type: str
    size_bytes: int
    checksum_sha256: str
    name: str
    source_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "kind": self.kind,
            "mediaType": self.media_type,
            "sizeBytes": self.size_bytes,
            "checksumSha256": self.checksum_sha256,
            "name": self.name,
            "sourceUrl": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class BrowserArtifactInput:
    id: UUID
    name: str
    media_type: str
    content: bytes
    checksum_sha256: str


class BrowserArtifactStore(Protocol):
    async def store(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        session_id: UUID,
        correlation_id: str,
        operation: str,
        kind: str,
        name: str,
        media_type: str,
        content: bytes,
        source_url: str | None = None,
    ) -> BrowserArtifactReference: ...

    async def load_input(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        artifact_id: UUID,
    ) -> BrowserArtifactInput: ...


def _safe_name(value: str, fallback: str) -> str:
    name = PurePath(value.replace("\\", "/")).name.strip()
    return name or fallback


def _suffix_for(media_type: str, name: str) -> str:
    suffix = PurePath(name).suffix[:16]
    if suffix:
        return suffix
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "application/json": ".json",
    }.get(media_type.lower(), ".bin")


class DatabaseBrowserArtifactStore:
    """Tenant-scoped Browser evidence/input boundary backed by canonical Artifact rows."""

    async def store(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        session_id: UUID,
        correlation_id: str,
        operation: str,
        kind: str,
        name: str,
        media_type: str,
        content: bytes,
        source_url: str | None = None,
    ) -> BrowserArtifactReference:
        if not content:
            raise ValueError("Browser artifact content must not be empty.")

        safe_name = _safe_name(name, f"{kind}.bin")
        safe_source_url = redact_url(source_url) if source_url else None
        checksum = hashlib.sha256(content).hexdigest()
        artifact_id = uuid4()
        key = (
            f"browser/{run_id or 'adhoc'}/{session_id}/"
            f"{kind}/{artifact_id.hex}{_suffix_for(media_type, safe_name)}"
        )
        storage = object_storage_from_settings()
        tenant_storage = TenantObjectStorage(storage, organization_id)
        stored = await tenant_storage.put(
            key=key,
            content=content,
            media_type=media_type,
        )

        try:
            async with session_factory() as session:
                artifact = Artifact(
                    id=artifact_id,
                    organization_id=organization_id,
                    run_id=run_id,
                    storage_key=stored.key,
                    media_type=media_type,
                    size_bytes=len(content),
                    checksum_sha256=checksum,
                    metadata_json={
                        "workerId": str(worker_id) if worker_id else "",
                        "name": safe_name,
                        "kind": kind,
                        "sourceUrl": safe_source_url,
                        "correlationId": correlation_id,
                        "browserSessionId": str(session_id),
                        "operation": operation,
                    },
                )
                session.add(artifact)
                await session.commit()
        except BaseException:
            with suppress(Exception):
                await storage.delete(key=stored.key)
            raise

        return BrowserArtifactReference(
            id=artifact_id,
            kind=kind,
            media_type=media_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            name=safe_name,
            source_url=safe_source_url,
        )

    async def load_input(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        artifact_id: UUID,
    ) -> BrowserArtifactInput:
        async with session_factory() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.organization_id == organization_id,
                    Artifact.id == artifact_id,
                )
            )
            if artifact is None:
                raise LookupError("Authorized browser upload artifact was not found.")

            metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
            owner_worker = str(metadata.get("workerId") or "").strip()
            if owner_worker:
                if worker_id is None or owner_worker != str(worker_id):
                    raise PermissionError(
                        "Browser upload artifact is not authorized for this Worker."
                    )
            elif artifact.run_id is not None:
                if run_id is None or artifact.run_id != run_id:
                    raise PermissionError("Browser upload artifact is not authorized for this Run.")
            else:
                raise PermissionError(
                    "Browser upload artifact lacks a governed Worker/Run binding."
                )
            storage_key = artifact.storage_key
            media_type = artifact.media_type
            name = _safe_name(
                str(metadata.get("name") or storage_key.rsplit("/", 1)[-1]),
                "artifact.bin",
            )
            expected_checksum = artifact.checksum_sha256

        content = await object_storage_from_settings().get(key=storage_key)
        checksum = hashlib.sha256(content).hexdigest()
        if expected_checksum and checksum != expected_checksum:
            raise RuntimeError("Browser upload artifact checksum verification failed.")

        return BrowserArtifactInput(
            id=artifact_id,
            name=name,
            media_type=media_type,
            content=content,
            checksum_sha256=checksum,
        )
