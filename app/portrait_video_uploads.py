import hashlib
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import HTTPException, status

from app.observability import wall_time
from app.portrait_state import read_json_state, write_json_state
from app.settings import (
    MAX_VIDEO_BYTES,
    VIDEO_UPLOAD_PART_DIR,
    VIDEO_UPLOAD_SESSION_STATE_PATH,
    VIDEO_UPLOAD_SESSION_TTL_SECONDS,
)
from app.video_io import resolve_video_job_input, validate_video_content, validate_video_filename

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"version": 1, "sessions": []}


def reset_video_upload_state() -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update({"version": 1, "sessions": []})


def load_video_upload_state() -> None:
    payload = read_json_state(VIDEO_UPLOAD_SESSION_STATE_PATH, {"version": 1, "sessions": []})
    sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
    with _LOCK:
        _STATE.clear()
        _STATE.update(
            {
                "version": 1,
                "sessions": [item for item in sessions if isinstance(item, dict)],
            }
        )


def save_video_upload_state() -> None:
    write_json_state(VIDEO_UPLOAD_SESSION_STATE_PATH, deepcopy(_STATE))


def public_upload_session(session: dict[str, Any]) -> dict[str, Any]:
    parts = sorted(session.get("parts", []), key=lambda item: int(item["offset"]))
    uploaded_bytes = sum(int(item["size"]) for item in parts)
    return {
        "upload_id": session["upload_id"],
        "filename": session["filename"],
        "content_type": session["content_type"],
        "total_bytes": session["total_bytes"],
        "uploaded_bytes": uploaded_bytes,
        "remaining_bytes": max(0, int(session["total_bytes"]) - uploaded_bytes),
        "sha256": session["sha256"],
        "status": session["status"],
        "job_id": session.get("job_id"),
        "part_count": len(parts),
        "parts": [
            {
                "part_number": item["part_number"],
                "offset": item["offset"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for item in parts
        ],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "expires_at": session["expires_at"],
    }


def _session_dir(upload_id: str) -> Path:
    target = (VIDEO_UPLOAD_PART_DIR / upload_id).resolve()
    root = VIDEO_UPLOAD_PART_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("upload session path escaped its root") from exc
    return target


def _part_path(upload_id: str, part_number: int) -> Path:
    return _session_dir(upload_id) / f"part-{part_number:08d}.bin"


def _remove_session_parts(upload_id: str) -> None:
    directory = _session_dir(upload_id)
    if not directory.is_dir():
        return
    for path in directory.glob("part-*.bin"):
        path.unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        # Unexpected files are preserved for operator inspection.
        return


def purge_expired_upload_sessions(*, current_time: float | None = None) -> int:
    timestamp = wall_time() if current_time is None else float(current_time)
    with _LOCK:
        expired = [
            session
            for session in _STATE["sessions"]
            if session.get("status") in {"uploading", "aborted"}
            and float(session.get("expires_at") or 0) <= timestamp
        ]
        if not expired:
            return 0
        expired_ids = {str(session["upload_id"]) for session in expired}
        _STATE["sessions"] = [
            session for session in _STATE["sessions"] if str(session.get("upload_id")) not in expired_ids
        ]
        save_video_upload_state()
    for upload_id in expired_ids:
        _remove_session_parts(upload_id)
    return len(expired_ids)


def require_upload_session(upload_id: str, tenant_id: str) -> dict[str, Any]:
    for session in _STATE["sessions"]:
        if session.get("upload_id") == upload_id and session.get("tenant_id") == tenant_id:
            return cast(dict[str, Any], session)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video upload session was not found")


def create_upload_session(
    tenant_id: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    filename = str(payload.get("filename") or "").strip()
    validate_video_filename(filename)
    total_bytes = int(payload.get("total_bytes") or 0)
    if total_bytes <= 0 or total_bytes > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="video size is invalid")
    digest = str(payload.get("sha256") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="video sha256 is invalid")
    normalized_key = str(idempotency_key or "").strip()[:256] or None
    purge_expired_upload_sessions()
    with _LOCK:
        if normalized_key:
            for existing in _STATE["sessions"]:
                if existing.get("tenant_id") != tenant_id or existing.get("idempotency_key") != normalized_key:
                    continue
                if (
                    existing.get("filename") != filename
                    or int(existing.get("total_bytes") or 0) != total_bytes
                    or existing.get("sha256") != digest
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="idempotency key was already used with a different video upload",
                    )
                return {**public_upload_session(existing), "idempotent_replay": True}
    timestamp = wall_time()
    session = {
        "upload_id": f"upl_{uuid4().hex[:20]}",
        "tenant_id": tenant_id,
        "filename": filename,
        "content_type": str(payload.get("content_type") or "application/octet-stream")[:256],
        "total_bytes": total_bytes,
        "sha256": digest,
        "status": "uploading",
        "parts": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "expires_at": timestamp + VIDEO_UPLOAD_SESSION_TTL_SECONDS,
        "idempotency_key": normalized_key,
    }
    upload_id = str(session["upload_id"])
    directory = _session_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=False)
    with _LOCK:
        _STATE["sessions"].append(session)
        try:
            save_video_upload_state()
        except Exception:
            _STATE["sessions"].remove(session)
            _remove_session_parts(str(session["upload_id"]))
            raise
    return {**public_upload_session(session), "idempotent_replay": False}


def get_upload_session(upload_id: str, tenant_id: str) -> dict[str, Any]:
    purge_expired_upload_sessions()
    with _LOCK:
        return public_upload_session(require_upload_session(upload_id, tenant_id))


def put_upload_part(
    upload_id: str,
    tenant_id: str,
    part_number: int,
    offset: int,
    data: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    if part_number < 1 or offset < 0 or not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="video upload part is invalid")
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256.strip().lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload part digest mismatch")
    purge_expired_upload_sessions()
    with _LOCK:
        session = require_upload_session(upload_id, tenant_id)
        if session["status"] != "uploading":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload is not writable")
        end = offset + len(data)
        if end > int(session["total_bytes"]):
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, detail="video upload part exceeds total size")
        for existing in session["parts"]:
            if int(existing["part_number"]) == part_number:
                if (
                    int(existing["offset"]) == offset
                    and int(existing["size"]) == len(data)
                    and existing["sha256"] == digest
                ):
                    return {**public_upload_session(session), "idempotent_replay": True}
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload part number conflicts")
            existing_start = int(existing["offset"])
            existing_end = existing_start + int(existing["size"])
            if offset < existing_end and end > existing_start:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload parts overlap")

        target = _part_path(upload_id, part_number)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        session["parts"].append(
            {
                "part_number": part_number,
                "offset": offset,
                "size": len(data),
                "sha256": digest,
            }
        )
        session["updated_at"] = wall_time()
        session["expires_at"] = session["updated_at"] + VIDEO_UPLOAD_SESSION_TTL_SECONDS
        save_video_upload_state()
        return {**public_upload_session(session), "idempotent_replay": False}


