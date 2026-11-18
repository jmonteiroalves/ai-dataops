"""Cloud Storage adapter for the serving snapshot."""

from __future__ import annotations

import importlib
from typing import Any

from ai_dataops.errors import ProviderError


def _client() -> Any:
    try:
        storage = importlib.import_module("google.cloud.storage")
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ProviderError("google-cloud-storage is not installed") from exc
    return storage.Client()


def upload_text(bucket: str, object_name: str, text: str) -> str:
    try:
        client = _client()
        blob = client.bucket(bucket).blob(object_name)
        blob.upload_from_string(text, content_type="application/json")
    except Exception as exc:
        raise ProviderError(f"snapshot upload failed: {type(exc).__name__}") from exc
    return f"gs://{bucket}/{object_name}"


def download_text(uri: str) -> str:
    if not uri.startswith("gs://"):
        raise ValueError(f"not a GCS URI: {uri}")
    bucket_name, _, object_name = uri[5:].partition("/")
    if not bucket_name or not object_name:
        raise ValueError(f"invalid GCS URI: {uri}")
    try:
        client = _client()
        payload = client.bucket(bucket_name).blob(object_name).download_as_text()
    except Exception as exc:
        raise ProviderError(f"snapshot download failed: {type(exc).__name__}") from exc
    return str(payload)


__all__ = ["download_text", "upload_text"]
