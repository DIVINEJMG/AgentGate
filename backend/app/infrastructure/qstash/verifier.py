from qstash import Receiver

from app.bootstrap.settings import settings


class QStashSignatureVerifier:
    def __init__(self) -> None:
        current = settings.qstash_current_signing_key
        next_key = settings.qstash_next_signing_key
        if current is None or next_key is None:
            raise RuntimeError("QStash signing keys are not configured.")
        self._receiver = Receiver(
            current_signing_key=current.get_secret_value(),
            next_signing_key=next_key.get_secret_value(),
        )

    def verify(self, *, body: str, signature: str, url: str) -> None:
        self._receiver.verify(body=body, signature=signature, url=url)
