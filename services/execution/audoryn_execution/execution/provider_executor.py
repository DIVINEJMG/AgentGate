from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from jsonschema import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from audoryn_execution.application.context.narrator import narrate
from audoryn_execution.execution.lifecycle import ObserveActVerifyLifecycle
from audoryn_execution.execution.providers.resolver import ExecutionResolver
from audoryn_execution.infrastructure.secrets.provider_vault import DatabaseSecretVault
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.actions.gateway import ActionProposal
from audoryn_shared.domain.integrations.contracts import IntegrationExecutionResult
from audoryn_shared.execution.authorization import (
    ExecutionAuthorizationSnapshot,
    UniversalActionRequest,
)
from audoryn_shared.execution.contracts import (
    ExecutionProviderError,
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from audoryn_shared.execution.providers.base import RecoverableExecutionProvider
from audoryn_shared.execution.providers.registry import ProviderRegistry
from audoryn_shared.execution.redaction import redacted_dict
from audoryn_shared.infrastructure.database.models import (
    Action,
    AuditEvent,
    Integration,
    IntegrationCredential,
    Run,
    WorkItem,
)
from audoryn_shared.infrastructure.database.outbox import TransactionalOutbox
from audoryn_shared.observability.metrics import ExecutionMetrics
from audoryn_shared.realtime.contracts import REALTIME_EVENT_TYPES

# Providers whose actions run without the durable integration lifecycle.
NON_DURABLE_PROVIDERS = frozenset({"browser", "web_research"})


def _durable(provider: str) -> bool:
    return settings.integration_foundation_enabled and provider not in NON_DURABLE_PROVIDERS


class ProviderContextLoader(Protocol):
    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext: ...


@runtime_checkable
class ExecutionEvidenceRecorder(Protocol):
    async def record_execution_evidence(
        self,
        *,
        request: ExecutionRequest,
        result,
        verification,
        snapshot: ExecutionAuthorizationSnapshot,
    ) -> None: ...


class DatabaseProviderContextLoader:
    def __init__(
        self,
        session: AsyncSession,
        registry: ProviderRegistry,
    ) -> None:
        self._session = session
        self._registry = registry
        self._vault = DatabaseSecretVault(session)

    async def release(self) -> None:
        """Ends the loading transaction before the provider is called. Loading may lock the
        connection's credential row (to scope or renew its token): held across the provider's
        network calls, that lock makes every other task on the same connection wait, and the open
        transaction keeps a database connection out of the pool. Only the loading's own changes
        (the credential and the connection's state) are pending here."""
        await self._session.commit()

    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext:
        raw_id = proposal.resource_id
        if raw_id.startswith("integration:"):
            raw_id = raw_id.split(":", 1)[1]
        try:
            integration_id = UUID(raw_id)
        except ValueError as error:
            raise LookupError("Action resource is not a canonical integration resource.") from error

        if _durable(proposal.provider):
            return await self._load_foundation(proposal, integration_id)

        integration = await self._session.scalar(
            select(Integration).where(
                Integration.organization_id == proposal.organization_id,
                Integration.id == integration_id,
            )
        )
        if integration is None or integration.status != "connected":
            raise LookupError("Integration is not in a connected execution state.")
        if integration.provider != proposal.provider:
            raise LookupError("Action provider does not match the connected resource.")

        provider = self._registry.get(integration.provider)
        config_raw = dict(integration.config) if isinstance(integration.config, dict) else {}
        configuration = {
            str(key): str(value)
            for key, value in config_raw.items()
            if isinstance(value, (str, int, float, bool))
        }

        credential_row = await self._session.scalar(
            select(IntegrationCredential).where(
                IntegrationCredential.integration_id == integration.id
            )
        )
        credential_reference: str | None = None
        credential: str | None = None
        if credential_row is not None:
            credential_reference = self._vault.reference_for(credential_row.id)
            # Raw secret resolution happens only here, at the provider edge.
            credential = await self._vault.read(credential_reference)

        capabilities = provider.manifest.capabilities
        resource_type = (
            str(config_raw.get("resourceType"))
            if config_raw.get("resourceType")
            else capabilities[0].resource_type
            if capabilities
            else "resource"
        )
        health = "healthy" if integration.status == "connected" else "degraded"
        raw_metadata = config_raw.get("metadata")
        metadata: dict[str, object] = (
            {str(key): value for key, value in raw_metadata.items()}
            if isinstance(raw_metadata, dict)
            else {}
        )
        resource = ResourceDescriptor(
            id=proposal.resource_id,
            provider=integration.provider,
            resource_type=resource_type,
            external_id=str(config_raw.get("resourceKey") or integration.id),
            display_name=integration.display_name,
            metadata=metadata,
            health=health,
            available_capabilities=tuple(capability.scope for capability in capabilities),
            web_url=(str(config_raw.get("webUrl")) if config_raw.get("webUrl") else None),
            configuration=configuration,
        )
        return ProviderRuntimeContext(
            configuration=configuration,
            credential=credential,
            resource=resource,
            credential_reference=credential_reference,
        )

    async def _load_foundation(
        self, proposal: ActionProposal, resource_id: UUID
    ) -> ProviderRuntimeContext:
        from audoryn_shared.application.services.integration_credentials import CredentialManager
        from audoryn_shared.application.services.integration_foundation import IntegrationFoundation

        authority = await IntegrationFoundation(self._session).task_authority(
            organization_id=proposal.organization_id,
            agent_id=proposal.agent_id,
            work_item_id=proposal.work_item_id,
            resource_id=resource_id,
            scope=proposal.scope,
            payload=proposal.payload,
        )
        # The rows the authority check just read are used as they are, not read again.
        resource, connection, state, item, grant = (
            authority.resource,
            authority.connection,
            authority.state,
            authority.item,
            authority.grant,
        )
        if connection is None or connection.provider != proposal.provider:
            raise PermissionError("Connection does not match the requested provider.")
        provider = self._registry.get(connection.provider)
        credential = await CredentialManager(self._session).resolve(
            connection=connection,
            provider=provider,
            correlation_id=proposal.correlation_id,
            state=state,
        )
        configuration = {
            str(k): str(v)
            for k, v in {**(connection.config or {}), **resource.configuration}.items()
            if isinstance(v, (str, int, float, bool))
        }
        allowed = tuple(resource.capabilities)
        granted_scopes = tuple((state.credential_metadata or {}).get("scopes", []))
        if granted_scopes:
            from audoryn_shared.execution.providers.integration_hooks import ConsentHooks

            allowed = tuple(
                s
                for s in allowed
                if isinstance(provider, ConsentHooks)
                and s in provider.capabilities_for_granted_scopes(scopes=granted_scopes)
            )
        if proposal.scope not in allowed:
            raise PermissionError("Provider consent does not cover this capability.")
        import json

        from audoryn_shared.execution.providers.integration_hooks import ScopedCredentialHooks
        from audoryn_shared.infrastructure.database.models import (
            IntegrationResource,
            IntegrationTaskGrant,
        )

        configuration["constraints"] = json.dumps(grant.constraints or {})
        credential_row = None  # read (and locked) below when the credential is scoped per action
        if isinstance(provider, ScopedCredentialHooks):
            rows = (
                await self._session.scalars(
                    select(IntegrationResource.external_id)
                    .join(
                        IntegrationTaskGrant,
                        IntegrationTaskGrant.resource_id == IntegrationResource.id,
                    )
                    .where(
                        IntegrationTaskGrant.job_revision_id == item.job_revision_id,
                        IntegrationTaskGrant.active.is_(True),
                        IntegrationResource.connection_id == connection.id,
                        IntegrationResource.resource_type == "repository",
                    )
                )
            ).all()
            configuration["authorizedRepositoryIds"] = json.dumps(list(rows))
            from audoryn_shared.application.services.integration_credentials import (
                decode_bundle,
                encode_bundle,
            )
            from audoryn_shared.execution.providers.integration_hooks import CredentialBundle
            from audoryn_shared.infrastructure.secrets.integration_crypto import (
                decrypt_integration_secret,
                encrypt_integration_secret,
            )

            credential_row = await self._session.scalar(
                select(IntegrationCredential)
                .where(IntegrationCredential.integration_id == connection.id)
                .with_for_update()
            )
            bundle = (
                decode_bundle(decrypt_integration_secret(credential_row.ciphertext))
                if credential_row
                else CredentialBundle(access_token="")
            )
            credential, updated_bundle = await provider.scope_execution_credential(
                configuration=configuration,
                scope=proposal.scope,
                bundle=bundle,
                input=proposal.payload,
            )
            if updated_bundle != bundle:
                if credential_row is None:
                    raise PermissionError(
                        "Credential renewal requires an existing encrypted connection credential."
                    )
                credential_row.ciphertext = encrypt_integration_secret(
                    encode_bundle(updated_bundle)
                )
                await self._session.flush()
        descriptor = ResourceDescriptor(
            id=str(resource.id),
            provider=resource.provider,
            resource_type=resource.resource_type,
            external_id=resource.external_id,
            display_name=resource.display_name,
            metadata={
                "connectionId": str(connection.id),
                "authorityVersion": state.authority_version,
            },
            health="healthy" if resource.health == "healthy" else "unknown",
            available_capabilities=allowed,
            configuration=configuration,
            web_url=resource.web_url,
        )
        row = (
            credential_row
            if isinstance(provider, ScopedCredentialHooks)
            else await self._session.scalar(
                select(IntegrationCredential).where(
                    IntegrationCredential.integration_id == connection.id
                )
            )
        )
        return ProviderRuntimeContext(
            configuration=configuration,
            credential=credential,
            resource=descriptor,
            credential_reference=self._vault.reference_for(row.id) if row else None,
        )

    async def checkpoint_write_dispatch(self, request: ExecutionRequest) -> bool:
        # Workspace actions change only their own task's isolated sandbox, so they neither wait for
        # nor hold back writes to the shared resource (and are safe to repeat).
        workspace = request.capability.target == "workspace"
        # A write held back by another one waits only while that write's own task is still working
        # on it (running, or re-checking its outcome). Once that task is paused for a person to
        # check, or has ended, it no longer holds back other tasks.
        conflicting = (
            None
            if workspace
            else await self._session.scalar(
                select(Action.id)
                .outerjoin(Run, Run.id == Action.run_id)
                .outerjoin(WorkItem, WorkItem.id == Run.work_item_id)
                .where(
                    Action.organization_id == request.organization_id,
                    Action.resource_id == request.resource.id,
                    Action.idempotency_key != request.idempotency_key,
                    Action.status == "processing",
                    Action.payload["integrationCertainty"].astext == "dispatching",
                    Action.scope.not_like("%.workspace.%"),
                    or_(
                        Action.run_id.is_(None),
                        WorkItem.status.in_(["queued", "admitted", "running", "waiting_approval"]),
                    ),
                )
                .limit(1)
            )
        )
        if conflicting is not None:
            raise ExecutionProviderError.for_request(
                request,
                "rate_limited",
                "Another write on this resource is still in flight or has an uncertain outcome. This action will wait for reconciliation.",
                retryable=True,
                retry_after_seconds=60,
            )
        row = await self._session.scalar(
            select(Action)
            .where(
                Action.organization_id == request.organization_id,
                Action.idempotency_key == request.idempotency_key,
            )
            .with_for_update()
        )
        if row is None:
            raise PermissionError(
                "Durable action record is required before dispatching an integration write."
            )
        payload = dict(row.payload or {})
        # A workspace action repeated after an interruption simply runs again (it finds its work done).
        reconcile = payload.get("integrationCertainty") == "dispatching" and not workspace
        payload["integrationCertainty"] = "dispatching"
        row.payload = payload
        from audoryn_shared.execution.providers.integration_hooks import ExecutionEvidenceHooks

        provider = self._registry.get(request.resource.provider)
        if isinstance(provider, ExecutionEvidenceHooks):
            await provider.record_dispatch(session=self._session, request=request)
        # Persist before network I/O: a killed process must reconcile on redelivery.
        await self._session.commit()
        return reconcile

    async def record_execution_evidence(
        self,
        *,
        request: ExecutionRequest,
        result,
        verification,
        snapshot: ExecutionAuthorizationSnapshot,
    ) -> None:
        output = result.output if isinstance(result.output, dict) else {}
        action_evidence = output.get("actionEvidence")
        evidence = action_evidence if isinstance(action_evidence, dict) else {}
        verification_failure_artifact = verification.details.get("verificationEvidenceArtifact")
        artifact_ids = list(result.artifacts)
        if isinstance(verification_failure_artifact, dict):
            raw_id = verification_failure_artifact.get("id")
            if raw_id is not None and str(raw_id) not in artifact_ids:
                artifact_ids.append(str(raw_id))

        audit = AuditEvent(
            organization_id=request.organization_id,
            event_type="action.executed",
            category="execution",
            severity="info" if verification.verified else "warning",
            correlation_id=request.correlation_id,
            actor={"type": "agent", "id": str(request.agent_id)},
            resource={
                "type": "execution_resource",
                "id": request.resource.id,
                "name": request.resource.display_name,
            },
            payload={
                "outcome": "verified" if verification.verified else "verification_failed",
                "summary": verification.summary,
                "metadata": {
                    "provider": result.provider,
                    "adapter": result.adapter,
                    "adapterVersion": result.adapter_version,
                    "capability": request.capability.scope,
                    "operation": request.operation,
                    "resourceId": request.resource.id,
                    "actionEvidence": evidence,
                    "verification": {
                        "verified": verification.verified,
                        "summary": verification.summary,
                        "details": verification.details,
                    },
                    "artifactIds": artifact_ids,
                    "authorization": snapshot.as_dict(),
                },
            },
        )
        self._session.add(audit)
        from audoryn_shared.execution.providers.integration_hooks import ExecutionEvidenceHooks

        provider = self._registry.get(request.resource.provider)
        if isinstance(provider, ExecutionEvidenceHooks):
            await provider.record_outcome(
                session=self._session, request=request, verification=verification
            )
        provider_events = output.get("realtimeEvents")
        if isinstance(provider_events, list):
            for index, event in enumerate(provider_events):
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                if event_type not in REALTIME_EVENT_TYPES:
                    continue
                raw_payload = event.get("payload")
                event_payload = (
                    {str(key): value for key, value in raw_payload.items()}
                    if isinstance(raw_payload, dict)
                    else {}
                )
                await TransactionalOutbox(self._session).enqueue(
                    topic=event_type,
                    aggregate_type="browser_execution",
                    aggregate_id=f"{request.idempotency_key}:{index}",
                    payload={
                        "organization_id": str(request.organization_id),
                        "worker_id": (str(request.worker_id) if request.worker_id else None),
                        "run_id": str(request.run_id) if request.run_id else None,
                        "resource_id": request.resource.id,
                        "provider": result.provider,
                        "capability": request.capability.scope,
                        "operation": request.operation,
                        "correlation_id": request.correlation_id,
                        **redacted_dict(event_payload),
                    },
                )
        await TransactionalOutbox(self._session).enqueue(
            topic="action.executed",
            aggregate_type="execution",
            aggregate_id=request.idempotency_key,
            payload={
                "organization_id": str(request.organization_id),
                "worker_id": str(request.worker_id) if request.worker_id else None,
                "run_id": str(request.run_id) if request.run_id else None,
                "resource_id": request.resource.id,
                "provider": result.provider,
                "capability": request.capability.scope,
                "operation": request.operation,
                "verified": verification.verified,
                "artifact_ids": artifact_ids,
                "correlation_id": request.correlation_id,
            },
        )
        await self._session.flush()


class UniversalProviderExecutor:
    """Provider-neutral executor behind the Action Gateway.

    Authorization is completed before execute_request is called. Provider-specific
    behavior remains inside the registered adapters.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        context_loader: ProviderContextLoader,
    ) -> None:
        self._registry = registry
        self._resolver = ExecutionResolver(registry)
        self._context_loader = context_loader
        self._lifecycle = ObserveActVerifyLifecycle()
        self._metrics = ExecutionMetrics()

    async def prepare(self, proposal: ActionProposal) -> UniversalActionRequest:
        context = await self._context_loader.load(proposal)
        provider = self._registry.get(proposal.provider)
        capability = next(
            (item for item in provider.manifest.capabilities if item.scope == proposal.scope),
            None,
        )
        if capability is None:
            raise LookupError("Connected provider does not expose the requested capability.")

        try:
            normalized_input = await provider.normalize_input(
                operation=capability.operation,
                input=proposal.payload,
            )
        except ValidationError as error:
            path = ".".join(str(part) for part in error.absolute_path) or "input"
            raise ExecutionProviderError(
                code="validation_error",
                retryable=False,
                provider=proposal.provider,
                operation=capability.operation,
                correlation_id=proposal.correlation_id,
                safe_message=f"The proposed {path} violates the tool's {error.validator} constraint. Nothing was dispatched; inspect the required value and replan.",
            ) from error
        except ValueError as error:
            # The provider refused the input before anything was sent (F49): an invalid action to
            # replan, never an interrupted one whose outcome needs checking.
            raise ExecutionProviderError(
                code="validation_error",
                retryable=False,
                provider=proposal.provider,
                operation=capability.operation,
                correlation_id=proposal.correlation_id,
                safe_message=f"{error} Nothing was dispatched; choose an action the tool allows and replan.",
            ) from error
        from audoryn_shared.execution.providers.integration_hooks import ActionPolicyHooks

        if isinstance(provider, ActionPolicyHooks):
            capability = provider.action_capability(capability=capability, input=normalized_input)
        request = ExecutionRequest(
            organization_id=proposal.organization_id,
            worker_id=None,
            agent_id=proposal.agent_id,
            job_id=None,
            work_item_id=proposal.work_item_id,
            run_id=None,
            capability=capability,
            resource=context.resource,
            operation=capability.operation,
            input=normalized_input,
            correlation_id=proposal.correlation_id,
            idempotency_key=proposal.idempotency_key,
        )
        permissions = await provider.discover_permissions(
            resource=context.resource,
            configuration=context.configuration,
            credential=context.credential,
            credential_reference=context.credential_reference,
        )
        return UniversalActionRequest(
            execution=request,
            provider_permissions=permissions,
        )

    async def execute_request(
        self,
        request: UniversalActionRequest,
        snapshot: ExecutionAuthorizationSnapshot,
    ) -> IntegrationExecutionResult:
        execution = request.execution
        _check_snapshot(snapshot, execution)
        proposal = ActionProposal(
            organization_id=execution.organization_id,
            agent_id=execution.agent_id,
            provider=execution.resource.provider,
            operation=execution.operation,
            scope=execution.capability.scope,
            resource_id=execution.resource.id,
            payload=execution.input,
            correlation_id=execution.correlation_id,
            idempotency_key=execution.idempotency_key,
            work_item_id=execution.work_item_id,
        )
        context = await self._context_loader.load(proposal)
        if isinstance(self._context_loader, DatabaseProviderContextLoader):
            await self._context_loader.release()
        resolved = await self._resolver.resolve(
            execution,
            contexts={execution.resource.provider: context},
        )
        recoverable_provider = (
            resolved.provider
            if isinstance(resolved.provider, RecoverableExecutionProvider)
            else None
        )
        recover = None
        if recoverable_provider is not None:
            recover = lambda error, result: recoverable_provider.recover_execution(
                request=execution,
                error=error,
                result=result,
                configuration=resolved.context.configuration,
                credential=resolved.context.credential,
            )
        durable = _durable(execution.resource.provider)

        async def execute():
            if durable:
                from audoryn_execution.execution.integration_execution import execute_native_action

                before_dispatch = None
                if execution.capability.side_effect and isinstance(
                    self._context_loader, DatabaseProviderContextLoader
                ):
                    checkpoint = self._context_loader.checkpoint_write_dispatch
                    before_dispatch = lambda: checkpoint(execution)
                return await execute_native_action(
                    provider=resolved.provider,
                    request=execution,
                    context=resolved.context,
                    before_dispatch=before_dispatch,
                )
            return await resolved.provider.execute(
                request=execution,
                configuration=resolved.context.configuration,
                credential=resolved.context.credential,
            )

        lifecycle = self._lifecycle
        if durable:
            from audoryn_execution.execution.integration_execution import DurableIntegrationRecovery

            lifecycle = ObserveActVerifyLifecycle(DurableIntegrationRecovery())
        outcome = await lifecycle.run(
            request=execution,
            execute=execute,
            verify=lambda result: resolved.provider.verify(
                request=execution,
                result=result,
                configuration=resolved.context.configuration,
                credential=resolved.context.credential,
            ),
            recover=recover,
        )
        if outcome.error is not None:
            raise _outcome_error(execution, outcome.error, durable=durable)
        if outcome.result is None or outcome.verification is None:
            raise RuntimeError("Execution lifecycle completed without a provider result.")
        await self._record_outcome(execution, outcome, snapshot)
        return _integration_result(execution, outcome, snapshot)

    async def _record_outcome(
        self, execution, outcome, snapshot: ExecutionAuthorizationSnapshot
    ) -> None:
        result, verification = outcome.result, outcome.verification
        await self._metrics.execution(
            provider=result.provider,
            capability=execution.capability.scope,
            adapter=result.adapter,
            correlation_id=execution.correlation_id,
            latency_ms=result.latency_ms,
            verified=verification.verified,
            retries=max(0, sum(1 for item in outcome.checkpoints if item.stage == "act") - 1),
            fallback_used=outcome.recovery is not None
            and outcome.recovery.action == "fallback_provider",
        )
        if isinstance(self._context_loader, ExecutionEvidenceRecorder):
            await self._context_loader.record_execution_evidence(
                request=execution,
                result=result,
                verification=verification,
                snapshot=snapshot,
            )

    async def execute(
        self,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        """Compatibility path for direct pre-F29 callers.

        The Action Gateway should call prepare + execute_request so authorization
        snapshots are always present on real provider execution.
        """
        await self.prepare(proposal)
        raise PermissionError(
            "Direct provider execution is disabled; execute through ActionGateway."
        )


def _check_snapshot(snapshot: ExecutionAuthorizationSnapshot, execution) -> None:
    for name, expected, actual in (
        ("capability", snapshot.capability_scope, execution.capability.scope),
        ("resource", snapshot.resource_id, execution.resource.id),
        ("correlation", snapshot.correlation_id, execution.correlation_id),
    ):
        if expected != actual:
            raise PermissionError(f"Authorization snapshot {name} mismatch.")


def _outcome_error(execution, error, *, durable: bool) -> ExecutionProviderError:
    """The lifecycle's error. A durable write that ran but could not be verified is uncertain,
    so it is never replayed automatically."""
    if durable and execution.capability.side_effect and error.code == "verification_failed":
        return ExecutionProviderError.for_request(
            execution,
            "uncertain_outcome",
            "This write was accepted, but verification did not establish its outcome. Automatic replay is paused.",
        )
    return ExecutionProviderError(
        code=error.code,
        retryable=error.retryable,
        provider=error.provider,
        operation=error.operation,
        correlation_id=error.correlation_id,
        safe_message=error.safe_message,
        internal_details=error.internal_details,
        retry_after_seconds=error.retry_after_seconds,
    )


def _integration_result(
    execution, outcome, snapshot: ExecutionAuthorizationSnapshot
) -> IntegrationExecutionResult:
    from audoryn_execution.execution.integration_evidence import external_references

    result, verification = outcome.result, outcome.verification
    # Say what the step did (a write never reads as a read), so the planner and the person can
    # tell an empty answer from a failed one. The verification note stays in the data below.
    return IntegrationExecutionResult(
        provider_operation=result.operation,
        summary=narrate(execution.capability.scope, result.output, execution.input)
        or verification.summary,
        data={
            "executionCertainty": "verified" if verification.verified else "uncertain",
            "externalReferences": external_references(result.output),
            "output": result.output,
            "verification": {
                "verified": verification.verified,
                "summary": verification.summary,
                "details": verification.details,
            },
            "lifecycle": [
                {
                    "stage": checkpoint.stage,
                    "attempt": checkpoint.attempt,
                    "detail": checkpoint.detail,
                }
                for checkpoint in outcome.checkpoints
            ],
            "authorization": snapshot.as_dict(),
        },
    )
