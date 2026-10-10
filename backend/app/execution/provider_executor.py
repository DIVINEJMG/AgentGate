from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from jsonschema import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.settings import settings
from app.domain.actions.gateway import ActionProposal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.authorization import (
    ExecutionAuthorizationSnapshot,
    UniversalActionRequest,
)
from app.execution.contracts import (
    ExecutionProviderError,
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from app.execution.lifecycle import ObserveActVerifyLifecycle
from app.execution.providers.base import RecoverableExecutionProvider
from app.execution.providers.registry import ProviderRegistry
from app.execution.providers.resolver import ExecutionResolver
from app.execution.redaction import redacted_dict
from app.infrastructure.database.models import (
    Action,
    AuditEvent,
    Integration,
    IntegrationCredential,
)
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.secrets.provider_vault import DatabaseSecretVault
from app.observability.metrics import ExecutionMetrics
from app.realtime.contracts import REALTIME_EVENT_TYPES


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

    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext:
        raw_id = proposal.resource_id
        if raw_id.startswith("integration:"):
            raw_id = raw_id.split(":", 1)[1]
        try:
            integration_id = UUID(raw_id)
        except ValueError as error:
            raise LookupError("Action resource is not a canonical integration resource.") from error

        if settings.integration_foundation_enabled and proposal.provider not in {
            "browser",
            "web_research",
        }:
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
        from app.application.services.integration_credentials import CredentialManager
        from app.application.services.integration_foundation import IntegrationFoundation

        resource = await IntegrationFoundation(self._session).authorize(
            organization_id=proposal.organization_id,
            agent_id=proposal.agent_id,
            work_item_id=proposal.work_item_id,
            resource_id=resource_id,
            scope=proposal.scope,
            payload=proposal.payload,
        )
        connection = await self._session.get(Integration, resource.connection_id)
        if connection is None or connection.provider != proposal.provider:
            raise PermissionError("Connection does not match the requested provider.")
        provider = self._registry.get(connection.provider)
        credential = await CredentialManager(self._session).resolve(
            connection=connection, provider=provider, correlation_id=proposal.correlation_id
        )
        configuration = {
            str(k): str(v)
            for k, v in {**(connection.config or {}), **resource.configuration}.items()
            if isinstance(v, (str, int, float, bool))
        }
        from app.infrastructure.database.models import IntegrationConnectionState

        state = await self._session.scalar(
            select(IntegrationConnectionState).where(
                IntegrationConnectionState.connection_id == connection.id
            )
        )
        if state is None:
            raise PermissionError("Connection ownership and authorization require review.")
        allowed = tuple(resource.capabilities)
        granted_scopes = tuple((state.credential_metadata or {}).get("scopes", []))
        if granted_scopes:
            from app.execution.providers.integration_hooks import ConsentHooks

            allowed = tuple(
                s
                for s in allowed
                if isinstance(provider, ConsentHooks)
                and s in provider.capabilities_for_granted_scopes(scopes=granted_scopes)
            )
        if proposal.scope not in allowed:
            raise PermissionError("Provider consent does not cover this capability.")
        from app.execution.providers.integration_hooks import ScopedCredentialHooks
        from app.infrastructure.database.models import (
            IntegrationResource,
            IntegrationTaskGrant,
            WorkItem,
        )

        item = await self._session.get(WorkItem, proposal.work_item_id)
        if item is None:
            raise PermissionError("Integration work item no longer exists.")
        grant = await self._session.scalar(
            select(IntegrationTaskGrant).where(
                IntegrationTaskGrant.job_revision_id == item.job_revision_id,
                IntegrationTaskGrant.resource_id == resource.id,
                IntegrationTaskGrant.active.is_(True),
            )
        )
        import json

        if grant is None:
            raise PermissionError("Integration task grant no longer exists.")
        configuration["constraints"] = json.dumps(grant.constraints or {})
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
            from app.application.services.integration_credentials import (
                decode_bundle,
                encode_bundle,
            )
            from app.execution.providers.integration_hooks import CredentialBundle
            from app.infrastructure.secrets.integration_crypto import (
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
        row = await self._session.scalar(
            select(IntegrationCredential).where(
                IntegrationCredential.integration_id == connection.id
            )
        )
        return ProviderRuntimeContext(
            configuration=configuration,
            credential=credential,
            resource=descriptor,
            credential_reference=self._vault.reference_for(row.id) if row else None,
        )

    async def checkpoint_write_dispatch(self, request: ExecutionRequest) -> bool:
        conflicting = await self._session.scalar(
            select(Action.id).where(
                Action.organization_id == request.organization_id,
                Action.resource_id == request.resource.id,
                Action.idempotency_key != request.idempotency_key,
                Action.status == "processing",
                Action.payload["integrationCertainty"].astext == "dispatching",
            )
        )
        if conflicting is not None:
            raise ExecutionProviderError(
                code="rate_limited",
                retryable=True,
                provider=request.resource.provider,
                operation=request.operation,
                correlation_id=request.correlation_id,
                retry_after_seconds=60,
                safe_message="Another write on this resource is still in flight or has an uncertain outcome. This action will wait for reconciliation.",
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
        reconcile = payload.get("integrationCertainty") == "dispatching"
        payload["integrationCertainty"] = "dispatching"
        row.payload = payload
        from app.execution.providers.integration_hooks import ExecutionEvidenceHooks

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
        from app.execution.providers.integration_hooks import ExecutionEvidenceHooks

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
        from app.execution.providers.integration_hooks import ActionPolicyHooks

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
        if snapshot.capability_scope != execution.capability.scope:
            raise PermissionError("Authorization snapshot capability mismatch.")
        if snapshot.resource_id != execution.resource.id:
            raise PermissionError("Authorization snapshot resource mismatch.")
        if snapshot.correlation_id != execution.correlation_id:
            raise PermissionError("Authorization snapshot correlation mismatch.")

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

        async def execute():
            if settings.integration_foundation_enabled and execution.resource.provider not in {
                "browser",
                "web_research",
            }:
                from app.execution.integration_execution import execute_native_action

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
        if settings.integration_foundation_enabled and execution.resource.provider not in {
            "browser",
            "web_research",
        }:
            from app.execution.integration_execution import DurableIntegrationRecovery

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
            if (
                settings.integration_foundation_enabled
                and execution.resource.provider not in {"browser", "web_research"}
                and execution.capability.side_effect
                and outcome.error.code == "verification_failed"
            ):
                raise ExecutionProviderError(
                    code="uncertain_outcome",
                    retryable=False,
                    provider=execution.resource.provider,
                    operation=execution.operation,
                    correlation_id=execution.correlation_id,
                    safe_message="This write was accepted, but verification did not establish its outcome. Automatic replay is paused.",
                )
            raise ExecutionProviderError(
                code=outcome.error.code,
                retryable=outcome.error.retryable,
                provider=outcome.error.provider,
                operation=outcome.error.operation,
                correlation_id=outcome.error.correlation_id,
                safe_message=outcome.error.safe_message,
                internal_details=outcome.error.internal_details,
                retry_after_seconds=outcome.error.retry_after_seconds,
            )
        if outcome.result is None or outcome.verification is None:
            raise RuntimeError("Execution lifecycle completed without a provider result.")

        result = outcome.result
        verification = outcome.verification
        retry_count = max(
            0,
            len([item for item in outcome.checkpoints if item.stage == "act"]) - 1,
        )
        await self._metrics.execution(
            provider=result.provider,
            capability=execution.capability.scope,
            adapter=result.adapter,
            correlation_id=execution.correlation_id,
            latency_ms=result.latency_ms,
            verified=verification.verified,
            retries=retry_count,
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

        summary = verification.summary
        from app.execution.integration_evidence import external_references

        return IntegrationExecutionResult(
            provider_operation=result.operation,
            summary=summary,
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
