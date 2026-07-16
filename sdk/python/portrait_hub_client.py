from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib import request as urllib_request

SDK_VERSION = "0.8.3"
USER_AGENT = f"portrait-hub-sdk-python/{SDK_VERSION}"


class PortraitHubHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: Any, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}
        super().__init__(f"PortraitHub 请求失败 with HTTP {status_code}: {detail}")


class PortraitHubClient:
    def __init__(
        self,
        base_url: str,
        api_token: str | None = None,
        auth_scheme: str = "bearer",
        timeout: float = 30.0,
        tenant_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.auth_scheme = self._normalize_auth_scheme(auth_scheme)
        self.timeout = timeout
        self.tenant_id = tenant_id

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
        headers = {"User-Agent": USER_AGENT, **(extra or {})}
        if self.tenant_id:
            headers["X-Tenant-ID"] = self.tenant_id
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

    def _request(self, req: urllib_request.Request) -> dict[str, Any]:
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as response:
                payload = self._decode_body(response.read())
        except HTTPError as exc:
            payload = self._decode_body(exc.read())
            raise PortraitHubHTTPError(exc.code, payload, dict(exc.headers.items())) from exc
        if not isinstance(payload, dict):
            raise PortraitHubHTTPError(502, payload, {})
        return payload

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=self._headers({"Content-Type": "application/json"}),
        )
        return self._request(req)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req = urllib_request.Request(
            f"{self.base_url}{self._path_with_query(path, params)}",
            method="GET",
            headers=self._headers(),
        )
        return self._request(req)

    def _multipart(
        self,
        path: str,
        fields: dict[str, Any] | None = None,
        files: list[tuple[str, str | Path]] | None = None,
    ) -> dict[str, Any]:
        boundary = f"portrait-hub-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for key, value in (fields or {}).items():
            field_name = self._multipart_header_value(key)
            chunks.append(f"--{boundary}\r\n".encode("utf-8"))
            chunks.append(f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode("utf-8"))
            chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")
        for field_name, path_value in files or []:
            path_obj = Path(path_value)
            content_type = mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream"
            safe_field_name = self._multipart_header_value(field_name)
            safe_filename = self._multipart_header_value(path_obj.name)
            chunks.append(f"--{boundary}\r\n".encode("utf-8"))
            chunks.append(
                (
                    f'Content-Disposition: form-data; name="{safe_field_name}"; '
                    f'filename="{safe_filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8")
            )
            chunks.append(path_obj.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers=self._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
        )
        return self._request(req)

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def compare_faces(self, image_a: str | Path, image_b: str | Path, threshold_profile: str = "normal") -> dict[str, Any]:
        return self._multipart(
            "/v1/compare/faces",
            fields={"threshold_profile": threshold_profile},
            files=[("image_a", image_a), ("image_b", image_b)],
        )

    def compare_persons(self, image_a: str | Path, image_b: str | Path, threshold_profile: str = "normal") -> dict[str, Any]:
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
            self._path_with_query("/v1/gallery/reindex", {"modality": modality, "model_id": model_id, "dry_run": dry_run}),
        )

    def create_video_job(
        self,
        video: str | Path,
        sample_interval_seconds: float | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        fields = {key: value for key, value in {"sample_interval_seconds": sample_interval_seconds, "batch_size": batch_size}.items() if value is not None}
        return self._multipart("/v1/jobs/video", fields=fields, files=[("file", video)])

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/jobs/{self._path_segment(job_id)}")

    def job_result(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/jobs/{self._path_segment(job_id)}/result")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/jobs/{self._path_segment(job_id)}/cancel")

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

    def list_streams(self, limit: int | None = None, offset: int | None = None, cursor: str | None = None) -> dict[str, Any]:
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
