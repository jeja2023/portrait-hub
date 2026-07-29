from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import random
import time
import uuid
import warnings
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

SDK_VERSION = "0.18.4"
USER_AGENT = f"portrait-hub-sdk-python/{SDK_VERSION}"
DEFAULT_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_JOB_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "paused"})


class JobRecord(TypedDict, total=False):
    job_id: str
    tenant_id: str
    status: str
    progress: float
    terminal_reason: str | None
    created_at: float
    updated_at: float


class ResumableUploadResult(TypedDict, total=False):
    upload: dict[str, Any]
    job: JobRecord
    idempotent_replay: bool


class CompatibilityResult(TypedDict, total=False):
    compatible: bool
    sdk_version: str
    api_contract: str
    service_version: str
    minimum_sdk_version: str
    maximum_sdk_version_exclusive: str
    deprecations: list[dict[str, Any]]


class PortraitHubHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: Any, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}
        super().__init__(f"PortraitHub request failed with HTTP {status_code}: {detail}")


class PortraitHubAuthenticationError(PortraitHubHTTPError):
    pass


class PortraitHubPermissionError(PortraitHubHTTPError):
    pass


class PortraitHubValidationError(PortraitHubHTTPError):
    pass


class PortraitHubConflictError(PortraitHubHTTPError):
    pass


class PortraitHubRateLimitError(PortraitHubHTTPError):
    pass


class PortraitHubServerError(PortraitHubHTTPError):
    pass


class PortraitHubTransportError(RuntimeError):
    pass


