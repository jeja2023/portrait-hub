from __future__ import annotations

from typing import Any

from app.server import app

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ERROR_STATUSES = {"400", "401", "403", "404", "409", "413", "422", "429", "500", "503"}


def v1_operations(schema: dict[str, Any]):
    for path, path_item in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_every_v1_json_operation_has_versioned_success_and_error_contracts() -> None:
    schema = app.openapi()
    operation_count = 0
    raw_response_count = 0

    for path, method, operation in v1_operations(schema):
        operation_count += 1
        success = operation["responses"].get("200") or operation["responses"].get("201")
        assert success is not None, f"{method.upper()} {path} has no success response"
        json_schema = success.get("content", {}).get("application/json", {}).get("schema")
        if not json_schema:
            raw_response_count += 1
            assert path == "/v1/analysis/artifacts/{archive_id}/{artifact_id}"
            continue
        assert json_schema, f"{method.upper()} {path} has an empty JSON success schema"
        assert ERROR_STATUSES.issubset(operation["responses"])
        for status_code in ERROR_STATUSES:
            error_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
            assert error_schema == {"$ref": "#/components/schemas/PortraitErrorResponse"}

    assert operation_count >= 97
    assert raw_response_count == 1

    components = schema["components"]["schemas"]
    success_envelope = components["PortraitSuccess_GenericData_"]
    error_envelope = components["PortraitErrorResponse"]
    assert success_envelope["properties"]["schema_version"]["const"] == "1.0"
    assert error_envelope["properties"]["schema_version"]["const"] == "1.0"
    assert success_envelope["additionalProperties"] is False
    assert error_envelope["additionalProperties"] is False


def test_core_parsing_operations_expose_domain_specific_response_models() -> None:
    schema = app.openapi()
    expected = {
        ("post", "/v1/infer/faces"): "PortraitSuccess_InferFacesData_",
        ("post", "/v1/infer/persons"): "PortraitSuccess_InferPersonsData_",
        ("post", "/v1/infer/pose"): "PortraitSuccess_InferPoseData_",
        ("post", "/v1/infer/appearance"): "PortraitSuccess_InferAppearanceData_",
        ("post", "/v1/infer/gait"): "PortraitSuccess_InferGaitData_",
        ("post", "/v1/infer/tracks"): "PortraitSuccess_InferTracksData_",
        ("post", "/v1/vision/infer"): "PortraitSuccess_VisionInferData_",
        ("get", "/v1/analysis/results"): "PortraitSuccess_AnalysisListData_",
        ("get", "/v1/analysis/results/{archive_id}"): "PortraitSuccess_AnalysisDetailData_",
    }

    for (method, path), component_name in expected.items():
        response_schema = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{component_name}"}

    components = schema["components"]["schemas"]
    assert components["InferFacesData"]["additionalProperties"] is False
    assert components["InferFacesData"]["required"] == [
        "frames",
        "frame_count",
        "face_count",
        "model",
    ]
    assert components["FaceContract"]["properties"]["box"]["maxItems"] == 4
    assert components["FaceContract"]["properties"]["score"]["maximum"] == 1.0


def test_generic_success_data_is_extensible_but_never_an_empty_closed_object() -> None:
    schema = app.openapi()
    generic_data = schema["components"]["schemas"]["GenericData"]

    assert generic_data["type"] == "object"
    assert generic_data["additionalProperties"] is True
