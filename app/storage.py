"""Encrypted transcript storage in MinIO (self-hosted = no BAA needed)."""
from io import BytesIO
from minio import Minio
from .config import settings
from .hipaa import encrypt_transcript


def _client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_use_ssl,
    )


def store_transcript(call_id: str, transcript: str) -> str:
    """Encrypts and stores transcript. Returns MinIO object path."""
    blob = encrypt_transcript(transcript)
    client = _client()
    path = f"calls/{call_id}/transcript.enc"
    client.put_object(
        settings.minio_bucket,
        path,
        BytesIO(blob),
        length=len(blob),
        content_type="application/octet-stream",
    )
    return f"minio://{settings.minio_bucket}/{path}"
