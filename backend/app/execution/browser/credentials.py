from __future__ import annotations

import base64
import hmac
import json
import struct
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

# A stored `otpSecret` is never typed: it is offered as `oneTimeCode`, the current code (F36).
OTP_SECRET_FIELD = "otpSecret"
ONE_TIME_CODE_FIELD = "oneTimeCode"
_DIGESTS = {"SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}


class BrowserAuthenticationFailure(RuntimeError):
    pass


def one_time_code(secret: str, at: float | None = None) -> str:
    """The RFC 6238 code for a stored secret (base32, or an otpauth:// link) at a moment."""
    key, digits, period, digest = _otp_parameters(secret)
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def _otp_parameters(secret: str) -> tuple[bytes, int, int, str]:
    text = secret.strip()
    options: dict[str, str] = {}
    if text.lower().startswith("otpauth://"):
        options = {key: values[0] for key, values in parse_qs(urlsplit(text).query).items()}
        text = options.get("secret", "")
    cleaned = text.replace(" ", "").replace("-", "").upper()
    try:
        key = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
        digits = int(options.get("digits", 6))
        period = int(options.get("period", 30))
        digest = _DIGESTS[options.get("algorithm", "SHA1").upper()]
    except (ValueError, KeyError) as error:
        raise _bad_secret() from error
    if not key or not 6 <= digits <= 8 or period <= 0:
        raise _bad_secret()
    return key, digits, period, digest


def _bad_secret() -> BrowserAuthenticationFailure:
    return BrowserAuthenticationFailure(
        f"The stored {OTP_SECRET_FIELD} is not a one-time-code secret "
        "(a base32 key or an otpauth:// link)."
    )


def login_field_names(raw_secret: str | None) -> list[str]:
    """The field names a stored login holds (never their values); none when it is not a login."""
    try:
        return list(BrowserCredentialBundle.from_secret(raw_secret).keys())
    except BrowserAuthenticationFailure:
        return []


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
        if OTP_SECRET_FIELD in parsed:
            _otp_parameters(parsed[OTP_SECRET_FIELD])
        return cls(parsed)

    def resolve(self, key: str) -> str:
        if key == ONE_TIME_CODE_FIELD and OTP_SECRET_FIELD in self._values:
            return one_time_code(self._values[OTP_SECRET_FIELD])
        if key != OTP_SECRET_FIELD and key in self._values:
            return self._values[key]
        raise BrowserAuthenticationFailure(
            f"The stored login has no field {key!r}; it has: {', '.join(self.keys())}."
        )

    def require(self, keys: tuple[str, ...]) -> None:
        """Every named field is stored, checked before anything is typed (F35)."""
        for key in keys:
            self.resolve(key)

    def keys(self) -> tuple[str, ...]:
        """The field names a sign-in can bind (never their values); a stored otpSecret is offered as
        oneTimeCode."""
        names = [key for key in self._values if key != OTP_SECRET_FIELD]
        if OTP_SECRET_FIELD in self._values:
            names.append(ONE_TIME_CODE_FIELD)
        return tuple(dict.fromkeys(names))

    def sensitive_values(self) -> tuple[str, ...]:
        return tuple(value for value in self._values.values() if value)
