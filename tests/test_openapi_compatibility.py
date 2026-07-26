from __future__ import annotations

import json
from copy import deepcopy

from tools.openapi_compatibility_check import DEFAULT_BASELINE, compare_openapi, compatibility_report


def document() -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/items/{item_id}": {
                "get": {
                    "operationId": "get_item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        }
                    ],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}}
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "state": {"type": "string", "enum": ["active", "disabled"]},
                    },
                }
            }
        },
    }


def test_additive_openapi_changes_are_compatible() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/v1/new"] = {"get": {"operationId": "new", "responses": {"200": {"description": "ok"}}}}
    current["paths"]["/v1/items/{item_id}"]["get"]["parameters"].append(
        {"name": "include_meta", "in": "query", "required": False, "schema": {"type": "boolean"}}
    )
    current["components"]["schemas"]["Item"]["properties"]["display_name"] = {"type": "string"}
    current["paths"]["/v1/items/{item_id}"]["get"]["parameters"][0]["required"] = False
    current["components"]["schemas"]["Item"]["additionalProperties"] = True

    assert compare_openapi(baseline, current) == []


def test_restrictive_object_contract_and_root_security_changes_are_breaking() -> None:
    baseline = document()
    baseline["security"] = [{"BearerAuth": []}]
    current = deepcopy(baseline)
    current["security"] = []
    current["components"]["schemas"]["Item"]["additionalProperties"] = False

    changes = compare_openapi(baseline, current)

    assert any("root security requirements changed" in item for item in changes)
    assert any("constraint 'additionalProperties'" in item for item in changes)


def test_removed_operations_and_narrowed_schemas_are_breaking() -> None:
    baseline = document()
    removed = deepcopy(baseline)
    del removed["paths"]["/v1/items/{item_id}"]["get"]
    narrowed = deepcopy(baseline)
    narrowed["components"]["schemas"]["Item"]["properties"]["state"]["enum"] = ["active"]

    removed_changes = compare_openapi(baseline, removed)
    narrowed_changes = compare_openapi(baseline, narrowed)

    assert any("operation was removed" in item for item in removed_changes)
    assert any("constraint 'enum' changed" in item for item in narrowed_changes)


def test_new_required_inputs_and_required_field_changes_are_breaking() -> None:
    baseline = document()
    current = deepcopy(baseline)
    operation = current["paths"]["/v1/items/{item_id}"]["get"]
    operation["parameters"].append({"name": "project", "in": "query", "required": True, "schema": {"type": "string"}})
    current["components"]["schemas"]["Item"]["required"].append("state")

    changes = compare_openapi(baseline, current)

    assert any("new required parameter" in item for item in changes)
    assert any("required fields changed" in item for item in changes)


def test_reviewed_repository_baseline_matches_current_openapi() -> None:
    from main import app

    assert DEFAULT_BASELINE.is_file()
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    report = compatibility_report(baseline, app.openapi())

    assert report["ok"] is True, report["breaking_changes"]
    assert report["baseline_path_count"] > 100
