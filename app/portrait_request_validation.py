from __future__ import annotations

from fastapi import HTTPException, status


def validate_int_range(
    field_name: str,
    value: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须是整数") from exc
    if number < minimum:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须大于等于 {minimum}")
    if maximum is not None and number > maximum:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 必须介于 {minimum} 到 {maximum} 之间",
        )
    return number