def complete_upload_session(upload_id: str, tenant_id: str, job_id: str) -> tuple[str, dict[str, Any]]:
    purge_expired_upload_sessions()
    with _LOCK:
        session = require_upload_session(upload_id, tenant_id)
        if session["status"] == "completed" and session.get("input_ref"):
            return str(session["input_ref"]), public_upload_session(session)
        parts = sorted(session["parts"], key=lambda item: int(item["offset"]))
        expected_offset = 0
        for part in parts:
            if int(part["offset"]) != expected_offset:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload has missing ranges")
            expected_offset += int(part["size"])
        if expected_offset != int(session["total_bytes"]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload is incomplete")

        suffix = validate_video_filename(session["filename"]) or ".video"
        tenant_segment = hashlib.sha256(str(tenant_id).encode("utf-8")).hexdigest()[:24]
        input_ref = f"{tenant_segment}/{job_id}{suffix}"
        target = resolve_video_job_input(input_ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
        digest = hashlib.sha256()
        prefix = bytearray()
        try:
            with temporary.open("xb") as output:
                for part in parts:
                    part_path = _part_path(upload_id, int(part["part_number"]))
                    with part_path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            if len(prefix) < 64:
                                prefix.extend(chunk[: 64 - len(prefix)])
                            output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != session["sha256"]:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video upload digest mismatch")
            validate_video_content(bytes(prefix), session["filename"])
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        session["status"] = "completed"
        session["input_ref"] = input_ref
        session["job_id"] = job_id
        session["updated_at"] = wall_time()
        save_video_upload_state()
        return input_ref, public_upload_session(session)


def abort_upload_session(upload_id: str, tenant_id: str) -> bool:
    with _LOCK:
        session = require_upload_session(upload_id, tenant_id)
        session["status"] = "aborted"
        session["updated_at"] = wall_time()
        save_video_upload_state()
    _remove_session_parts(upload_id)
    return True


def reopen_completed_upload(upload_id: str, tenant_id: str, job_id: str) -> None:
    with _LOCK:
        session = require_upload_session(upload_id, tenant_id)
        if session.get("job_id") != job_id:
            return
        session["status"] = "uploading"
        session.pop("input_ref", None)
        session.pop("job_id", None)
        session["updated_at"] = wall_time()
        save_video_upload_state()


load_video_upload_state()


__all__ = [
    "abort_upload_session",
    "complete_upload_session",
    "create_upload_session",
    "get_upload_session",
    "load_video_upload_state",
    "purge_expired_upload_sessions",
    "put_upload_part",
    "reopen_completed_upload",
    "reset_video_upload_state",
]
