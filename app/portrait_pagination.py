from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import HTTPException, status

from app.settings import (
    API_LIST_DEFAULT_LIMIT,
    MAX_API_LIST_LIMIT,
    MAX_STREAM_EVENT_LIST_LIMIT,
    STREAM_EVENT_LIST_DEFAULT_LIMIT,
)


T = TypeVar("T")


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int
    cursor: str | None = None


def bounded_limit(value: int | None, *, default: int, max_limit: int, field_name: str = "limit") -> int:
    raw = default if value is None else value
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must be an integer") from exc
    if limit < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must be >= 0")
    if limit > max_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be <= {max_limit}",
        )
    return limit


def bounded_offset(value: int | None, *, field_name: str = "offset") -> int:
    raw = 0 if value is None else value
    try:
        offset = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must be an integer") from exc
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must be >= 0")
    return offset


def encode_cursor(values: list[Any]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> list[Any] | None:
    if value is None or value == "":
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cursor is invalid") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cursor is invalid")
    return payload


def normalize_list_pagination(limit: int | None, offset: int | None, cursor: str | None = None) -> Pagination:
    return Pagination(
        limit=bounded_limit(limit, default=API_LIST_DEFAULT_LIMIT, max_limit=MAX_API_LIST_LIMIT),
        offset=bounded_offset(offset),
        cursor=cursor,
    )


def normalize_stream_event_pagination(limit: int | None, offset: int | None, cursor: str | None = None) -> Pagination:
    return Pagination(
        limit=bounded_limit(
            limit,
            default=STREAM_EVENT_LIST_DEFAULT_LIMIT,
            max_limit=MAX_STREAM_EVENT_LIST_LIMIT,
            field_name="event_limit" if limit is not None else "limit",
        ),
        offset=bounded_offset(offset),
        cursor=cursor,
    )


def page_items(items: Sequence[T], *, limit: int, offset: int) -> tuple[list[T], dict[str, Any]]:
    total = len(items)
    page = list(items[offset : offset + limit])
    next_offset = offset + len(page) if offset + len(page) < total else None
    return page, {
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "cursor": None,
        "next_cursor": None,
        "has_more": next_offset is not None,
    }


def page_items_keyset(
    items: Sequence[T],
    *,
    limit: int,
    offset: int = 0,
    cursor: str | None = None,
    key_fields: list[str],
) -> tuple[list[T], dict[str, Any]]:
    def item_values(item: T) -> list[Any]:
        if isinstance(item, dict):
            return [item.get(field) for field in key_fields]
        return [getattr(item, field) for field in key_fields]

    decoded = decode_cursor(cursor)
    start_index = bounded_offset(offset)
    if decoded is not None:
        for index, item in enumerate(items):
            item_key = item_values(item)
            if item_key > decoded:
                start_index = index
                break
        else:
            start_index = len(items)
    page, metadata = page_items(items, limit=limit, offset=start_index)
    next_cursor = None
    if metadata["next_offset"] is not None and page:
        last_item = page[-1]
        next_cursor = encode_cursor(item_values(last_item))
    metadata["cursor"] = cursor
    metadata["next_cursor"] = next_cursor
    metadata["has_more"] = next_cursor is not None
    return page, metadata
