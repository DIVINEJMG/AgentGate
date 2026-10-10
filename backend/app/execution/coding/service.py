"""Durable coding workspaces with bounded admission and no GitHub token in the VM."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import shlex
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.bootstrap.settings import settings
from app.execution.contracts import ExecutionErrorCode, ExecutionProviderError
from app.execution.providers.integration_hooks import HookUnavailable
from app.execution.providers.native.github.artifacts import store_task_artifact
from app.execution.providers.native.github.safety import SECRET, safe_path, validate_changes
from app.infrastructure.database.models import Artifact, CodingBudget, CodingCommand, CodingSession
from app.infrastructure.database.session import session_factory
from app.infrastructure.redis.coordination import RedisCoordinator
from app.infrastructure.storage.provider import object_storage_from_settings

from .bundles import manifest_fingerprint, source_manifest
from .e2b import E2BCodingRuntime
from .manifest_export import SCAN_SCRIPT

logger = logging.getLogger("uvicorn.error")
_stage = ContextVar("coding_action_stage", default="preparation")


def log_failure(request, error):
    # Exception messages and tracebacks may contain SDK headers, credentials or source.
    reasons = {
        "Expected to receive information about written file": "missing_write_acknowledgement",
        "Received unexpected response from write operation": "unexpected_write_acknowledgement",
    }
    reason = reasons.get(str(error), "unclassified_exception")
    status = getattr(error, "status_code", None)
    logger.warning(
        "Coding action failed work_item=%s operation=%s stage=%s error_type=%s reason=%s http_status=%s",
        getattr(request, "work_item_id", None),
        getattr(request, "operation", "command.status"),
        _stage.get(),
        type(error).__name__,
        reason,
        status if isinstance(status, int) else None,
    )


EXPORT_SCRIPT = """import os,json,stat,base64,fnmatch
root='/workspace'; result={}; total=0
with open('/tmp/audoryn-baseline-paths.json') as f: tracked=set(json.load(f))
generated={'.pytest_cache','.mypy_cache','.ruff_cache','node_modules','.venv','__pycache__'}
for directory,dirs,files in os.walk(root,followlinks=False):
 def keep(d):
  prefix=os.path.relpath(os.path.join(directory,d),root).replace(os.sep,'/')+'/'
  return d!='.git' and (d not in generated or any(p.startswith(prefix) for p in tracked))
 dirs[:]=[d for d in dirs if keep(d)]
 for name in files:
  path=os.path.join(directory,name)
  relative=os.path.relpath(path,root).replace(os.sep,'/')
  if relative not in tracked and (fnmatch.fnmatch(name,'*.pyc') or name=='.coverage' or name.startswith('.coverage.')): continue
  if not stat.S_ISREG(os.lstat(path).st_mode) or os.path.commonpath([os.path.realpath(path),os.path.realpath(root)])!=os.path.realpath(root): raise ValueError('Unsafe workspace path')
  if os.path.getsize(path)>2000000: raise ValueError('Workspace file exceeds export budget')
  with open(path,'rb') as f: raw=f.read()
  try: content=raw.decode('utf-8')
  except UnicodeDecodeError: content={'binaryBase64':base64.b64encode(raw).decode()}
  total+=len(raw)
  if total>40000000 or len(result)>10000: raise ValueError('Workspace export exceeds budget')
  result[relative]=content
