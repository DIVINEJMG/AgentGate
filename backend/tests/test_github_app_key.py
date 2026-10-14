"""The GitHub App key is read however the environment carries it, and an unreadable key is a
clear configuration message rather than an internal error."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.execution.providers.native.github import authentication
from app.execution.providers.native.http import ProviderTransportError

PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
BACKSLASH_N = chr(92) + "n"
ONE_LINE = PEM.strip().replace("\n", BACKSLASH_N)


def test_the_one_line_form_really_is_one_line() -> None:
    assert "\n" not in ONE_LINE and BACKSLASH_N in ONE_LINE


@pytest.mark.parametrize("value", [
    PEM,                     # multi-line (python-dotenv, quoted)
    ONE_LINE,                # one line with \n escapes (podman --env-file)
    f'"{ONE_LINE}"',         # podman keeps surrounding quotes
    f"'{ONE_LINE}'",
    PEM.replace("\n", "\r\n"),
    ONE_LINE.replace(BACKSLASH_N, chr(92) + "r" + BACKSLASH_N),
])
def test_the_key_is_read_in_any_common_environment_form(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(authentication.settings, "github_app_id", "5211774")
    monkeypatch.setattr(authentication.settings, "github_app_private_key", SecretStr(value))
    assert authentication.app_jwt().count(".") == 2


def test_an_unreadable_key_is_a_clear_configuration_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authentication.settings, "github_app_id", "5211774")
    monkeypatch.setattr(authentication.settings, "github_app_private_key", SecretStr('"-----BEGIN RSA PRIVATE KEY-----'))
    with pytest.raises(ProviderTransportError) as raised:
        authentication.app_jwt()
    assert raised.value.code == "authentication_error" and "GITHUB_APP_PRIVATE_KEY" in str(raised.value)
