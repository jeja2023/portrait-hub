import pytest
from fastapi import HTTPException

from app.model_refs import (
    INVALID_ALIAS_NAME_DETAIL,
    INVALID_MODEL_REFERENCE_DETAIL,
    cache_key,
    split_cache_key,
    validate_alias_name,
    validate_model_reference_parts,
    validate_path_name,
)


def test_validate_path_name_rejects_path_segments() -> None:
    for value in ["", " name", "name ", "..", ".", "../model.onnx", "nested/model.onnx", "nested\\model.onnx"]:
        with pytest.raises(ValueError):
            validate_path_name(value)


def test_split_cache_key_validates_format() -> None:
    assert split_cache_key("project/model.onnx") == ("project", "model.onnx")
    assert cache_key("project", "model.onnx") == "project/model.onnx"

    with pytest.raises(HTTPException) as nested:
        split_cache_key("project/nested/model.onnx")
    assert nested.value.detail == INVALID_MODEL_REFERENCE_DETAIL

    for value in [" project/model.onnx", "project/model.onnx "]:
        with pytest.raises(HTTPException) as exc_info:
            split_cache_key(value)
        assert exc_info.value.detail == "模型必须使用 'project_name/model_name' 格式，且不能包含首尾空白"


def test_public_model_reference_validation_uses_fixed_error_detail() -> None:
    secret_project = "secret/project"
    secret_alias = "secret/alias"

    with pytest.raises(HTTPException) as model_ref:
        validate_model_reference_parts(secret_project, "secret-model.onnx")
    with pytest.raises(HTTPException) as alias_ref:
        validate_alias_name(secret_alias)

    assert model_ref.value.status_code == 400
    assert model_ref.value.detail == INVALID_MODEL_REFERENCE_DETAIL
    assert "secret" not in str(model_ref.value.detail)
    assert alias_ref.value.status_code == 400
    assert alias_ref.value.detail == INVALID_ALIAS_NAME_DETAIL
    assert "secret" not in str(alias_ref.value.detail)
