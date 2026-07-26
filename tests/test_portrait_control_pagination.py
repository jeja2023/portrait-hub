from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.portrait_pagination import filter_sort_dict_rows, page_items_cursor


def rows() -> list[dict[str, object]]:
    return [
        {"id": "a", "title": "Alpha event", "created_at": 10.0},
        {"id": "b", "title": "Beta event", "created_at": 30.0},
        {"id": "c", "title": "Alpha follow-up", "created_at": 20.0},
    ]


def test_control_rows_support_search_time_sort_and_cursor_paging() -> None:
    ordered = filter_sort_dict_rows(
        rows(),
        search="alpha",
        search_fields=["title"],
        created_since=5,
        created_until=25,
        time_field="created_at",
        sort_by="created_at",
        sort_order="desc",
        id_field="id",
    )

    first, first_page = page_items_cursor(ordered, limit=1)
    second, second_page = page_items_cursor(ordered, limit=1, cursor=first_page["next_cursor"])

    assert [item["id"] for item in first] == ["c"]
    assert [item["id"] for item in second] == ["a"]
    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert second_page["next_cursor"] is None


def test_control_row_pagination_rejects_invalid_cursor_and_time_window() -> None:
    with pytest.raises(HTTPException) as cursor_error:
        page_items_cursor(rows(), limit=1, cursor="WzFd")
    with pytest.raises(HTTPException) as range_error:
        filter_sort_dict_rows(
            rows(),
            created_since=30,
            created_until=10,
            time_field="created_at",
            sort_by="created_at",
            sort_order="asc",
            id_field="id",
        )

    assert cursor_error.value.status_code == 422
    assert range_error.value.status_code == 422
