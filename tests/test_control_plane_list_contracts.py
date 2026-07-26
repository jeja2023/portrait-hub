from __future__ import annotations

from main import app

CONTROL_LIST_PATHS = (
    "/v1/access/entitlements",
    "/v1/access/support/cases",
    "/v1/access/usage/timeseries",
    "/v1/admin/compliance/rights-requests",
    "/v1/admin/evidence",
    "/v1/admin/industry-template-applications",
    "/v1/admin/industry-templates",
    "/v1/admin/models/registry",
    "/v1/admin/models/registry/{model_id}/versions",
    "/v1/admin/models/releases/audit",
    "/v1/admin/models/releases/shadow-results",
    "/v1/admin/operations/health-timeline",
    "/v1/admin/operations/incidents",
    "/v1/admin/operations/sla",
    "/v1/admin/operations/sla/reports",
    "/v1/evaluation/datasets",
    "/v1/evaluation/review-samples",
    "/v1/evaluation/track-corrections",
    "/v1/evaluation/track-reviews",
)


def test_control_plane_list_operations_publish_the_common_query_contract() -> None:
    schema = app.openapi()

    for path in CONTROL_LIST_PATHS:
        operation = schema["paths"][path]["get"]
        query_parameters = {
            parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "query"
        }
        assert {"limit", "offset", "cursor", "sort_by", "sort_order"} <= query_parameters, path


def test_v1_write_operations_publish_the_idempotency_header_contract() -> None:
    schema = app.openapi()

    for path, path_item in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method in ("post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            header_parameters = {
                parameter["name"].lower()
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "header"
            }
            assert "idempotency-key" in header_parameters, f"{method.upper()} {path}"
