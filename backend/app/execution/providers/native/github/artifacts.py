"""Private task artifacts: never accept arbitrary file paths or download URLs."""

from __future__ import annotations

import hashlib
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import select

from app.infrastructure.database.models import Artifact, CodingCommand, CodingSession
from app.infrastructure.database.session import session_factory
from app.infrastructure.storage.provider import object_storage_from_settings


async def associate_test_revision(request, sha):
    """Only a canonically verified commit of the exact tested export earns a SHA."""
    import json

    async with session_factory() as session:
        row = await session.scalar(
            select(CodingSession).where(
                CodingSession.organization_id == request.organization_id,
                CodingSession.work_item_id == request.work_item_id,
                CodingSession.resource_id == UUID(request.resource.id),
            )
        )
        if (
            not row
            or row.base_sha != request.input["baseSha"]
            or not row.evidence.get("exportArtifactId")
        ):
            return
        artifact = await session.get(Artifact, UUID(row.evidence["exportArtifactId"]))
        if not artifact or artifact.organization_id != request.organization_id:
            return
        content = await object_storage_from_settings().get(key=artifact.storage_key)
        if hashlib.sha256(content).hexdigest() != artifact.checksum_sha256:
            raise ValueError("Coding export evidence failed its stored checksum check.")
        exported = json.loads(content)
        # Compare the complete change set, not only its names or a command title.
        canonical = lambda changes: sorted(changes, key=lambda change: change["path"])
        if canonical(exported["changes"]) != canonical(request.input["changes"]):
            return
        commands = (
            await session.scalars(
                select(CodingCommand).where(
                    CodingCommand.session_id == row.id, CodingCommand.status == "completed"
                )
            )
        ).all()
        for command in commands:
            output = command.output
            if (
                output.get("processEvidence") == "e2b_exit_event"
                and output.get("sourceUnchangedDuringCommand")
                and output.get("sourceFingerprint") == exported["sourceFingerprint"]
            ):
                command.output = {**output, "testedSha": sha}
        await session.commit()


async def store_task_artifact(request, content: bytes, media_type: str):
    identity = uuid4()
    checksum = hashlib.sha256(content).hexdigest()
    key = f"{request.organization_id}/work-items/{request.work_item_id}/github/{identity}"
    await object_storage_from_settings().put(key=key, content=content, media_type=media_type)
    async with session_factory() as session:
        session.add(
            Artifact(
                id=identity,
                organization_id=request.organization_id,
                run_id=request.run_id,
                storage_key=key,
                media_type=media_type,
                size_bytes=len(content),
                checksum_sha256=checksum,
                metadata_json={
                    "workItemId": str(request.work_item_id),
                    "integrationResourceId": str(request.resource.id),
                    "source": "github",
                    "actionKey": request.idempotency_key,
                },
            )
        )
        await session.commit()
    return {
        "artifactId": str(identity),
        "sha256": checksum,
        "sizeBytes": len(content),
        "mediaType": media_type,
    }


async def upload_release_asset(provider, request, configuration, credential):
    values = request.input
    async with session_factory() as session:
        artifact = await session.get(Artifact, UUID(values["artifactId"]))
        if (
            not artifact
            or artifact.organization_id != request.organization_id
            or artifact.metadata_json.get("workItemId") != str(request.work_item_id)
        ):
            raise ValueError("Release artifact is not owned by this task.")
        if (
            artifact.checksum_sha256 != values["sha256"]
            or not artifact.size_bytes
            or artifact.size_bytes > 20_000_000
        ):
            raise ValueError("Release artifact exceeds limits or its checksum changed.")
        content = await object_storage_from_settings().get(key=artifact.storage_key)
        media_type = artifact.media_type
    if hashlib.sha256(content).hexdigest() != values["sha256"]:
        raise ValueError("Stored release artifact failed its checksum check.")
    repo = provider.repo_path(configuration)
    metadata = (await provider.api("GET", repo, credential)).data
    release = (await provider.api("GET", f"{repo}/releases/{values['releaseId']}", credential)).data
    if not release["draft"]:
        raise ValueError("Assets can only be attached to an unpublished draft release.")
    if any(c in values["name"] for c in ("/", "\\", "\x00")):
        raise ValueError("Release asset name cannot contain a path.")
    response = await provider._github_http.request(
        method="POST",
        provider="GitHub",
        credential=credential,
        url=f"https://uploads.github.com/repos/{metadata['full_name']}/releases/{values['releaseId']}/assets?name={quote(values['name'], safe='')}",
        data=content,
        headers={"Content-Type": media_type},
    )
    return response, f"{repo}/releases/assets/{response.data['id']}", response.data


async def verify_test_evidence(request, sha: str, conclusion: str | None):
    if conclusion not in {"success", "failure"}:
        return
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(CodingCommand, CodingSession)
                .join(CodingSession, CodingCommand.session_id == CodingSession.id)
                .where(
                    CodingSession.organization_id == request.organization_id,
                    CodingSession.work_item_id == request.work_item_id,
                    CodingSession.resource_id == UUID(request.resource.id),
                    CodingCommand.status == "completed",
                )
            )
        ).all()
        if not any(
            command.output.get("testedSha") == sha
            and command.output.get("exitCode")
            == (0 if conclusion == "success" else command.output.get("exitCode"))
            and (conclusion != "failure" or command.output.get("exitCode") not in {None, 0})
            for command, _ in rows
        ):
            raise ValueError(
                "A success/failure check requires persisted command evidence for this exact revision."
            )
