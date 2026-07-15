import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import STATE_READ_FAIL_CLOSED, STATE_WRITE_FAIL_CLOSED

ATOMIC_STATE_WRITE_DISABLED = False


def state_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def read_json_state(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:
        logger.warning(
            "读取状态文件失败: path_hash=%s error=%s",
            state_path_fingerprint(path),
            exception_log_summary(exc),
        )
        if STATE_READ_FAIL_CLOSED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="状态读取失败",
            ) from exc
        return default


def handle_state_read_error(message: str) -> None:
    logger.warning(message)
    if STATE_READ_FAIL_CLOSED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="状态读取失败",
        )


def handle_state_write_error(path: Path, exc: Exception) -> None:
    logger.warning(
        "写入状态文件失败: path_hash=%s error=%s",
        state_path_fingerprint(path),
        exception_log_summary(exc),
    )
    if STATE_WRITE_FAIL_CLOSED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="状态写入失败",
        ) from exc


def write_json_state(path: Path, payload: Any) -> None:
    global ATOMIC_STATE_WRITE_DISABLED
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if ATOMIC_STATE_WRITE_DISABLED:
            with path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, sort_keys=True)
                file.write("\n")
            return

        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
        try:
            os.replace(temp_path, path)
        except OSError as replace_exc:
            logger.warning(
                "原子状态替换失败: path_hash=%s error=%s；回退到直接写入",
                state_path_fingerprint(path),
                exception_log_summary(replace_exc),
            )
            ATOMIC_STATE_WRITE_DISABLED = True
            with path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, sort_keys=True)
                file.write("\n")
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception as exc:
        handle_state_write_error(path, exc)


def append_jsonl(path: Path, payload: dict[str, Any], *, fail_closed: bool = False) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
    except Exception as exc:
        logger.warning(
            "追加审计文件失败: path_hash=%s error=%s",
            state_path_fingerprint(path),
            exception_log_summary(exc),
        )
        if fail_closed:
            handle_state_write_error(path, exc)
