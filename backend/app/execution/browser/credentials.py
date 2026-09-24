from __future__ import annotations

import json
from dataclasses import dataclass


class BrowserAuthenticationFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserCredentialBundle:
    """Runtime-only credential values resolved from Secret Vault material."""

    _values: dict[str, str]

    @classmethod
    def from_secret(cls, raw_secret: str | None) -> BrowserCredentialBundle:
        if not raw_secret:
            raise BrowserAuthenticationFailure("Browser authentication credential is unavailable.")
        try:
            value = json.loads(raw_secret)
        except json.JSONDecodeError as error:
            raise BrowserAuthenticationFailure(
                "Browser authentication credential must be a JSON object."
            ) from error
        if not isinstance(value, dict) or not value:
            raise BrowserAuthenticationFailure(
                "Browser authentication credential must be a non-empty JSON object."
            )

        parsed: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = str(key).strip()
            if not normalized_key or not isinstance(item, (str, int, float, bool)):
                raise BrowserAuthenticationFailure(
                    "Browser authentication credential contains an unsupported value."
                )
            parsed[normalized_key] = str(item)
        return cls(parsed)

    def resolve(self, key: str) -> str:
        try:
            return self._values[key]
        except KeyError as error:
            raise BrowserAuthenticationFailure(
                "Requested browser credential field is unavailable."
            ) from error

    def sensitive_values(self) -> tuple[str, ...]:
        return tuple(value for value in self._values.values() if value)
