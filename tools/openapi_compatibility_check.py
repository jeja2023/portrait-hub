"""Fail CI when the current OpenAPI contract breaks the reviewed baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = PROJECT_ROOT / "contracts" / "openapi-v1-baseline.json"
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
SCHEMA_VALUE_KEYS = {
    "additionalProperties",
    "const",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "nullable",
    "pattern",
    "type",
    "uniqueItems",
}


class OpenAPICompatibilityError(ValueError):
    pass


def _load_document(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenAPICompatibilityError(f"cannot read OpenAPI document {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        raise OpenAPICompatibilityError(f"invalid OpenAPI document: {path}")
    return payload


def _current_document(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return _load_document(path)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from main import app

    payload = app.openapi()
    if not isinstance(payload, dict):
        raise OpenAPICompatibilityError("application returned an invalid OpenAPI document")
    return payload


def _digest(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or set(value) != {"$ref"}:
        return value
    reference = value.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return value
    current: Any = document
    for part in reference[2:].split("/"):
        if not isinstance(current, dict):
            return value
        current = current.get(part.replace("~1", "/").replace("~0", "~"))
    return current if current is not None else value


def _append(changes: list[str], location: str, message: str) -> None:
    changes.append(f"{location}: {message}")


def _compare_schema(baseline: Any, current: Any, location: str, changes: list[str]) -> None:
    if isinstance(baseline, bool):
        if current is not baseline:
            _append(changes, location, "schema boolean changed")
        return
    if not isinstance(baseline, dict):
        return
    if not isinstance(current, dict):
        _append(changes, location, "schema was removed or changed to a non-object")
        return
    baseline_ref = baseline.get("$ref")
    if baseline_ref is not None:
        if current.get("$ref") != baseline_ref:
            _append(changes, location, f"schema reference changed from {baseline_ref!r}")
        return

    for key in sorted(SCHEMA_VALUE_KEYS):
        if key in baseline and current.get(key) != baseline.get(key):
            _append(changes, location, f"schema constraint {key!r} changed")
        elif key not in baseline and key in current:
            if key != "additionalProperties" or current.get(key) is not True:
                _append(changes, location, f"schema added restrictive constraint {key!r}")

    baseline_required = set(baseline.get("required") or [])
    current_required = set(current.get("required") or [])
    if baseline_required != current_required:
        removed = sorted(baseline_required - current_required)
        added = sorted(current_required - baseline_required)
        _append(changes, location, f"required fields changed (removed={removed}, added={added})")

    baseline_properties = baseline.get("properties") or {}
    current_properties = current.get("properties") or {}
    if isinstance(baseline_properties, dict):
        if not isinstance(current_properties, dict):
            _append(changes, location, "object properties were removed")
        else:
            for name, schema in baseline_properties.items():
                if name not in current_properties:
                    _append(changes, f"{location}.properties.{name}", "property was removed")
                    continue
                _compare_schema(schema, current_properties[name], f"{location}.properties.{name}", changes)

    if "items" in baseline:
        _compare_schema(baseline["items"], current.get("items"), f"{location}.items", changes)
    for keyword in ("allOf", "anyOf", "oneOf"):
        baseline_variants = baseline.get(keyword)
        if not isinstance(baseline_variants, list):
            continue
        current_variants = current.get(keyword)
        if not isinstance(current_variants, list) or len(current_variants) != len(baseline_variants):
            _append(changes, location, f"{keyword} alternatives changed")
            continue
        for index, schema in enumerate(baseline_variants):
            _compare_schema(schema, current_variants[index], f"{location}.{keyword}[{index}]", changes)


def _parameter_map(
    document: dict[str, Any], path_item: dict[str, Any], operation: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for value in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
        parameter = _resolve(document, value)
        if not isinstance(parameter, dict):
            continue
        key = (str(parameter.get("in") or ""), str(parameter.get("name") or ""))
        if all(key):
            result[key] = parameter
    return result


def _compare_parameters(
    baseline_document: dict[str, Any],
    current_document: dict[str, Any],
    baseline_path_item: dict[str, Any],
    current_path_item: dict[str, Any],
    baseline_operation: dict[str, Any],
    current_operation: dict[str, Any],
    location: str,
    changes: list[str],
) -> None:
    baseline = _parameter_map(baseline_document, baseline_path_item, baseline_operation)
    current = _parameter_map(current_document, current_path_item, current_operation)
    for key, parameter in baseline.items():
        parameter_location = f"{location}.parameters[{key[0]}:{key[1]}]"
        candidate = current.get(key)
        if candidate is None:
            _append(changes, parameter_location, "parameter was removed")
            continue
        _compare_schema(parameter.get("schema"), candidate.get("schema"), f"{parameter_location}.schema", changes)
    for key, parameter in current.items():
        if key not in baseline and parameter.get("required") is True:
            _append(changes, f"{location}.parameters[{key[0]}:{key[1]}]", "new required parameter")


def _compare_content(
    baseline_document: dict[str, Any],
    current_document: dict[str, Any],
    baseline_value: Any,
    current_value: Any,
    location: str,
    changes: list[str],
) -> None:
    baseline = _resolve(baseline_document, baseline_value)
    current = _resolve(current_document, current_value)
    if not isinstance(baseline, dict):
        return
    if not isinstance(current, dict):
        _append(changes, location, "contract object was removed")
        return
    baseline_content = baseline.get("content") or {}
    current_content = current.get("content") or {}
    if not isinstance(baseline_content, dict):
        return
    if not isinstance(current_content, dict):
        _append(changes, location, "media types were removed")
        return
    for media_type, media in baseline_content.items():
        if media_type not in current_content:
            _append(changes, f"{location}.content[{media_type}]", "media type was removed")
            continue
        baseline_schema = media.get("schema") if isinstance(media, dict) else None
        current_media = current_content[media_type]
        current_schema = current_media.get("schema") if isinstance(current_media, dict) else None
        _compare_schema(baseline_schema, current_schema, f"{location}.content[{media_type}].schema", changes)


def _compare_operation(
    baseline_document: dict[str, Any],
    current_document: dict[str, Any],
    baseline_path_item: dict[str, Any],
    current_path_item: dict[str, Any],
    method: str,
    path: str,
    changes: list[str],
) -> None:
    baseline = baseline_path_item[method]
    current = current_path_item[method]
    location = f"paths[{path}].{method}"
    if baseline.get("operationId") != current.get("operationId"):
        _append(changes, location, "operationId changed")
    _compare_parameters(
        baseline_document,
        current_document,
        baseline_path_item,
        current_path_item,
        baseline,
        current,
        location,
        changes,
    )

    baseline_body = _resolve(baseline_document, baseline.get("requestBody"))
    current_body = _resolve(current_document, current.get("requestBody"))
    if baseline_body is None:
        if isinstance(current_body, dict) and current_body.get("required") is True:
            _append(changes, f"{location}.requestBody", "new required request body")
    else:
        if isinstance(baseline_body, dict) and baseline_body.get("required") is not True:
            if isinstance(current_body, dict) and current_body.get("required") is True:
                _append(changes, f"{location}.requestBody", "optional request body became required")
        _compare_content(
            baseline_document,
            current_document,
            baseline.get("requestBody"),
            current.get("requestBody"),
            f"{location}.requestBody",
            changes,
        )

    baseline_responses = baseline.get("responses") or {}
    current_responses = current.get("responses") or {}
    if not isinstance(current_responses, dict):
        _append(changes, f"{location}.responses", "responses were removed")
    elif isinstance(baseline_responses, dict):
        for status_code, response in baseline_responses.items():
            if status_code not in current_responses:
                _append(changes, f"{location}.responses[{status_code}]", "response status was removed")
                continue
            _compare_content(
                baseline_document,
                current_document,
                response,
                current_responses[status_code],
                f"{location}.responses[{status_code}]",
                changes,
            )
    if baseline.get("security") != current.get("security"):
        _append(changes, f"{location}.security", "operation security requirements changed")


def compare_openapi(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    if baseline.get("security") != current.get("security"):
        _append(changes, "security", "root security requirements changed")
    baseline_paths = baseline.get("paths") or {}
    current_paths = current.get("paths") or {}
    if not isinstance(baseline_paths, dict) or not isinstance(current_paths, dict):
        return ["paths: OpenAPI paths must be objects"]
    for path, baseline_path_item in baseline_paths.items():
        if path not in current_paths:
            _append(changes, f"paths[{path}]", "path was removed")
            continue
        current_path_item = current_paths[path]
        if not isinstance(baseline_path_item, dict) or not isinstance(current_path_item, dict):
            _append(changes, f"paths[{path}]", "path contract changed shape")
            continue
        for method in sorted(HTTP_METHODS & set(baseline_path_item)):
            if method not in current_path_item:
                _append(changes, f"paths[{path}].{method}", "operation was removed")
                continue
            _compare_operation(
                baseline,
                current,
                baseline_path_item,
                current_path_item,
                method,
                path,
                changes,
            )

    baseline_components = baseline.get("components") or {}
    current_components = current.get("components") or {}
    baseline_schemas = baseline_components.get("schemas") if isinstance(baseline_components, dict) else {}
    current_schemas = current_components.get("schemas") if isinstance(current_components, dict) else {}
    if isinstance(baseline_schemas, dict):
        for name, schema in baseline_schemas.items():
            if not isinstance(current_schemas, dict) or name not in current_schemas:
                _append(changes, f"components.schemas.{name}", "schema was removed")
                continue
            _compare_schema(schema, current_schemas[name], f"components.schemas.{name}", changes)
    baseline_security = baseline_components.get("securitySchemes") if isinstance(baseline_components, dict) else None
    current_security = current_components.get("securitySchemes") if isinstance(current_components, dict) else None
    if baseline_security != current_security:
        _append(changes, "components.securitySchemes", "security schemes changed")
    return sorted(set(changes))


def compatibility_report(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    breaking_changes = compare_openapi(baseline, current)
    baseline_paths = baseline.get("paths") or {}
    current_paths = current.get("paths") or {}
    return {
        "ok": not breaking_changes,
        "baseline_sha256": _digest(baseline),
        "current_sha256": _digest(current),
        "baseline_path_count": len(baseline_paths) if isinstance(baseline_paths, dict) else 0,
        "current_path_count": len(current_paths) if isinstance(current_paths, dict) else 0,
        "breaking_change_count": len(breaking_changes),
        "breaking_changes": breaking_changes,
    }


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OpenAPI backward compatibility against a reviewed baseline.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        current = _current_document(args.current)
        if args.write_baseline:
            _write_document(args.baseline, current)
            result = {
                "ok": True,
                "written": str(args.baseline),
                "sha256": _digest(current),
                "path_count": len(current.get("paths") or {}),
            }
        else:
            result = compatibility_report(_load_document(args.baseline), current)
    except OpenAPICompatibilityError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
