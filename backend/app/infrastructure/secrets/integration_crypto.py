from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.bootstrap.settings import settings


class IntegrationCipherError(RuntimeError):
    pass


def _fernet() -> Fernet:
    configured = settings.integration_encryption_key
    if configured is None:
        raise IntegrationCipherError(
            "Secure integration credential storage is not configured."
        )
    raw = configured.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_integration_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_integration_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise IntegrationCipherError(
            "Integration credential could not be decrypted."
        ) from error
