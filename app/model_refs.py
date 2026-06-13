from fastapi import HTTPException, status


INVALID_MODEL_REFERENCE_DETAIL = "invalid model reference"
INVALID_ALIAS_NAME_DETAIL = "invalid alias name"


def cache_key(project_name: str, model_name: str) -> str:
    return f"{project_name}/{model_name}"


def validate_path_name(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("path names must not be empty or contain leading/trailing whitespace")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("path separators and relative path segments are not allowed")
    return value


def split_cache_key(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or value.strip() != value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model must use 'project_name/model_name' format without leading/trailing whitespace",
        )
    parts = value.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model must use 'project_name/model_name' format",
        )
    try:
        return validate_path_name(parts[0]), validate_path_name(parts[1])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_MODEL_REFERENCE_DETAIL,
        ) from exc


def validate_model_target(value: str) -> str:
    project, model = split_cache_key(value)
    return cache_key(project, model)


def validate_model_reference_parts(*values: str) -> tuple[str, ...]:
    try:
        return tuple(validate_path_name(value) for value in values)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_MODEL_REFERENCE_DETAIL,
        ) from exc


def validate_alias_name(value: str) -> str:
    try:
        return validate_path_name(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_ALIAS_NAME_DETAIL,
        ) from exc
