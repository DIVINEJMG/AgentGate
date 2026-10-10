from __future__ import annotations

import asyncio
import hashlib
import importlib
import ipaddress
import json
import logging
import re
import shlex
import socket

from app.bootstrap.settings import settings
from app.execution.providers.integration_hooks import HookUnavailable

# SDK handles are transport hints only. Durable command identity and outcomes
# live in PostgreSQL; a lost handle never authorizes replay of a command.
_handles = {}


async def validate_registry_hosts(hosts):
    for host in hosts:
        records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not records or any(
            not ipaddress.ip_address(record[4][0]).is_global for record in records
        ):
            raise HookUnavailable(
                "Coding dependency registries must resolve exclusively to public addresses."
            )


class E2BCodingRuntime:
    def __init__(self, sdk=None):
        if (
            not settings.coding_execution_enabled
            or not settings.e2b_api_key
            or not settings.coding_template_id
        ):
            raise HookUnavailable(
                "Coding execution requires its flag, E2B key and pinned template."
            )
        self._api_key = settings.e2b_api_key.get_secret_value()
        self.timeout = min(settings.coding_idle_seconds, settings.coding_task_active_seconds)
        self._sandboxes = {}
        try:
            self.sdk = sdk or importlib.import_module("e2b").AsyncSandbox
        except ImportError as error:
            raise HookUnavailable(
                "Install the optional coding dependency to enable E2B tools."
            ) from error

    @property
    def api_key(self):
        return self._api_key

    async def create(self, *, metadata, timeout):
        hosts = [h.strip() for h in settings.coding_allowed_hosts.split(",") if h.strip()]
        if not hosts or any(not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.[a-z]{2,}", h) for h in hosts):
            raise HookUnavailable(
                "Coding registries must be explicitly configured public hostnames."
            )
        await validate_registry_hosts(hosts)
        sandbox = await self.sdk.create(
            template=settings.coding_template_id,
            api_key=self.api_key,
            timeout=timeout,
            secure=True,
            metadata=metadata,
            envs={},
            lifecycle={"on_timeout": "pause", "auto_resume": False},
            network={"allow_out": hosts, "deny_out": ["0.0.0.0/0"], "allow_public_traffic": False},
        )
        self._sandboxes[sandbox.sandbox_id] = sandbox
        return sandbox.sandbox_id

    async def connect(self, sandbox_id, timeout=None):
        if timeout is not None:
            self.timeout = timeout
        if sandbox_id not in self._sandboxes:
            self._sandboxes[sandbox_id] = await self.sdk.connect(
                sandbox_id, api_key=self.api_key, timeout=self.timeout
            )
        elif timeout is not None:
            await self._sandboxes[sandbox_id].set_timeout(timeout)
        return self._sandboxes[sandbox_id]

    async def set_timeout(self, sandbox_id, timeout):
        self.timeout = timeout
        await (await self.connect(sandbox_id)).set_timeout(timeout)

    async def release(self):
        # Handles belong to this action only; releasing them does not pause/delete the VM.
        self._sandboxes.clear()

    async def find(self, session_id):
        query = importlib.import_module("e2b").SandboxQuery(
            metadata={"codingSessionId": session_id}
        )
        paginator = self.sdk.list(query=query, api_key=self.api_key, limit=2)
        rows = await paginator.next_items()
        if paginator.has_next or len(rows) != 1:
            return None
        return rows[0].sandbox_id

    async def read(self, sandbox_id, path):
        sandbox = await self.connect(sandbox_id)
        info = await sandbox.files.get_info(path)
        if info.size > 2_000_000:
            raise ValueError("Coding file exceeds the read size budget.")
        return await sandbox.files.read(path)

    async def write(self, sandbox_id, path, content):
        await (await self.connect(sandbox_id)).files.write(path, content)

    async def ensure_control_file(self, sandbox_id, path, content, stage):
        """Reconcile backend manifests without replaying repository mutations."""
        if path not in {"/tmp/audoryn-baseline-manifest.json", "/tmp/audoryn-baseline-paths.json"}:
            raise ValueError("Unsupported workspace control file.")
        if len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("Workspace control manifest exceeds its size budget.")
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        helper = (
            "import os,stat,hashlib,json\np=" + repr(path) + "\n"
            "s=os.lstat(p) if os.path.lexists(p) else None\n"
            "if s is not None and (not stat.S_ISREG(s.st_mode) or s.st_size>2000000):\n"
            " raise ValueError('Unsafe workspace control manifest')\n"
            "print(json.dumps({'sha256':hashlib.sha256(open(p,'rb').read()).hexdigest() if s else None}))"
        )

        async def matches():
            result = await self.run(sandbox_id, "python3 -c " + shlex.quote(helper))
            if result.exit_code != 0:
                raise ValueError("Workspace control manifest could not be inspected safely.")
            return json.loads(result.stdout).get("sha256") == expected

        stage("baseline_manifest_check")
        if await matches():
            return "reused"
        stage("baseline_manifest_upload")
        try:
            await self.write(sandbox_id, path, content)
        except Exception:
            stage("baseline_manifest_reconciliation")
            try:
                confirmed = await matches()
            except Exception as probe_error:  # noqa: BLE001 - Preserve the original upload error if its verification fails.
                logging.getLogger("uvicorn.error").warning(
                    "Coding manifest reconciliation probe failed error_type=%s",
                    type(probe_error).__name__,
                )
                confirmed = False
            if not confirmed:
                stage("baseline_manifest_upload")
                raise
            return "reconciled"
        stage("baseline_manifest_verification")
        if not await matches():
            raise ValueError("Uploaded workspace control manifest checksum did not match.")
        return "uploaded"

    async def run(self, sandbox_id, command, background=False):
        sandbox = await self.connect(sandbox_id)
        if command == "mkdir -p /workspace":
            await sandbox.files.make_dir("/workspace")
        try:
            handle = await sandbox.commands.run(
                command,
                cwd="/workspace",
                background=background,
                timeout=0 if background else min(60, self.timeout),
                user="user",
            )
        except self.sdk_command_exit_error() as error:
            return error
        if background:
            _handles[(sandbox_id, handle.pid)] = handle
        return handle

    @staticmethod
    def sdk_command_exit_error():
        return importlib.import_module("e2b").CommandExitException

    async def command_result(self, sandbox_id, process_id):
        key = (sandbox_id, process_id)
        handle = _handles.get(key)
        if handle is None:
            handle = await (await self.connect(sandbox_id)).commands.connect(process_id, timeout=2)
        await asyncio.sleep(0.25)
        result = {"exitCode": handle.exit_code}
        if key not in _handles:
            await handle.disconnect()
        if handle.exit_code is not None:
            _handles.pop(key, None)
        return result

    async def cancel(self, sandbox_id, process_id):
        await (await self.connect(sandbox_id)).commands.kill(process_id)

    async def pause(self, sandbox_id):
        await self.sdk.beta_pause(sandbox_id, api_key=self.api_key)

    async def delete(self, sandbox_id):
        await self.sdk.kill(sandbox_id, api_key=self.api_key)
