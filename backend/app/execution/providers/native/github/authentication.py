"""App signing and verified user/install access; no tokens enter planner context."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.bootstrap.settings import settings
from app.execution.providers.integration_hooks import CredentialBundle
from app.execution.providers.native.http import ProviderTransportError

from .transport import account_context


def app_jwt() -> str:
    if not settings.github_app_id or not settings.github_app_private_key:
        raise ProviderTransportError(
            code="authentication_error",
            retryable=False,
            safe_message="GitHub App signing is not configured.",
        )
    key = serialization.load_pem_private_key(
        settings.github_app_private_key.get_secret_value().encode(), password=None
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProviderTransportError(
            code="authentication_error",
            retryable=False,
            safe_message="GitHub App requires an RSA signing key.",
        )

    def encode(value):
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    now = int(time.time())
    unsigned = (
        encode({"alg": "RS256", "typ": "JWT"})
        + "."
        + encode({"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id})
    )
    signature = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
    return unsigned + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")


def permission_scopes(permissions: dict) -> tuple[str, ...]:
    return tuple(
        sorted(
            f"{name}:{level}" for name, level in permissions.items() if level in {"read", "write"}
        )
    )


class GitHubAuthentication:
    def __init__(self, http):
        self.http = http

    async def initiate_authorization(self, *, state, callback_url, code_challenge=None):
        if not settings.github_client_id or not settings.github_client_secret:
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message="GitHub user authorization is not configured.",
            )
        return "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": settings.github_client_id,
                "redirect_uri": callback_url,
                "state": state,
                **(
                    {"code_challenge": code_challenge, "code_challenge_method": "S256"}
                    if code_challenge
                    else {}
                ),
            }
        )

    async def complete_authorization(self, *, code, callback_url, code_verifier=None):
        token = await self._oauth(
            {
                "code": code,
                "redirect_uri": callback_url,
                **({"code_verifier": code_verifier} if code_verifier else {}),
            }
        )
        profile = await self.http.request(
            method="GET", url="https://api.github.com/user", credential=token["access_token"]
        )
        return CredentialBundle(
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            expires_at=datetime.fromtimestamp(time.time() + token["expires_in"], tz=UTC)
            if token.get("expires_in")
            else None,
            scopes=(),
            account_id=str(profile.data["id"]),
            renewal_metadata={"strategy": "github_user", "login": profile.data["login"]},
        )

    async def _oauth(self, payload):
        if not settings.github_client_secret:
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message="GitHub user authorization is not configured.",
            )
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    json={
                        "client_id": settings.github_client_id,
                        "client_secret": settings.github_client_secret.get_secret_value(),
                        **payload,
                    },
                )
            data = response.json()
        except (httpx.RequestError, ValueError) as error:
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub authorization returned no usable response.",
            ) from error
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub authorization is temporarily unavailable.",
            )
        if not isinstance(data, dict):
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub authorization returned an unexpected response.",
            )
        if response.status_code != 200 or not data.get("access_token"):
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message="GitHub rejected authorization or renewal. Reconnect the account.",
            )
        return data

    async def verify_installation(self, *, installation_id: str, user_bundle: CredentialBundle):
        # The user-visible installation list establishes ownership/access before
        # any app-level signing credential is used to mint an installation token.
        installation = None
        for page in range(1, 11):
            response = await self.http.request(
                method="GET",
                url=f"https://api.github.com/user/installations?per_page=100&page={page}",
                credential=user_bundle.access_token,
            )
            rows = response.data.get("installations", [])
            installation = next((r for r in rows if str(r.get("id")) == installation_id), None)
            if installation or len(rows) < 100:
                break
        if not installation or installation.get("suspended_at"):
            raise ProviderTransportError(
                code="authorization_error",
                retryable=False,
                safe_message="The selected installation is inaccessible or suspended.",
            )
        trusted = await self.http.request(
            method="GET",
            url=f"https://api.github.com/app/installations/{installation_id}",
            credential=app_jwt(),
        )
        if str(trusted.data.get("app_id")) != settings.github_app_id:
            raise ProviderTransportError(
                code="authorization_error",
                retryable=False,
                safe_message="The installation belongs to another GitHub App.",
            )
        return trusted.data

    async def installation_bundle(
        self, installation_id: str, *, repositories=None, permissions=None
    ):
        payload = {}
        if repositories is not None:
            payload["repository_ids"] = [int(value) for value in repositories]
        if permissions:
            payload["permissions"] = permissions
        token = account_context.set(installation_id)
        try:
            response = await self.http.request(
                method="POST",
                url=f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                credential=app_jwt(),
                json_body=payload,
            )
        finally:
            account_context.reset(token)
        data = response.data
        return CredentialBundle(
            access_token=data["token"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            scopes=permission_scopes(data.get("permissions", {})),
            account_id="installation:" + installation_id,
            renewal_metadata={
                "strategy": "github_installation",
                "installationId": installation_id,
                "repositoryIds": repositories or [],
                "permissions": permissions or {},
            },
        )

    async def renew_credentials(self, *, bundle):
        metadata = bundle.renewal_metadata
        if metadata.get("strategy") == "github_installation":
            from dataclasses import replace

            renewed = await self.installation_bundle(
                metadata["installationId"],
                repositories=metadata.get("repositoryIds"),
                permissions=metadata.get("permissions"),
            )
            return replace(
                renewed,
                renewal_metadata={
                    **renewed.renewal_metadata,
                    **(
                        {"userBundle": metadata["userBundle"]} if metadata.get("userBundle") else {}
                    ),
                },
            )
        data = await self._oauth(
            {"grant_type": "refresh_token", "refresh_token": bundle.refresh_token}
        )
        return CredentialBundle(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            account_id=bundle.account_id,
            renewal_metadata=metadata,
            scopes=bundle.scopes,
            expires_at=datetime.fromtimestamp(time.time() + data.get("expires_in", 28800), tz=UTC),
        )

    async def revoke_credentials(self, *, bundle):
        if bundle.renewal_metadata.get("strategy") == "github_installation":
            await self.http.request(
                method="DELETE",
                url="https://api.github.com/installation/token",
                credential=bundle.access_token,
            )
            return
        # A user token is owned by the connection; do not uninstall shared installations.
        if not settings.github_client_secret:
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message="GitHub revocation is not configured.",
            )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                "DELETE",
                f"https://api.github.com/applications/{settings.github_client_id}/token",
                auth=(settings.github_client_id, settings.github_client_secret.get_secret_value()),
                json={"access_token": bundle.access_token},
            )
        if response.status_code not in {204, 404}:
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message="GitHub could not confirm credential revocation.",
            )
