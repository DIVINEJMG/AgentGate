import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import boto3
import httpx
from botocore.client import Config

from app.bootstrap.settings import settings


@dataclass(frozen=True, slots=True)
class BlobCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    endpoint: str
    bucket: str
    region: str
    expires_at: int


class UpstashBlobS3Transport:
    """Python transport matching Upstash Blob's documented S3-compatible credential flow."""

    CREDENTIAL_URL = "https://blob.upstash.io/v1/credentials"

    def __init__(self, *, token: str, expected_bucket: str | None = None) -> None:
        self._token = token
        self._expected_bucket = expected_bucket
        self._cached: BlobCredentials | None = None

    @classmethod
    def from_settings(cls) -> "UpstashBlobS3Transport":
        if settings.upstash_blob_token is None:
            raise RuntimeError("UPSTASH_BLOB_TOKEN is not configured.")
        return cls(
            token=settings.upstash_blob_token.get_secret_value(),
            expected_bucket=settings.upstash_blob_bucket,
        )

    async def _credentials(self) -> BlobCredentials:
        if self._cached and self._cached.expires_at - int(time.time()) > 30:
            return self._cached

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.CREDENTIAL_URL,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            response.raise_for_status()
            payload = response.json()

        endpoint = str(payload["endpoint"])
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(
            ".r2.cloudflarestorage.com"
        ):
            raise RuntimeError("Upstash Blob returned an unexpected storage endpoint.")

        creds = BlobCredentials(
            access_key_id=str(payload["accessKeyId"]),
            secret_access_key=str(payload["secretAccessKey"]),
            session_token=str(payload["sessionToken"]),
            endpoint=endpoint,
            bucket=str(payload["bucket"]),
            region=str(payload["region"]),
            expires_at=int(payload["expiresAt"]),
        )
        if self._expected_bucket and creds.bucket != self._expected_bucket:
            raise RuntimeError("UPSTASH_BLOB_TOKEN does not belong to the configured bucket.")
        self._cached = creds
        return creds

    async def _client(self):
        creds = await self._credentials()
        client = boto3.client(
            "s3",
            endpoint_url=creds.endpoint,
            region_name=creds.region,
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
            config=Config(signature_version="s3v4"),
        )
        return creds, client

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        creds, client = await self._client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=creds.bucket,
            Key=key,
            Body=content,
            ContentType=media_type,
            CacheControl="private, max-age=3600",
        )

    async def get(self, *, key: str) -> bytes:
        creds, client = await self._client()

        def read() -> bytes:
            response = client.get_object(Bucket=creds.bucket, Key=key)
            return response["Body"].read()

        return await asyncio.to_thread(read)

    async def delete(self, *, key: str) -> None:
        creds, client = await self._client()
        await asyncio.to_thread(client.delete_object, Bucket=creds.bucket, Key=key)

    async def signed_url(self, *, key: str, expires_seconds: int) -> str:
        creds, client = await self._client()
        remaining = max(1, creds.expires_at - int(time.time()) - 5)
        expiry = min(max(1, expires_seconds), remaining)
        return await asyncio.to_thread(
            client.generate_presigned_url,
            "get_object",
            Params={"Bucket": creds.bucket, "Key": key},
            ExpiresIn=expiry,
        )

    async def healthcheck(self) -> bool:
        creds = await self._credentials()
        return bool(creds.bucket)
