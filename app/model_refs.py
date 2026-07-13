from fastapi import HTTPException, status


INVALID_MODEL_REFERENCE_DETAIL = "模型引用无效"
INVALID_ALIAS_NAME_DETAIL = "别名名称无效"


def cache_key(project_name: str, model_name: str) -> str:
    return f"{project_name}/{model_name}"


def validate_path_name(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("路径名称不能为空，且不能包含首尾空白")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("不允许使用路径分隔符或相对路径片段")
    return value


def split_cache_key(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or value.strip() != value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型必须使用 'project_name/model_name' 格式，且不能包含首尾空白",
        )
    parts = value.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型必须使用 'project_name/model_name' 格式",
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
