"""Checkpointed initialization: external I/O never holds a database transaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.application.services.integration_foundation import IntegrationFoundation
from app.bootstrap.settings import settings
from app.infrastructure.database.models import CodingSession, WorkItem
from app.infrastructure.database.session import session_factory
from app.infrastructure.redis.coordination import RedisCoordinator

from .bundles import INITIALIZE_HELPER, build_bundles, source_manifest
from .snapshot import validated_snapshot

logger = logging.getLogger("uvicorn.error")


async def initialize(provider, request, configuration, credential, runtime):
    # Late imports avoid a cycle with the public coding action entrypoint.
    from .service import coding_error, load_artifact, reserve, store_task_artifact

    coordinator = RedisCoordinator.from_settings()
    key = f"coding-workspace:{request.work_item_id}:{request.resource.id}"
    ttl = settings.coding_initialization_seconds + 120
    lease = await coordinator.acquire_lock(key, ttl_seconds=ttl)
    if not lease:
        await coordinator.close()
        raise coding_error(request, "Another workspace operation is in progress.")
    try:
        async with session_factory() as session:
            row = await session.scalar(
                select(CodingSession)
                .where(
                    CodingSession.organization_id == request.organization_id,
                    CodingSession.work_item_id == request.work_item_id,
                    CodingSession.resource_id == UUID(request.resource.id),
                )
                .with_for_update()
            )
            now = datetime.now(UTC)
            new_session = row is None
            if row and row.base_sha != request.input["sha"]:
                raise ValueError("Workspace is pinned to another revision.")
            if row and row.status == "running":
                await session.commit()
                return {"sessionId": str(row.id), "status": "running", "baseSha": row.base_sha}
            if row and row.status not in {"creating", "creating_uncertain"}:
                raise coding_error(
                    request,
                    "Workspace is paused, closed or expired; use its recovery controls.",
                    "policy_blocked",
                    False,
                )
            if not row:
                admission = await coordinator.acquire_lock("coding-admission", ttl_seconds=120)
                if not admission:
                    raise coding_error(request, "Coding admission is in progress.")
                try:
                    day = await reserve(session, request)
                    row = CodingSession(
                        id=uuid4(),
                        organization_id=request.organization_id,
                        work_item_id=request.work_item_id,
                        resource_id=UUID(request.resource.id),
                        base_sha=request.input["sha"],
                        status="creating",
                        active_seconds=0,
                        active_since=now,
                        last_activity_at=now,
                        expires_at=now + timedelta(seconds=settings.coding_retention_seconds),
                        fencing_token=0,
                        evidence={
                            "budgetDay": day,
                            "reservedSeconds": settings.coding_task_active_seconds,
                        },
                    )
                    session.add(row)
                    await session.commit()
                finally:
                    await coordinator.release_lock(admission)
            evidence = dict(row.evidence)
            # A pre-upgrade creating row may have lost both local references after
            # sandbox creation. Absence of an ID is never permission to recreate it.
            legacy = not new_session and "initialization" not in evidence
            init: dict[str, Any] = dict(
                evidence.get("initialization")
                or {
                    "stage": "snapshot",
                    "deadline": (
                        now + timedelta(seconds=settings.coding_initialization_seconds)
                    ).isoformat(),
                    "confirmedBundles": [],
                    "generation": row.fencing_token + 1,
                    "stageStartedAt": now.isoformat(),
                    "stageDurations": {},
                    "legacy": legacy,
                }
            )
            # Older partially initialized sessions retain their fingerprint algorithm.
            if not legacy and "fingerprintAlgorithm" not in evidence:
                evidence["fingerprintAlgorithm"] = "manifest-sha256-v1"
            row.fencing_token += 1
            generation = row.fencing_token
            init["generation"] = generation
            row.evidence = {**evidence, "initialization": init}
            await session.commit()
            deadline = datetime.fromisoformat(init["deadline"])
            started = time.monotonic()

            def remaining():
                return max(0.0, (deadline - datetime.now(UTC)).total_seconds())

            async def checkpoint(
                stage, *, evidence_updates=None, sandbox_id=None, ready=False, **updates
            ):
                nonlocal init
                if remaining() <= 0:
                    raise coding_error(
                        request,
                        "Workspace initialization deadline reached at "
                        + init["stage"]
                        + "; saved source and sandbox state remain available.",
                        "timeout",
                        False,
                    )
                if not await coordinator.renew_lock(lease, ttl_seconds=int(remaining()) + 120):
                    raise coding_error(
                        request,
                        "Workspace ownership was lost; initialization is paused.",
                        "uncertain_outcome",
                        False,
                    )
                await session.refresh(row, with_for_update=True)
                if row.fencing_token != generation:
                    raise coding_error(
                        request,
                        "A newer workspace operation owns this checkpoint.",
                        "uncertain_outcome",
                        False,
                    )
                if row.status not in {"creating", "creating_uncertain"}:
                    raise coding_error(
                        request,
                        "Workspace initialization is no longer active; baseline restoration is blocked.",
                        "policy_blocked",
                        False,
                    )
                item = await session.get(WorkItem, request.work_item_id, populate_existing=True)
                if not item or item.status not in {"queued", "running", "admitted"}:
                    raise coding_error(
                        request,
                        "Task stopped or was cancelled; saved workspace state is preserved.",
                        "policy_blocked",
                        False,
                    )
                await IntegrationFoundation(session).authorize(
                    organization_id=request.organization_id,
                    agent_id=request.agent_id,
                    work_item_id=request.work_item_id,
                    resource_id=UUID(request.resource.id),
                    scope=request.capability.scope,
                    payload=request.input,
                )
                stamp = datetime.now(UTC)
                durations = dict(init.get("stageDurations", {}))
                changed_stage = stage != init["stage"]
                if changed_stage:
                    previous_started = datetime.fromisoformat(
                        init.get("stageStartedAt", stamp.isoformat())
                    )
                    durations[init["stage"]] = durations.get(init["stage"], 0) + max(
                        0, (stamp - previous_started).total_seconds()
                    )
                    logger.info(
                        "Coding initialization stage ended work_item=%s session=%s stage=%s elapsed_seconds=%.3f",
                        request.work_item_id,
                        row.id,
                        init["stage"],
                        durations[init["stage"]],
                    )
                init = {
                    **init,
                    **updates,
                    "stage": stage,
                    "updatedAt": stamp.isoformat(),
                    "stageDurations": durations,
                    "stageStartedAt": stamp.isoformat()
                    if changed_stage
                    else init.get("stageStartedAt", stamp.isoformat()),
                }
                row.evidence = {**row.evidence, **(evidence_updates or {}), "initialization": init}
                if sandbox_id is not None:
                    if row.sandbox_id and row.sandbox_id != sandbox_id:
                        raise coding_error(
                            request,
                            "Sandbox identity changed during initialization.",
                            "uncertain_outcome",
                            False,
                        )
                    row.sandbox_id = sandbox_id
                if ready:
                    row.status = "running"
                moment = datetime.now(UTC)
                if row.active_since:
                    row.active_seconds += max(0, int((moment - row.active_since).total_seconds()))
                row.last_activity_at = row.active_since = moment
                if row.active_seconds >= settings.coding_task_active_seconds:
                    raise coding_error(
                        request,
                        "Task coding-time allowance was exhausted during initialization; checkpoints remain saved.",
                        "policy_blocked",
                        False,
                    )
                await session.commit()
                logger.info(
                    "Coding initialization stage work_item=%s session=%s stage=%s elapsed_seconds=%.3f files=%s bundles_confirmed=%s bundles_total=%s bytes=%s",
                    request.work_item_id,
                    row.id,
                    stage,
                    time.monotonic() - started,
                    init.get("fileCount", 0),
                    len(init.get("confirmedBundles", [])),
                    len(init.get("bundles", [])),
                    init.get("sourceBytes", 0),
                )

            try:
                async with asyncio.timeout(remaining()):
                    await checkpoint(init["stage"])
                    if not row.evidence.get("baselineArtifactId"):
                        if not init.get("snapshotArtifactId"):
                            repo = provider.repo_path(configuration)
                            await provider.api("GET", repo + "/commits/" + row.base_sha, credential)
                            snapshot = await provider.api(
                                "GET",
                                repo + "/tarball/" + row.base_sha,
                                credential,
                                binary=True,
                                max_bytes=32_000_000,
                            )
                            artifact = await store_task_artifact(
                                request, snapshot.raw, "application/octet-stream"
                            )
                            await checkpoint(
                                "validation", snapshotArtifactId=artifact["artifactId"]
                            )
                        raw = await load_artifact(init["snapshotArtifactId"], request)
                        files = validated_snapshot(raw, include_binary=True)
                        baseline = await store_task_artifact(
                            request, json.dumps(files).encode(), "application/json"
                        )
                        await checkpoint(
                            "validation",
                            evidence_updates={"baselineArtifactId": baseline["artifactId"]},
                        )
                    files = json.loads(
                        await load_artifact(row.evidence["baselineArtifactId"], request)
                    )
                    manifest = source_manifest(files)
                    if not init.get("manifestArtifactId"):
                        bundles = []
                        for payload, paths in build_bundles(files):
                            artifact = await store_task_artifact(
                                request, payload, "application/octet-stream"
                            )
                            bundles.append(
                                {
                                    "artifactId": artifact["artifactId"],
                                    "sha256": hashlib.sha256(payload).hexdigest(),
                                    "paths": paths,
                                }
                            )
                        artifact = await store_task_artifact(
                            request,
                            json.dumps({"files": manifest, "bundles": bundles}).encode(),
                            "application/json",
                        )
                        # DB records hold bundle identities, not the complete path/hash manifest.
                        await checkpoint(
                            "sandbox_creation",
                            manifestArtifactId=artifact["artifactId"],
                            bundles=[
                                {"artifactId": b["artifactId"], "sha256": b["sha256"]}
                                for b in bundles
                            ],
                            fileCount=len(files),
                            sourceBytes=sum(v["size"] for v in manifest.values()),
                        )
                    if not row.sandbox_id:
                        if init.get("createRequested") or init.get("legacy"):
                            recovered_id = await runtime.find(str(row.id))
                            if not recovered_id:
                                raise coding_error(
                                    request,
                                    "Sandbox creation outcome could not be established; recreation is paused.",
                                    "uncertain_outcome",
                                    False,
                                )
                            await checkpoint("sandbox_creation", sandbox_id=recovered_id)
                        else:
                            await checkpoint("sandbox_creation", createRequested=True)
                            sandbox_id = await runtime.create(
                                metadata={
                                    "workItemId": str(request.work_item_id),
                                    "codingSessionId": str(row.id),
                                },
                                timeout=max(1, int(remaining()) + 120),
                            )
                            # Record returned identity even if ownership/cancellation changed during creation.
                            await checkpoint("sandbox_creation", sandbox_id=sandbox_id)
                    await checkpoint("bundle_transfer")
                    await runtime.connect(row.sandbox_id, max(1, int(remaining()) + 120))
                    await runtime.run(row.sandbox_id, "mkdir -p /workspace")
                    await runtime.write(
                        row.sandbox_id, "/tmp/audoryn-init-helper.py", INITIALIZE_HELPER
                    )
                    spec = await load_artifact(init["manifestArtifactId"], request)
                    await runtime.write(row.sandbox_id, "/tmp/audoryn-source-manifest.json", spec)
                    for index, bundle in enumerate(init["bundles"]):
                        await checkpoint("bundle_transfer")
                        command = "python3 /tmp/audoryn-init-helper.py check " + str(index)
                        checked = await runtime.run(row.sandbox_id, command)
                        observed = json.loads(checked.stdout) if checked.exit_code == 0 else {}
                        complete = observed.get("complete")
                        if not complete:
                            if not observed.get("bundleAvailable"):
                                payload = await load_artifact(bundle["artifactId"], request)
                                if hashlib.sha256(payload).hexdigest() != bundle["sha256"]:
                                    raise ValueError("Stored source bundle checksum mismatch.")
                                await runtime.write(
                                    row.sandbox_id, f"/tmp/audoryn-source-{index}.tar", payload
                                )
                            applied = await runtime.run(
                                row.sandbox_id,
                                "python3 /tmp/audoryn-init-helper.py apply " + str(index),
                            )
                            if applied.exit_code != 0 or not json.loads(applied.stdout).get(
                                "complete"
                            ):
                                raise ValueError("Workspace source bundle verification failed.")
                        await checkpoint(
                            "bundle_transfer",
                            confirmedBundles=sorted(set(init["confirmedBundles"] + [index])),
                        )
                    await checkpoint("verification")
                    verified = await runtime.run(
                        row.sandbox_id, "python3 /tmp/audoryn-init-helper.py verify-all"
                    )
                    if verified.exit_code != 0 or not json.loads(verified.stdout).get("complete"):
                        raise ValueError("Workspace source manifest verification failed.")
                    await runtime.set_timeout(row.sandbox_id, settings.coding_idle_seconds)
                    await checkpoint("ready", ready=True)
                    return {
                        "sessionId": str(row.id),
                        "status": row.status,
                        "baseSha": row.base_sha,
                        "sourceFiles": len(files),
                        "initializationStage": "ready",
                    }
            except TimeoutError as error:
                logger.warning(
                    "Coding initialization deadline work_item=%s session=%s stage=%s elapsed_seconds=%.3f",
                    request.work_item_id,
                    row.id,
                    init["stage"],
                    time.monotonic() - started,
                )
                expired = remaining() <= 0
                message = (
                    (
                        "Workspace initialization deadline reached at "
                        if expired
                        else "Workspace service timed out during "
                    )
                    + init["stage"]
                    + "; saved artifacts and sandbox state remain available."
                )
                raise coding_error(request, message, "timeout", not expired) from error
            except asyncio.CancelledError:
                logger.warning(
                    "Coding initialization interrupted work_item=%s session=%s stage=%s elapsed_seconds=%.3f deadline=%s",
                    request.work_item_id,
                    row.id,
                    init["stage"],
                    time.monotonic() - started,
                    deadline.isoformat(),
                )
                raise
    finally:
        await coordinator.release_lock(lease)
        await coordinator.close()