class PortraitHubClient:
    def __init__(
        self,
        base_url: str,
        api_token: str | None = None,
        auth_scheme: str = "bearer",
        timeout: float = 30.0,
        tenant_id: str | None = None,
        project_id: str | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.auth_scheme = self._normalize_auth_scheme(auth_scheme)
        self.timeout = timeout
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.max_retries = max(0, min(10, int(max_retries)))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def _normalize_auth_scheme(self, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized not in {"bearer", "api_key"}:
            raise ValueError("auth_scheme 必须是 'bearer' 或 'api_key'")
        return normalized

    def _path_segment(self, value: str) -> str:
        return quote(str(value), safe="")

    def _multipart_header_value(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "X-Request-ID": f"sdk_{uuid.uuid4().hex}",
            "X-PortraitHub-SDK-Version": SDK_VERSION,
            **(extra or {}),
        }
        if self.tenant_id:
            headers["X-Tenant-ID"] = self.tenant_id
        if self.project_id:
            headers["X-Project-ID"] = self.project_id
        if self.api_token:
            if self.auth_scheme == "api_key":
                headers["X-API-Key"] = self.api_token
            else:
                headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _path_with_query(self, path: str, params: dict[str, Any] | None = None) -> str:
        clean_params: dict[str, Any] = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            clean_params[key] = str(value).lower() if isinstance(value, bool) else value
        if not clean_params:
            return path
        return f"{path}?{urlencode(clean_params, doseq=True)}"

    def _decode_body(self, body: bytes) -> Any:
        text = body.decode("utf-8") if body else ""
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _http_error(self, status_code: int, payload: Any, headers: dict[str, str]) -> PortraitHubHTTPError:
        error_type: type[PortraitHubHTTPError]
        if status_code == 401:
            error_type = PortraitHubAuthenticationError
        elif status_code == 403:
            error_type = PortraitHubPermissionError
        elif status_code == 409:
            error_type = PortraitHubConflictError
        elif status_code == 422:
            error_type = PortraitHubValidationError
        elif status_code == 429:
            error_type = PortraitHubRateLimitError
        elif status_code >= 500:
            error_type = PortraitHubServerError
        else:
            error_type = PortraitHubHTTPError
        return error_type(status_code, payload, headers)

    def _retry_delay(self, attempt: int, headers: dict[str, str] | None = None) -> float:
        retry_after = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
        if retry_after:
            try:
                return max(0.0, min(60.0, float(retry_after)))
            except ValueError:
                pass
        exponential = self.retry_backoff_seconds * (2**attempt)
        return float(exponential + random.uniform(0.0, exponential * 0.2))

    @staticmethod
    def _request_is_retryable(req: urllib_request.Request) -> bool:
        method = req.get_method().upper()
        headers = {key.lower(): value for key, value in req.header_items()}
        return method in {"GET", "HEAD", "OPTIONS", "DELETE"} or "idempotency-key" in headers

    def _request(self, req: urllib_request.Request) -> dict[str, Any]:
        retryable = self._request_is_retryable(req)
        for attempt in range(self.max_retries + 1):
            try:
                with urllib_request.urlopen(req, timeout=self.timeout) as response:
                    payload = self._decode_body(response.read())
                    response_headers = getattr(response, "headers", None)
                    if response_headers is not None:
                        self._warn_deprecation_headers(dict(response_headers.items()))
                break
            except HTTPError as exc:
                payload = self._decode_body(exc.read())
                headers = dict(exc.headers.items()) if exc.headers is not None else {}
                if retryable and attempt < self.max_retries and (exc.code in {408, 429} or exc.code >= 500):
                    time.sleep(self._retry_delay(attempt, headers))
                    continue
                raise self._http_error(exc.code, payload, headers) from exc
            except URLError as exc:
                if retryable and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt))
                    continue
                raise PortraitHubTransportError(f"PortraitHub transport failed: {type(exc.reason).__name__}") from exc
        if not isinstance(payload, dict):
            raise PortraitHubHTTPError(502, payload, {})
        return payload

    @staticmethod
    def _warn_deprecation_headers(headers: Mapping[str, str]) -> None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        if normalized.get("deprecation", "").lower() not in {"true", "1"}:
            return
        detail = normalized.get("sunset") or normalized.get("link") or "A called API capability is deprecated."
        warnings.warn(detail, DeprecationWarning, stacklevel=3)

    def _json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        extra_headers = {"Content-Type": "application/json"}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=self._headers(extra_headers),
        )
        return self._request(req)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req = urllib_request.Request(
            f"{self.base_url}{self._path_with_query(path, params)}",
            method="GET",
            headers=self._headers(),
        )
        return self._request(req)

    def _bytes(
        self,
        method: str,
        path: str,
        data: bytes,
        *,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        extra_headers = {"Content-Type": "application/octet-stream", **(headers or {})}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=self._headers(extra_headers),
        )
        return self._request(req)

    @staticmethod
    def _response_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _multipart(
        self,
        path: str,
        fields: dict[str, Any] | None = None,
        files: list[tuple[str, str | Path]] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        boundary = f"portrait-hub-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for key, value in (fields or {}).items():
            field_name = self._multipart_header_value(key)
            chunks.append(f"--{boundary}\r\n".encode())
            chunks.append(f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode())
            chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")
        for field_name, path_value in files or []:
            path_obj = Path(path_value)
            content_type = mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream"
            safe_field_name = self._multipart_header_value(field_name)
            safe_filename = self._multipart_header_value(path_obj.name)
            chunks.append(f"--{boundary}\r\n".encode())
            chunks.append(
                (
                    f'Content-Disposition: form-data; name="{safe_field_name}"; '
                    f'filename="{safe_filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode()
            )
            chunks.append(path_obj.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers=self._headers(headers),
        )
        return self._request(req)

    @staticmethod
    def _file_sha256(path: Path, *, block_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(block_size), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        numeric = str(value).split("+", 1)[0].split("-", 1)[0].split(".")
        if not numeric or any(not part.isdigit() for part in numeric[:3]):
            raise ValueError(f"invalid semantic version: {value}")
        padded = [*numeric, "0", "0", "0"][:3]
        return tuple(int(part) for part in padded)  # type: ignore[return-value]

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def api_metadata(self) -> dict[str, Any]:
        return self._get("/v1/meta")

    def check_compatibility(self, *, warn_deprecations: bool = True) -> CompatibilityResult:
        payload = self._response_data(self.api_metadata())
        supported = payload.get("supported_sdks", {})
        python_support = supported.get("python", {}) if isinstance(supported, dict) else {}
        minimum = str(python_support.get("minimum_version") or "0.0.0")
        maximum = str(python_support.get("maximum_version_exclusive") or "999999.0.0")
        compatible = self._version_tuple(minimum) <= self._version_tuple(SDK_VERSION) < self._version_tuple(maximum)
        deprecations = payload.get("deprecations", [])
        normalized_deprecations = [item for item in deprecations if isinstance(item, dict)] if isinstance(deprecations, list) else []
        if warn_deprecations:
            for item in normalized_deprecations:
                message = str(item.get("message") or item.get("capability") or "Deprecated API capability")
                warnings.warn(message, DeprecationWarning, stacklevel=2)
        result: CompatibilityResult = {
            "compatible": compatible,
            "sdk_version": SDK_VERSION,
            "api_contract": str(payload.get("api_contract") or "unknown"),
            "service_version": str(payload.get("service_version") or "unknown"),
            "minimum_sdk_version": minimum,
            "maximum_sdk_version_exclusive": maximum,
            "deprecations": normalized_deprecations,
        }
        if not compatible:
            raise RuntimeError(
                f"Python SDK {SDK_VERSION} is outside the supported range [{minimum}, {maximum})"
            )
        return result

    @staticmethod
    def verify_webhook_signature(
        payload: bytes | str,
        signature: str,
        secret: str,
        *,
        timestamp: int | str | None = None,
        tolerance_seconds: int = 300,
        now: float | None = None,
    ) -> bool:
        if not secret or tolerance_seconds < 0:
            return False
        body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        signed_payload = body
        if timestamp is not None:
            try:
                timestamp_value = int(timestamp)
            except (TypeError, ValueError):
                return False
            current = time.time() if now is None else float(now)
            if abs(current - timestamp_value) > tolerance_seconds:
                return False
            signed_payload = str(timestamp_value).encode("ascii") + b"." + body
        provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided.strip().lower())

    def compare_faces(
        self, image_a: str | Path, image_b: str | Path, threshold_profile: str = "normal"
    ) -> dict[str, Any]:
        return self._multipart(
            "/v1/compare/faces",
            fields={"threshold_profile": threshold_profile},
            files=[("image_a", image_a), ("image_b", image_b)],
        )

    def compare_persons(
        self, image_a: str | Path, image_b: str | Path, threshold_profile: str = "normal"
    ) -> dict[str, Any]:
        return self._multipart(
            "/v1/compare/persons",
            fields={"threshold_profile": threshold_profile},
            files=[("image_a", image_a), ("image_b", image_b)],
        )

    def enroll(self, person_id: str, images: list[str | Path], modality: str = "body") -> dict[str, Any]:
        return self._multipart(
            "/v1/gallery/enroll",
            fields={"person_id": person_id, "modality": modality},
            files=[("files", image) for image in images],
        )

    def search(
        self,
        image: str | Path,
        modality: str = "body",
        top_k: int = 5,
        threshold_profile: str = "normal",
    ) -> dict[str, Any]:
        return self._multipart(
            "/v1/gallery/search",
            fields={"modality": modality, "top_k": top_k, "threshold_profile": threshold_profile},
            files=[("file", image)],
        )

    def search_batch(
        self,
        images: list[str | Path],
        modality: str = "body",
        top_k: int = 5,
        threshold_profile: str = "normal",
        async_mode: bool = False,
    ) -> dict[str, Any]:
        return self._multipart(
            "/v1/gallery/search/batch",
            fields={
                "modality": modality,
                "top_k": top_k,
                "threshold_profile": threshold_profile,
                "async_mode": async_mode,
            },
            files=[("files", image) for image in images],
        )

    def compare_batch(
        self,
        image_a: list[str | Path],
        image_b: list[str | Path],
        modality: str = "body",
        threshold_profile: str = "normal",
        include_vectors: bool = False,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        return self._multipart(
            "/v1/compare/batch",
            fields={
                "modality": modality,
                "threshold_profile": threshold_profile,
                "include_vectors": include_vectors,
                "async_mode": async_mode,
            },
            files=[("image_a", image) for image in image_a] + [("image_b", image) for image in image_b],
        )

    def reindex_gallery(
        self,
        modality: str | None = None,
        model_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            self._path_with_query(
                "/v1/gallery/reindex", {"modality": modality, "model_id": model_id, "dry_run": dry_run}
            ),
        )

    def create_video_job(
        self,
        video: str | Path,
        sample_interval_seconds: float | None = None,
        batch_size: int | None = None,
        priority: int = 0,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fields = {
            key: value
            for key, value in {
                "sample_interval_seconds": sample_interval_seconds,
                "batch_size": batch_size,
                "priority": priority,
            }.items()
            if value is not None
        }
        return self._multipart(
            "/v1/jobs/video",
            fields=fields,
            files=[("file", video)],
            idempotency_key=idempotency_key,
        )

    def upload_video_resumable(
        self,
        video: str | Path,
        *,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
        upload_id: str | None = None,
        idempotency_key: str | None = None,
        sample_interval_seconds: float | None = None,
        batch_size: int | None = None,
        priority: int = 0,
        include_embeddings: bool = False,
    ) -> ResumableUploadResult:
        path = Path(video)
        if not path.is_file():
            raise FileNotFoundError(path)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        total_bytes = path.stat().st_size
        digest = self._file_sha256(path)
        stable_key = idempotency_key or f"video-upload-{digest}"
        if upload_id is None:
            created = self._response_data(
                self._json(
                    "POST",
                    "/v1/uploads/video",
                    {
                        "filename": path.name,
                        "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        "total_bytes": total_bytes,
                        "sha256": digest,
                    },
                    idempotency_key=stable_key,
                )
            )
            upload = created.get("upload")
        else:
            current = self._response_data(self._get(f"/v1/uploads/video/{self._path_segment(upload_id)}"))
            upload = current.get("upload")
        if not isinstance(upload, dict):
            raise PortraitHubHTTPError(502, "video upload response is missing upload data", {})
        if int(upload.get("total_bytes") or 0) != total_bytes or str(upload.get("sha256") or "") != digest:
            raise PortraitHubConflictError(409, "local video does not match the upload session", {})
        upload_id = str(upload["upload_id"])
        existing_parts = {
            (int(item.get("offset") or 0), int(item.get("size") or 0)): str(item.get("sha256") or "")
            for item in upload.get("parts", [])
            if isinstance(item, dict)
        }
        with path.open("rb") as file:
            offset = 0
            part_number = 1
            while offset < total_bytes:
                chunk = file.read(min(chunk_size, total_bytes - offset))
                chunk_digest = hashlib.sha256(chunk).hexdigest()
                if existing_parts.get((offset, len(chunk))) != chunk_digest:
                    response = self._bytes(
                        "PUT",
                        f"/v1/uploads/video/{self._path_segment(upload_id)}/parts/{part_number}",
                        chunk,
                        headers={
                            "X-Chunk-Offset": str(offset),
                            "X-Chunk-SHA256": chunk_digest,
                        },
                        idempotency_key=f"{stable_key}-part-{part_number}",
                    )
                    response_data = self._response_data(response)
                    next_upload = response_data.get("upload")
                    if isinstance(next_upload, dict):
                        upload = next_upload
                offset += len(chunk)
                part_number += 1
        completion_payload: dict[str, Any] = {
            "priority": priority,
            "include_embeddings": include_embeddings,
        }
        if sample_interval_seconds is not None:
            completion_payload["sample_interval_seconds"] = sample_interval_seconds
        if batch_size is not None:
            completion_payload["batch_size"] = batch_size
        completed = self._response_data(
            self._json(
                "POST",
                f"/v1/uploads/video/{self._path_segment(upload_id)}/complete",
                completion_payload,
                idempotency_key=f"{stable_key}-complete",
            )
        )
        return ResumableUploadResult(
            upload=completed.get("upload", upload),
            job=completed.get("job", {}),
            idempotent_replay=bool(completed.get("idempotent_replay", False)),
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/jobs/{self._path_segment(job_id)}")

    def list_jobs(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        cursor: str | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/v1/jobs",
            {"limit": limit, "offset": offset, "cursor": cursor, "status": status, "kind": kind},
        )

    def iter_jobs(
        self,
        *,
        page_size: int = 100,
        status: str | None = None,
        kind: str | None = None,
    ) -> Iterator[JobRecord]:
        if page_size < 1 or page_size > 200:
            raise ValueError("page_size must be between 1 and 200")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            data = self._response_data(
                self.list_jobs(limit=page_size, cursor=cursor, status=status, kind=kind)
            )
            items = data.get("items") or data.get("jobs") or []
            if not isinstance(items, list):
                raise PortraitHubHTTPError(502, "jobs response has invalid items", {})
            for item in items:
                if isinstance(item, dict):
                    yield cast(JobRecord, dict(item))
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise PortraitHubHTTPError(502, "jobs pagination cursor repeated", {})
            seen_cursors.add(cursor)

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        terminal_statuses: set[str] | frozenset[str] = DEFAULT_JOB_TERMINAL_STATUSES,
    ) -> JobRecord:
        if timeout <= 0 or poll_interval < 0:
            raise ValueError("timeout must be positive and poll_interval cannot be negative")
        deadline = time.monotonic() + timeout
        while True:
            data = self._response_data(self.get_job(job_id))
            job = data.get("job")
            if not isinstance(job, dict):
                raise PortraitHubHTTPError(502, "job response is missing job data", {})
            if str(job.get("status")) in terminal_statuses:
                return cast(JobRecord, dict(job))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"job {job_id} did not reach a terminal status within {timeout} seconds")
            time.sleep(min(poll_interval, remaining))

    def job_result(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/jobs/{self._path_segment(job_id)}/result")

    def cancel_job(self, job_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/v1/jobs/{self._path_segment(job_id)}/cancel",
            idempotency_key=idempotency_key,
        )

    def create_stream(
        self,
        stream_url: str,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            "/v1/streams",
            {
                "stream_url": stream_url,
                "name": name,
                "settings": settings or {},
                "metadata": metadata or {},
            },
        )

    def list_streams(
        self, limit: int | None = None, offset: int | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        return self._get("/v1/streams", {"limit": limit, "offset": offset, "cursor": cursor})

    def get_stream(self, stream_id: str) -> dict[str, Any]:
        return self._get(f"/v1/streams/{self._path_segment(stream_id)}")

    def start_stream(self, stream_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/streams/{self._path_segment(stream_id)}/start")

    def stop_stream(self, stream_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/streams/{self._path_segment(stream_id)}/stop")

    def stream_status(self, stream_id: str) -> dict[str, Any]:
        return self._get(f"/v1/streams/{self._path_segment(stream_id)}/status")

    def stream_events(
        self,
        stream_id: str,
        limit: int | None = None,
        offset: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self._get(
            f"/v1/streams/{self._path_segment(stream_id)}/events",
            {"limit": limit, "offset": offset, "cursor": cursor},
        )

    def models(self) -> dict[str, Any]:
        return self._get("/v1/models")

    def get_model(self, model_id: str) -> dict[str, Any]:
        return self._get(f"/v1/models/{self._path_segment(model_id)}")

    def load_model(self, model_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/models/{self._path_segment(model_id)}/load")

    def unload_model(self, model_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/models/{self._path_segment(model_id)}/unload")

    def thresholds(self) -> dict[str, Any]:
        return self._get("/v1/thresholds")

    def update_thresholds(self, profile: str, thresholds: dict[str, float]) -> dict[str, Any]:
        return self._json("PUT", f"/v1/thresholds/{self._path_segment(profile)}", thresholds)

    def admin_status(self) -> dict[str, Any]:
        return self._get("/v1/admin/status")
