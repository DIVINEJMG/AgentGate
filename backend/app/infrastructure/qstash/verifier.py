import base64
import hashlib
import hmac
import json
import time

from app.bootstrap.settings import settings


def _decode_segment(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _encode_digest(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class QStashSignatureVerifier:
    def verify(self, *, body: str, signature: str, url: str) -> None:
        parts = signature.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid QStash JWT.")

        header, payload_segment, supplied_signature = parts
        message = f"{header}.{payload_segment}".encode()
        keys = (
            settings.qstash_current_signing_key,
            settings.qstash_next_signing_key,
        )
        configured = [key.get_secret_value() for key in keys if key is not None]
        if not configured:
            raise RuntimeError("QStash signing keys are not configured.")

        valid = any(
            hmac.compare_digest(
                _encode_digest(
                    hmac.new(key.encode(), message, hashlib.sha256).digest()
                ),
                supplied_signature,
            )
            for key in configured
        )
        if not valid:
            raise ValueError("Invalid QStash signature.")

        payload = json.loads(_decode_segment(payload_segment))
        now = int(time.time())
        if payload.get("iss") != "Upstash":
            raise ValueError("Invalid QStash issuer.")
        if payload.get("sub") != url:
            raise ValueError("Invalid QStash subject.")
        if now > int(payload["exp"]):
            raise ValueError("QStash token expired.")
        if now < int(payload["nbf"]):
            raise ValueError("QStash token not active.")

        expected_body = _encode_digest(hashlib.sha256(body.encode()).digest())
        if str(payload.get("body", "")).rstrip("=") != expected_body:
            raise ValueError("QStash body hash mismatch.")