print(json.dumps(result))"""


def coding_error(request, message, code: ExecutionErrorCode = "rate_limited", retryable=True):
    return ExecutionProviderError(
        code=code,
        retryable=retryable,
        provider="github",
        operation=request.operation,
        correlation_id=request.correlation_id,
        safe_message=message,
        retry_after_seconds=60 if retryable else None,
    )


async def admit_capacity(session, request):
    # Called under a global admission lease. The durable creating row counts as
    # occupied before E2B I/O, preventing cross-process oversubscription.
    active = {"creating", "running"}
    global_count = await session.scalar(
        select(func.count()).select_from(CodingSession).where(CodingSession.status.in_(active))
    )
    org_count = await session.scalar(
        select(func.count())
        .select_from(CodingSession)
        .where(
            CodingSession.status.in_(active),
            CodingSession.organization_id == request.organization_id,
        )
    )
    if (
        global_count >= settings.coding_global_concurrency
        or org_count >= settings.coding_organization_concurrency
    ):
        raise coding_error(
            request, "Coding workspace capacity is full; GitHub API actions remain available."
        )


async def reserve(session, request):
    await admit_capacity(session, request)
    day = datetime.now(UTC).date().isoformat()
    await session.execute(
        insert(CodingBudget)
        .values(id=uuid4(), organization_id=request.organization_id, day=day, reserved_seconds=0)
        .on_conflict_do_nothing(constraint="uq_coding_daily_budget")
    )
    budget = await session.scalar(
        select(CodingBudget)
        .where(CodingBudget.organization_id == request.organization_id, CodingBudget.day == day)
        .with_for_update()
    )
    if (
        budget.reserved_seconds + settings.coding_task_active_seconds
        > settings.coding_organization_daily_seconds
    ):
        raise coding_error(
            request, "Organization coding-time budget is exhausted; this task will wait."
        )
    budget.reserved_seconds += settings.coding_task_active_seconds
    return day


async def release_unused_budget(session, row):
    budget = await session.scalar(
        select(CodingBudget)
        .where(
            CodingBudget.organization_id == row.organization_id,
            CodingBudget.day == row.evidence.get("budgetDay"),
        )
        .with_for_update()
    )
    if budget and not row.evidence.get("budgetReleased"):
        used = max(0, row.active_seconds - row.evidence.get("budgetActiveBaseline", 0))
        unused = max(0, row.evidence.get("reservedSeconds", 0) - used)
        budget.reserved_seconds = max(0, budget.reserved_seconds - unused)
        row.evidence = {**row.evidence, "budgetReleased": True}


async def rollover_budget(session, row, request, now):
    """Charge resumed work against today's budget without losing yesterday's usage."""
    day = now.date().isoformat()
    if row.evidence.get("budgetDay") == day:
        return
    remaining = max(0, settings.coding_task_active_seconds - row.active_seconds)
    await session.execute(
        insert(CodingBudget)
        .values(id=uuid4(), organization_id=row.organization_id, day=day, reserved_seconds=0)
        .on_conflict_do_nothing(constraint="uq_coding_daily_budget")
    )
    budget = await session.scalar(
        select(CodingBudget)
        .where(CodingBudget.organization_id == row.organization_id, CodingBudget.day == day)
        .with_for_update()
    )
    if budget.reserved_seconds + remaining > settings.coding_organization_daily_seconds:
        raise coding_error(
            request,
            "Today's organization coding-time budget is exhausted; retained work is unchanged.",
        )
    await release_unused_budget(session, row)
    budget.reserved_seconds += remaining
    row.evidence = {
        **row.evidence,
        "budgetDay": day,
        "reservedSeconds": remaining,
        "budgetReleased": False,
        "budgetActiveBaseline": row.active_seconds,
    }


async def export(runtime, row, request, configuration):
    _stage.set("baseline_artifact_read")
    baseline_artifact = await load_artifact(row.evidence["baselineArtifactId"], request)
    original = json.loads(baseline_artifact)
    manifest_mode = row.evidence.get("fingerprintAlgorithm") == "manifest-sha256-v1"
    baseline_manifest = {}
    if manifest_mode:
        baseline_manifest = source_manifest(original)
        path, content = "/tmp/audoryn-baseline-manifest.json", json.dumps(baseline_manifest)
    else:
        path, content = "/tmp/audoryn-baseline-paths.json", json.dumps(list(original))
    _stage.set("baseline_manifest_upload")
    if hasattr(runtime, "ensure_control_file"):
        outcome = await runtime.ensure_control_file(row.sandbox_id, path, content, _stage.set)
        logger.info(
            "Coding baseline manifest work_item=%s outcome=%s", request.work_item_id, outcome
        )
    else:
        await runtime.write(row.sandbox_id, path, content)
    _stage.set("workspace_scan")
    result = await runtime.run(
        row.sandbox_id, "python3 -c " + shlex.quote(SCAN_SCRIPT if manifest_mode else EXPORT_SCRIPT)
    )
    if result.exit_code != 0:
        raise ValueError("Workspace export failed its path and size checks.")
    scanned = json.loads(result.stdout)
    if manifest_mode:
        observed = scanned["manifest"]
        changed = {
            path
            for path in set(baseline_manifest) | set(observed)
            if baseline_manifest.get(path) != observed.get(path)
        }
        if set(scanned["changed"]) != changed & set(observed):
            raise ValueError("Workspace change evidence is incomplete.")
        # Bind transferred content to the scan, including binary assets.
        if source_manifest(scanned["changed"]) != {
            path: observed[path] for path in scanned["changed"]
        }:
            raise ValueError("Workspace content does not match its observed hashes.")
        for path in observed:
            safe_path(path)
        current = {path: scanned["changed"].get(path, original.get(path)) for path in observed}
        fingerprint = manifest_fingerprint(observed)
    else:
        current = scanned
        fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    changes = [
        {"path": path, **({"delete": True} if path not in current else {"content": current[path]})}
        for path in sorted(set(original) | set(current))
        if original.get(path) != current.get(path)
    ]
    if any(isinstance(c.get("content"), str) and SECRET.search(c["content"]) for c in changes):
        raise ValueError("A possible credential was found in workspace changes; export is blocked.")
    publication_blocker = None
    try:
        validate_changes(changes, configuration.get("constraints", {}))
    except ValueError:
        publication_blocker = "Workspace changes exceed the authorized publication paths or limits. Review the diff before publishing."
    patch = "".join(
        "".join(
            difflib.unified_diff(
                (
                    original.get(c["path"], "")
                    if isinstance(original.get(c["path"], ""), str)
                    else "[Binary content]\n"
                ).splitlines(keepends=True),
                (
                    current.get(c["path"], "")
                    if isinstance(current.get(c["path"], ""), str)
                    else "[Binary content changed]\n"
                ).splitlines(keepends=True),
                fromfile="a/" + c["path"],
                tofile="b/" + c["path"],
            )
        )
        for c in changes
    )
    # A fresh scan is mandatory even when reusing a saved diff artifact.
    if (
        manifest_mode
        and row.evidence.get("sourceFingerprint") == fingerprint
        and row.evidence.get("exportArtifactId")
    ):
        _stage.set("saved_diff_read")
        saved = json.loads(await load_artifact(row.evidence["exportArtifactId"], request))
        if saved.get("sourceFingerprint") == fingerprint and saved.get("changes") == changes:
            return {
                **saved,
                "artifact": {"artifactId": row.evidence["exportArtifactId"]},
                "publicationAllowed": publication_blocker is None,
                "publicationBlocker": publication_blocker,
            }
    _stage.set("diff_artifact_write")
    evidence = await store_task_artifact(
        request,
        json.dumps(
            {
                "baseSha": row.base_sha,
                "changes": changes,
                "diff": patch,
                "sourceFingerprint": fingerprint,
            }
        ).encode(),
        "application/json",
    )
    row.evidence = {
        **row.evidence,
        "exportArtifactId": evidence["artifactId"],
        "sourceFingerprint": fingerprint,
    }
    return {
        "baseSha": row.base_sha,
        "changes": changes,
        "diff": patch,
        "sourceFingerprint": fingerprint,
        "artifact": evidence,
        "publicationAllowed": publication_blocker is None,
        "publicationBlocker": publication_blocker,
    }


async def load_artifact(identity, request):
    async with session_factory() as session:
        row = await session.get(Artifact, UUID(identity))
        if (
            not row
            or row.organization_id != request.organization_id
            or row.metadata_json.get("workItemId") != str(request.work_item_id)
        ):
            raise PermissionError("Coding evidence is outside this task.")
        key, checksum = row.storage_key, row.checksum_sha256
        await session.commit()
        content = await object_storage_from_settings().get(key=key)
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError("Saved coding artifact failed checksum verification.")
        return content


async def execute_coding_action(provider, request, configuration, credential, runtime=None):
    stage_token = _stage.set("preparation")
    try:
        runtime = runtime or E2BCodingRuntime()
    except HookUnavailable as error:
        _stage.reset(stage_token)
        raise coding_error(request, str(error), "unsupported_operation", False) from error
    if request.operation == "repository.workspace.open":
        from .initialization import initialize

        try:
            return await initialize(provider, request, configuration, credential, runtime)
        except (ExecutionProviderError, ValueError, PermissionError, TimeoutError):
            raise
        except Exception as error:
            log_failure(request, error)
            raise coding_error(
                request,
                "Workspace initialization was interrupted; saved checkpoints will be reconciled before resuming.",
                "temporary_provider_error",
                True,
            ) from error
        finally:
            if hasattr(runtime, "release"):
                await runtime.release()
            _stage.reset(stage_token)
    coordinator = RedisCoordinator.from_settings()
    lease = await coordinator.acquire_lock(
        f"coding-workspace:{request.work_item_id}:{request.resource.id}", ttl_seconds=120
    )
    if not lease:
        await coordinator.close()
        _stage.reset(stage_token)
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
            operation = request.operation.removeprefix("repository.workspace.")
            if not row or not row.sandbox_id or row.status not in {"running", "paused"}:
                raise ValueError("Open an available pinned coding workspace first.")
            if row.expires_at <= now:
                raise ValueError("Coding workspace retention expired.")
            if row.active_since:
                row.active_seconds += min(
                    settings.coding_idle_seconds,
                    max(0, int((now - row.active_since).total_seconds())),
                )
            if row.active_seconds >= settings.coding_task_active_seconds:
                raise coding_error(
                    request,
                    "Task coding-time budget is exhausted. Saved patches remain available.",
                    "policy_blocked",
                    False,
                )
            if row.status == "paused" or row.evidence.get("budgetDay") != now.date().isoformat():
                admission = await coordinator.acquire_lock("coding-admission", ttl_seconds=120)
                if not admission:
                    raise coding_error(request, "Coding admission is already in progress.")
                try:
                    if row.status == "paused":
                        await admit_capacity(session, request)
                    await rollover_budget(session, row, request, now)
                    row.status = "running"
                    await session.commit()
                finally:
                    await coordinator.release_lock(admission)
            row.fencing_token += 1
            row.active_since, row.last_activity_at, row.status = now, now, "running"
            generation = row.fencing_token
            await session.commit()
            session.expunge(row)

            async def checkpoint():
                from app.application.services.integration_foundation import IntegrationFoundation
                from app.infrastructure.database.models import WorkItem

                _stage.set("checkpoint")
                if not await coordinator.renew_lock(lease, ttl_seconds=120):
                    raise coding_error(
                        request,
                        "Workspace ownership was lost; completed evidence is retained.",
                        "policy_blocked",
                        False,
                    )
                # Detached workspace changes are accepted only by their current owner.
                with session.no_autoflush:
                    current = await session.scalar(
                        select(CodingSession).where(CodingSession.id == row.id).with_for_update()
                    )
                if (
                    not current
                    or current.fencing_token != generation
                    or current.status != "running"
                ):
                    raise coding_error(
                        request,
                        "Workspace ownership changed; the pending result was not accepted.",
                        "policy_blocked",
                        False,
                    )
                item = await session.get(WorkItem, request.work_item_id, populate_existing=True)
                if not item or item.status not in {"queued", "running", "admitted"}:
                    raise coding_error(
                        request,
                        "Task stopped; completed workspace evidence is retained.",
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
                current.evidence = dict(row.evidence)
                current.active_seconds = row.active_seconds
                current.active_since, current.last_activity_at = (
                    row.active_since,
                    row.last_activity_at,
                )
                current.status = row.status
                if row.status == "closed":
                    await release_unused_budget(session, current)
                await session.commit()
                session.expunge(current)

            await checkpoint()
            _stage.set("sandbox_connect")
            await runtime.connect(
                row.sandbox_id,
                min(
                    settings.coding_idle_seconds,
                    settings.coding_task_active_seconds - row.active_seconds,
                ),
            )
            output = {}
            if operation in {"read", "edit"}:
                _stage.set("workspace_path_check")
                path = safe_path(request.input["path"])
                await assert_workspace_path(runtime, row.sandbox_id, path)
                if operation == "edit":
                    validate_changes(
                        [{"path": path, "content": request.input["content"]}],
                        configuration.get("constraints", {}),
                    )
                    await checkpoint()
                    _stage.set("workspace_edit")
                    await runtime.write(
                        row.sandbox_id, "/workspace/" + path, request.input["content"]
                    )
                _stage.set("workspace_read")
                content = await runtime.read(row.sandbox_id, "/workspace/" + path)
                offset = request.input.get("offset", 0)
                end = offset + request.input.get("maxChars", 12000)
                output = {
                    "path": path,
                    "content": content[offset:end],
                    "offset": offset,
                    "totalChars": len(content),
                    "nextOffset": end if end < len(content) else None,
                    "truncated": end < len(content),
                }
            elif operation == "search":
                _stage.set("workspace_search")
                result = await runtime.run(
                    row.sandbox_id,
                    "grep -R -n -F --exclude-dir=node_modules --exclude-dir=.git -- "
                    + shlex.quote(request.input["query"])
                    + " . | head -n 100",
                )
                output = {"matches": result.stdout[:30000], "limit": 100}
            elif operation == "patch":
                # Validate every destination before applying untrusted patch text.
                for line in request.input["patch"].splitlines():
                    if line.startswith(("+++ ", "--- ")):
                        name = shlex.split(line[4:])[0]
                        if name != "/dev/null":
                            name = name[2:] if name.startswith(("a/", "b/")) else name
                            validate_changes(
                                [{"path": name, "content": ""}],
                                configuration.get("constraints", {}),
                            )
                            await assert_workspace_path(runtime, row.sandbox_id, name)
                await checkpoint()
                _stage.set("workspace_patch")
                await runtime.write(row.sandbox_id, "/tmp/audoryn.patch", request.input["patch"])
                result = await runtime.run(
                    row.sandbox_id, "git apply --no-index /tmp/audoryn.patch"
                )
                if result.exit_code != 0:
                    raise ValueError("Patch did not apply safely to the workspace.")
                output = await export(runtime, row, request, configuration)
            elif operation in {"diff", "close"}:
                output = await export(runtime, row, request, configuration)
                if operation == "close":
                    await checkpoint()
                    _stage.set("sandbox_delete")
                    await runtime.delete(row.sandbox_id)
                    row.status, row.active_since = "closed", None
            elif operation == "command.start":
                output = await start_command(
                    session, runtime, row, request, configuration, checkpoint=checkpoint
                )
            elif operation in {"command.status", "command.cancel"}:
                output = await inspect_command(
                    session,
                    runtime,
                    row,
                    request,
                    operation == "command.cancel",
                    configuration,
                    checkpoint=checkpoint,
                )
            else:
                raise ValueError("Coding action is unavailable.")
            await checkpoint()
            return {"sessionId": str(row.id), "status": row.status, **output}
    except (ExecutionProviderError, ValueError, PermissionError) as error:
        log_failure(request, error)
        raise
    except Exception as error:  # SDK errors must not leak credentials or authorize command replay
        log_failure(request, error)
        inspection = request.operation in {
            "repository.workspace.read",
            "repository.workspace.search",
            "repository.workspace.diff",
            "repository.workspace.command.status",
        }
        raise coding_error(
            request,
            "Workspace inspection could not finish; saved edits and completed commands remain available. Inspection will retry."
            if inspection
            else "Coding operation outcome could not be established. Saved checkpoints remain intact; automatic mutation replay is paused.",
            "temporary_provider_error" if inspection else "uncertain_outcome",
            inspection,
        ) from error
    finally:
        await coordinator.release_lock(lease)
        await coordinator.close()
        if hasattr(runtime, "release"):
            await runtime.release()
        _stage.reset(stage_token)


async def assert_workspace_path(runtime, sandbox_id, path):
    script = (
        "import os; p="
        + repr("/workspace/" + path)
        + "; assert os.path.realpath(p).startswith('/workspace/'), 'Unsafe path'"
    )
    result = await runtime.run(sandbox_id, "python3 -c " + shlex.quote(script))
    if result.exit_code:
        raise ValueError("Workspace path resolves outside the source root.")


async def start_command(session, runtime, row, request, configuration, *, checkpoint=None):
    _stage.set("command_lookup")
    prior = await session.scalar(
        select(CodingCommand).where(
            CodingCommand.session_id == row.id, CodingCommand.action_key == request.idempotency_key
        )
    )
    if prior:
        if prior.process_id is None:
            raise coding_error(
                request,
                "Command launch has an uncertain outcome; automatic replay is paused.",
                "uncertain_outcome",
                False,
            )
        return {"commandId": str(prior.id), "status": prior.status}
    await session.commit()  # Release lookup transaction before scanning the workspace.
    source = await export(runtime, row, request, configuration)
    command = CodingCommand(
        id=uuid4(),
        organization_id=request.organization_id,
        session_id=row.id,
        action_key=request.idempotency_key,
        command=request.input["command"],
        status="starting",
        output={"startFingerprint": source["sourceFingerprint"], "baseSha": row.base_sha},
    )
    session.add(command)
    if checkpoint:
        await checkpoint()
    else:
        await session.commit()
    prefix = "/tmp/audoryn-" + str(command.id)
    shell = (
        "sh -c "
        + shlex.quote(command.command)
        + " >"
        + prefix
        + ".stdout 2>"
        + prefix
        + ".stderr; code=$?; exit $code"
    )
    _stage.set("command_launch")
    handle = await runtime.run(row.sandbox_id, shell, background=True)
    command.process_id, command.status = handle.pid, "running"
    await session.commit()
    return {"commandId": str(command.id), "status": "running", "testsPassed": False}


async def verify_completed_command(
    session, runtime, row, request, configuration, command, *, checkpoint=None
):
    output = dict(command.output)
    try:
        source = await export(runtime, row, request, configuration)
    except Exception as error:  # noqa: BLE001 - completed commands must never replay after export failure
        log_failure(request, error)
        output["sourceVerificationAvailable"] = False
        output["sourceUnchangedDuringCommand"] = False
        output["reason"] = (
            "Command finished, but its source snapshot could not be verified. Saved output and exit status remain available; publication is blocked."
        )
    else:
        output["sourceVerificationAvailable"] = True
        output["sourceFingerprint"] = source["sourceFingerprint"]
        output["sourceUnchangedDuringCommand"] = (
            output.get("startFingerprint") == source["sourceFingerprint"]
        )
        output["publicationAllowed"] = source["publicationAllowed"]
        output["publicationBlocker"] = source["publicationBlocker"]
        output.pop("reason", None)
    # A fresh JSON value is essential: in-place changes after commit are not tracked.
    command.output = dict(output)
    if checkpoint:
        await checkpoint()
    else:
        await session.commit()


async def inspect_command(
    session, runtime, row, request, cancel, configuration, *, checkpoint=None
):
    _stage.set("command_lookup")
    command = await session.get(CodingCommand, UUID(request.input["commandId"]))
    if (
        not command
        or command.session_id != row.id
        or command.organization_id != request.organization_id
    ):
        raise PermissionError("Command belongs to another workspace.")
    await session.commit()  # Process inspection and export run without row locks.
    if cancel and command.status == "running":
        if checkpoint:
            await checkpoint()
        _stage.set("command_cancel")
        await runtime.cancel(row.sandbox_id, command.process_id)
        command.status = "cancelled"
    if command.status in {"completed", "cancelled", "uncertain_outcome"}:
        if (
            command.status == "completed"
            and command.output.get("sourceVerificationAvailable") is not True
            and command.output.get("exitCode") is not None
        ):
            await verify_completed_command(
                session, runtime, row, request, configuration, command, checkpoint=checkpoint
            )
        return {
            "commandId": str(command.id),
            "command": command.command,
            "status": command.status,
            **command.output,
        }
    prefix = "/tmp/audoryn-" + str(command.id)
    output = dict(command.output)
    output["outputLimits"] = {"selection": "tail", "maxBytesPerStream": 30000}
    for suffix in ("stdout", "stderr"):
        _stage.set("command_output_" + suffix)
        result = await runtime.run(
            row.sandbox_id,
            "if test -f "
            + prefix
            + "."
            + suffix
            + "; then tail -c 30000 "
            + prefix
            + "."
            + suffix
            + "; fi",
        )
        output[suffix] = result.stdout
    try:
        _stage.set("command_exit_inspection")
        outcome = await runtime.command_result(row.sandbox_id, command.process_id)
    except Exception as error:  # noqa: BLE001 - missing process evidence never authorizes replay
        log_failure(request, error)
        command.status = "uncertain_outcome"
        output["reason"] = (
            "The process result could not be recovered from E2B. Automatic command replay is paused."
        )
    else:
        if outcome.get("exitCode") is not None and command.status != "cancelled":
            command.status = "completed"
            output["exitCode"] = outcome["exitCode"]
            output["processEvidence"] = "e2b_exit_event"
            command.output = dict(output)
            # Persist the observed process outcome before fallible publication checks.
            await session.commit()
            await verify_completed_command(
                session, runtime, row, request, configuration, command, checkpoint=checkpoint
            )
            output = dict(command.output)
    command.output = dict(output)
    return {
        "commandId": str(command.id),
        "command": command.command,
        "status": command.status,
        **output,
    }


async def maintain_coding_sessions(runtime=None):
    """Run on the existing outbox recovery tick; paused E2B state needs deletion."""
    if not settings.coding_execution_enabled:
        return
    from types import SimpleNamespace

    runtime = runtime or E2BCodingRuntime()
    coordinator = RedisCoordinator.from_settings()
    try:
        async with session_factory() as session:
            now = datetime.now(UTC)
            rows = (
                await session.scalars(
                    select(CodingSession)
                    .where(
                        CodingSession.status.in_(
                            {"creating", "creating_uncertain", "running", "paused"}
                        ),
                        CodingSession.last_activity_at
                        < now - timedelta(seconds=settings.coding_idle_seconds),
                    )
                    .limit(20)
                )
            ).all()
            for row in rows:
                lease = await coordinator.acquire_lock(
                    f"coding-workspace:{row.work_item_id}:{row.resource_id}", ttl_seconds=120
                )
                if not lease:
                    continue
                try:
                    await session.refresh(row)
                    if row.last_activity_at > now - timedelta(seconds=settings.coding_idle_seconds):
                        continue
                    request = SimpleNamespace(
                        organization_id=row.organization_id,
                        work_item_id=row.work_item_id,
                        run_id=None,
                        idempotency_key="coding-retention:" + str(row.id),
                        resource=SimpleNamespace(id=str(row.resource_id)),
                    )
                    if row.status == "creating" and not row.sandbox_id:
                        row.sandbox_id = await runtime.find(str(row.id))
                    if row.sandbox_id and row.status in {"running", "creating"}:
                        try:
                            await runtime.connect(row.sandbox_id, 120)
                            if row.evidence.get("baselineArtifactId"):
                                await export(runtime, row, request, {"constraints": {}})
                        except Exception:  # noqa: BLE001 - cleanup must continue when evidence export fails
                            row.evidence = {
                                **row.evidence,
                                "cleanupWarning": "Workspace changes could not be exported; inspect retained evidence before publication.",
                            }
                        finally:
                            await runtime.pause(row.sandbox_id)
                        row.active_seconds += (
                            min(
                                settings.coding_idle_seconds,
                                max(0, int((now - row.active_since).total_seconds())),
                            )
                            if row.active_since
                            else 0
                        )
                        row.status, row.active_since = (
                            ("creating_uncertain" if row.status == "creating" else "paused"),
                            None,
                        )
                    elif row.status == "creating":
                        row.status, row.active_since = "creating_uncertain", None
                    if row.expires_at <= now:
                        if row.sandbox_id:
                            await runtime.delete(row.sandbox_id)
                        row.status = "expired"
                        await release_unused_budget(session, row)
                    await session.commit()
                except Exception:  # noqa: BLE001 - one unavailable sandbox must not prevent cleanup of other sessions
                    await session.rollback()
                finally:
                    await coordinator.release_lock(lease)
    finally:
        await coordinator.close()
