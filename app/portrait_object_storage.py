import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from app.observability import logger
from app.portrait_crypto import encrypt_bytes
from app.portrait_response import OBJECT_DELETE_FAILED, exception_log_summary
from app.settings import (
    OBJECT_STORAGE_DIR,
    PORTRAIT_OBJECT_STORAGE_BACKEND,
    PORTRAIT_STORAGE_BACKEND,
    S3_REGION,
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_SECRET_ACCESS_KEY,
)

try:  # pragma: no cover - optional production dependency
    import boto3
except Exception:  # pragma: no cover - exercised when dependency is absent
    boto3 = None


class ObjectStore(Protocol):
    backend_name: str

    def put_bytes(self, tenant_id: str, object_type: str, filename: str | None, data: bytes) -> dict[str, Any]:
        ...

    def delete_object(self, info: dict[str, Any]) -> dict[str, Any]:
        ...

    def health(self) -> dict[str, Any]:
        ...


def object_key_segment(value: str) -> str:
    cleaned = str(value).replace("..", "_")
    encoded = quote(cleaned, safe="._-")
    return encoded or "_"


def object_key_for(tenant_id: str, object_type: str, filename: str | None, digest: str) -> str:
    suffix = Path(filename or "").suffix
    suffix_segment = object_key_segment(suffix) if suffix else ""
    return (
        f"{object_key_segment(tenant_id)}/"
        f"{object_key_segment(object_type)}/"
        f"{digest[:2]}/"
        f"{digest}{suffix_segment}.json"
    )


def local_object_path(object_key: str) -> Path:
    target = (OBJECT_STORAGE_DIR / object_key).resolve()
    root = OBJECT_STORAGE_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("object key escapes object storage directory") from exc
    return target


def object_key_fingerprint(object_key: str) -> str:
    return hashlib.sha256(object_key.encode("utf-8")).hexdigest()[:16]


def object_record_metadata(object_type: str, filename: str | None) -> dict[str, Any]:
    return {"object_type": object_type, "filename_provided": bool(filename)}


def public_object_info(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": str(info.get("backend") or "unknown"),
        "stored": bool(info),
        "encrypted": bool(info.get("encrypted", False)),
    }


def write_local_object_payload(target: Path, payload: dict[str, Any]) -> None:
    def dump(path: Path) -> None:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")

    temp_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        dump(temp_path)
        try:
            os.replace(temp_path, target)
        except OSError:
            dump(target)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class LocalObjectStore:
    backend_name = "local_file"

    def _record_object(self, tenant_id: str, info: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
        if PORTRAIT_STORAGE_BACKEND != "postgres":
            return
        try:
            from app.portrait_postgres import insert_object_record

            insert_object_record(tenant_id, info, metadata)
        except Exception:
            return

    def put_bytes(self, tenant_id: str, object_type: str, filename: str | None, data: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        object_key = object_key_for(tenant_id, object_type, filename, digest)
        target = local_object_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = encrypt_bytes(data)
        write_local_object_payload(target, payload)
        info = {
            "backend": self.backend_name,
            "object_key": object_key,
            "sha256": digest,
            "bytes": len(data),
            "encrypted": payload.get("encrypted", False),
        }
        self._record_object(tenant_id, info, object_record_metadata(object_type, filename))
        return info

    def delete_object(self, info: dict[str, Any]) -> dict[str, Any]:
        object_key = str(info.get("object_key") or "")
        if not object_key:
            return {"backend": self.backend_name, "deleted": False, "reason": "missing object_key"}
        try:
            target = local_object_path(object_key)
            target.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(
                "local object delete failed: key_hash=%s error=%s",
                object_key_fingerprint(object_key),
                exception_log_summary(exc),
            )
            return {
                "backend": self.backend_name,
                "deleted": False,
                "reason": OBJECT_DELETE_FAILED,
            }
        return {"backend": self.backend_name, "deleted": True}

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "storage_dir_configured": bool(OBJECT_STORAGE_DIR),
            "status": "ready",
        }


class S3ObjectStore(LocalObjectStore):
    backend_name = "s3"

    def _client(self) -> Any:
        if boto3 is None:
            raise RuntimeError("boto3 is not installed; install requirements-prod-optional.txt")
        if not S3_BUCKET:
            raise RuntimeError("S3_BUCKET is not configured")
        kwargs: dict[str, Any] = {"region_name": S3_REGION or None}
        if S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = S3_ENDPOINT_URL
        if S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = S3_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
        return boto3.client("s3", **kwargs)

    def put_bytes(self, tenant_id: str, object_type: str, filename: str | None, data: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        object_key = object_key_for(tenant_id, object_type, filename, digest)
        payload = encrypt_bytes(data)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        client = self._client()
        client.put_object(
            Bucket=S3_BUCKET,
            Key=object_key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            Metadata={
                "sha256": digest,
                "tenant-id": tenant_id,
                "object-type": object_type,
                "encrypted": str(bool(payload.get("encrypted", False))).lower(),
            },
        )
        info = {
            "backend": self.backend_name,
            "object_key": object_key,
            "bucket": S3_BUCKET,
            "sha256": digest,
            "bytes": len(data),
            "encrypted": payload.get("encrypted", False),
        }
        self._record_object(tenant_id, info, object_record_metadata(object_type, filename))
        return info

    def delete_object(self, info: dict[str, Any]) -> dict[str, Any]:
        object_key = str(info.get("object_key") or "")
        if not object_key:
            return {"backend": self.backend_name, "deleted": False, "reason": "missing object_key"}
        try:
            self._client().delete_object(Bucket=S3_BUCKET, Key=object_key)
        except Exception as exc:
            logger.warning(
                "s3 object delete failed: key_hash=%s error=%s",
                object_key_fingerprint(object_key),
                exception_log_summary(exc),
            )
            return {"backend": self.backend_name, "deleted": False, "reason": OBJECT_DELETE_FAILED}
        return {"backend": self.backend_name, "deleted": True}

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "configured": bool(S3_BUCKET),
            "driver_available": boto3 is not None,
            "bucket_configured": bool(S3_BUCKET),
            "endpoint_configured": bool(S3_ENDPOINT_URL),
            "region_configured": bool(S3_REGION),
            "status": "ready" if S3_BUCKET and boto3 is not None else "not_ready",
        }


def configured_object_store() -> ObjectStore:
    if PORTRAIT_OBJECT_STORAGE_BACKEND == "s3":
        return S3ObjectStore()
    return LocalObjectStore()


OBJECT_STORE = configured_object_store()
