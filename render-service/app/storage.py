"""S3-compatible storage helper (MinIO)."""
from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import Optional

from minio import Minio
from minio.error import S3Error

from .config import settings


class Storage:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket = settings.minio_bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except S3Error:
            # MinIO init container suele crearlo; si falla aqui no es fatal
            pass

    def upload_file(self, local_path: str | Path, object_name: str) -> str:
        local_path = Path(local_path)
        content_type, _ = mimetypes.guess_type(str(local_path))
        content_type = content_type or "application/octet-stream"
        self.client.fput_object(
            self.bucket, object_name, str(local_path),
            content_type=content_type,
        )
        return self.public_url(object_name)

    def upload_bytes(self, data: bytes, object_name: str,
                     content_type: str = "application/octet-stream") -> str:
        self.client.put_object(
            self.bucket, object_name,
            io.BytesIO(data), length=len(data),
            content_type=content_type,
        )
        return self.public_url(object_name)

    def public_url(self, object_name: str) -> str:
        scheme = "https" if settings.minio_secure else "http"
        return f"{scheme}://{settings.minio_endpoint}/{self.bucket}/{object_name}"

    def download_to(self, object_name: str, dest: str | Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(self.bucket, object_name, str(dest))
        return dest


storage: Optional[Storage] = None


def get_storage() -> Storage:
    global storage
    if storage is None:
        storage = Storage()
    return storage
