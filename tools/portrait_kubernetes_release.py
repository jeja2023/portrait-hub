from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

import yaml

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes" / "base"
IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:(?P<digest>[a-fA-F0-9]{64})$")
DNS_NAME_PATTERN = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?$"
)
PLACEHOLDER_DIGESTS = {"0" * 64, "1" * 64}
CLUSTER_SCOPED_KINDS = {"Namespace"}
POD_SPEC_PATHS = {
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "Deployment": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
}

Manifest = dict[str, Any]


class ReleaseManifestError(RuntimeError):
    pass


def _mapping(value: object, *, context: str) -> Manifest:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReleaseManifestError(f"{context} must be a YAML mapping")
    return cast(Manifest, value)


def _nested_mapping(document: Manifest, path: tuple[str, ...]) -> Manifest | None:
    current: object = document
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return cast(Manifest, current) if isinstance(current, dict) else None


def _pod_spec(document: Manifest) -> Manifest | None:
    kind = document.get("kind")
    path = POD_SPEC_PATHS.get(str(kind))
    return _nested_mapping(document, path) if path is not None else None


def _containers(document: Manifest) -> Iterator[Manifest]:
    pod_spec = _pod_spec(document)
    if pod_spec is None:
        return
    for field in ("initContainers", "containers"):
        raw_containers = pod_spec.get(field, [])
        if not isinstance(raw_containers, list):
            continue
        for index, raw_container in enumerate(raw_containers):
            yield _mapping(raw_container, context=f"{document.get('kind')}.{field}[{index}]")


def _load_yaml_documents(path: Path) -> list[Manifest]:
    try:
        raw_documents = yaml.safe_load_all(path.read_text(encoding="utf-8"))
        return [
            _mapping(item, context=str(path))
            for item in raw_documents
            if item is not None
        ]
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReleaseManifestError(f"cannot read Kubernetes YAML {path}: {exc}") from exc


def load_base_resources(base_dir: Path = DEFAULT_BASE_DIR) -> tuple[list[Manifest], Manifest]:
    root = base_dir.resolve()
    kustomization_path = root / "kustomization.yaml"
    documents = _load_yaml_documents(kustomization_path)
    if len(documents) != 1:
        raise ReleaseManifestError("kustomization.yaml must contain exactly one document")
    kustomization = documents[0]
    raw_resources = kustomization.get("resources")
    if not isinstance(raw_resources, list) or not raw_resources:
        raise ReleaseManifestError("kustomization.yaml must list local resources")

    resources: list[Manifest] = []
    for raw_resource in raw_resources:
        if not isinstance(raw_resource, str):
            raise ReleaseManifestError("kustomization resources must be local file paths")
        resource_path = (root / raw_resource).resolve()
        if resource_path.parent != root or not resource_path.is_file():
            raise ReleaseManifestError(f"kustomization resource is not a local file: {raw_resource}")
        resources.extend(_load_yaml_documents(resource_path))
    return resources, kustomization


def load_release_resources(path: Path) -> list[Manifest]:
    if path.is_file():
        return _load_yaml_documents(path)
    if path.is_dir() and (path / "kustomization.yaml").is_file():
        return load_base_resources(path)[0]
    if path.is_dir():
        resources: list[Manifest] = []
        for yaml_path in sorted((*path.glob("*.yaml"), *path.glob("*.yml"))):
            resources.extend(_load_yaml_documents(yaml_path))
        if resources:
            return resources
    raise ReleaseManifestError(f"manifest path does not contain Kubernetes YAML: {path}")


def _validate_image(image: object) -> str | None:
    if not isinstance(image, str):
        return "image must be a string"
    match = IMAGE_PATTERN.fullmatch(image)
    if match is None:
        return "image must use an immutable repository@sha256:<64 hex> reference"
    if "example" in image.lower() or match.group("digest").lower() in PLACEHOLDER_DIGESTS:
        return "image contains an example repository or placeholder digest"
    return None


def _validate_hostname(hostname: object) -> str | None:
    if not isinstance(hostname, str) or DNS_NAME_PATTERN.fullmatch(hostname) is None:
        return "hostname must be a fully qualified DNS name"
    lowered = hostname.lower()
    if "example" in lowered or lowered.rstrip(".").endswith((".invalid", ".localhost", ".test")):
        return "hostname contains a reserved or example domain"
    return None


def _placeholder_paths(value: object, *, path: str = "$") -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _placeholder_paths(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _placeholder_paths(nested, path=f"{path}[{index}]")
    elif isinstance(value, str) and "example" in value.lower():
        yield path


def validate_release(resources: Iterable[Manifest]) -> list[str]:
    documents = list(resources)
    errors: list[str] = []
    workload_count = 0
    route_count = 0
    weighted_route_found = False
    primary_images: dict[str, object] = {}
    for index, document in enumerate(documents):
        kind = str(document.get("kind", "<unknown>"))
        metadata = document.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else "<unnamed>"
        identity = f"{kind}/{name}"
        containers = list(_containers(document))
        if containers:
            workload_count += 1
            primary_images[str(name)] = containers[0].get("image")
        for container_index, container in enumerate(containers):
            image_error = _validate_image(container.get("image"))
            if image_error is not None:
                errors.append(f"{identity} container[{container_index}]: {image_error}")

        if kind == "HTTPRoute":
            route_count += 1
            spec = _nested_mapping(document, ("spec",)) or {}
            hostnames = spec.get("hostnames")
            if not isinstance(hostnames, list) or not hostnames:
                errors.append(f"{identity}: at least one hostname is required")
            else:
                for hostname in hostnames:
                    hostname_error = _validate_hostname(hostname)
                    if hostname_error is not None:
                        errors.append(f"{identity}: {hostname_error}")
            rules = spec.get("rules", [])
            if not isinstance(rules, list):
                errors.append(f"{identity}: rules must be a list")
                rules = []
            for rule_index, raw_rule in enumerate(rules):
                if not isinstance(raw_rule, dict):
                    continue
                backend_refs = raw_rule.get("backendRefs", [])
                if not isinstance(backend_refs, list) or len(backend_refs) < 2:
                    continue
                backend_names = {
                    str(item.get("name"))
                    for item in backend_refs
                    if isinstance(item, dict)
                }
                if {"portrait-hub-api", "portrait-hub-api-canary"} <= backend_names:
                    weighted_route_found = True
                weights = [item.get("weight") for item in backend_refs if isinstance(item, dict)]
                if len(weights) != len(backend_refs) or any(not isinstance(weight, int) for weight in weights):
                    errors.append(f"{identity} rule[{rule_index}]: all backend weights must be integers")
                elif sum(cast(list[int], weights)) != 100:
                    errors.append(f"{identity} rule[{rule_index}]: backend weights must total 100")
                elif any(weight <= 0 or weight >= 100 for weight in cast(list[int], weights)):
                    errors.append(f"{identity} rule[{rule_index}]: weighted rollout values must be between 1 and 99")

        for placeholder_path in _placeholder_paths(document, path=f"$[{index}]"):
            errors.append(f"{identity}: example placeholder at {placeholder_path}")
    if workload_count == 0:
        errors.append("manifest contains no supported Kubernetes workloads")
    if route_count == 0:
        errors.append("manifest contains no HTTPRoute")
    if not weighted_route_found:
        errors.append("manifest contains no stable/canary weighted HTTPRoute")
    stable_image = primary_images.get("portrait-hub-api")
    canary_image = primary_images.get("portrait-hub-api-canary")
    if stable_image is None or canary_image is None:
        errors.append("manifest must contain stable and canary API workloads")
    elif stable_image == canary_image:
        errors.append("stable and canary API workloads must use different image digests")
    return errors


def _apply_kustomization_metadata(resources: list[Manifest], kustomization: Manifest) -> None:
    namespace = kustomization.get("namespace")
    raw_labels = kustomization.get("labels", [])
    common_labels: dict[str, str] = {}
    if isinstance(raw_labels, list):
        for raw_label in raw_labels:
            if isinstance(raw_label, dict) and isinstance(raw_label.get("pairs"), dict):
                common_labels.update(
                    {
                        str(key): str(value)
                        for key, value in raw_label["pairs"].items()
                    }
                )
    for document in resources:
        metadata = document.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise ReleaseManifestError(f"{document.get('kind')} metadata must be a mapping")
        if isinstance(namespace, str) and document.get("kind") not in CLUSTER_SCOPED_KINDS:
            metadata["namespace"] = namespace
        if common_labels:
            labels = metadata.setdefault("labels", {})
            if not isinstance(labels, dict):
                raise ReleaseManifestError(f"{document.get('kind')} metadata.labels must be a mapping")
            labels.update(common_labels)


def materialize_release(
    *,
    stable_image: str,
    canary_image: str,
    hostname: str,
    canary_weight: int,
    base_dir: Path = DEFAULT_BASE_DIR,
) -> list[Manifest]:
    input_errors = []
    for label, image in (("stable", stable_image), ("canary", canary_image)):
        image_error = _validate_image(image)
        if image_error is not None:
            input_errors.append(f"{label} image: {image_error}")
    hostname_error = _validate_hostname(hostname)
    if hostname_error is not None:
        input_errors.append(hostname_error)
    if not 1 <= canary_weight <= 99:
        input_errors.append("canary weight must be between 1 and 99")
    if stable_image == canary_image:
        input_errors.append("stable and canary images must use different digests")
    if input_errors:
        raise ReleaseManifestError("; ".join(input_errors))

    loaded, kustomization = load_base_resources(base_dir)
    resources = copy.deepcopy(loaded)
    _apply_kustomization_metadata(resources, kustomization)
    canary_workload_found = False
    weighted_route_found = False
    for document in resources:
        metadata = document.get("metadata", {})
        name = metadata.get("name") if isinstance(metadata, dict) else None
        is_canary = name == "portrait-hub-api-canary"
        containers = list(_containers(document))
        if containers and is_canary:
            canary_workload_found = True
        for container in containers:
            container["image"] = canary_image if is_canary else stable_image

        if document.get("kind") != "HTTPRoute":
            continue
        spec = _nested_mapping(document, ("spec",))
        if spec is None:
            continue
        spec["hostnames"] = [hostname]
        rules = spec.get("rules", [])
        if not isinstance(rules, list):
            continue
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                continue
            backend_refs = raw_rule.get("backendRefs", [])
            if not isinstance(backend_refs, list):
                continue
            by_name = {
                str(item.get("name")): item
                for item in backend_refs
                if isinstance(item, dict)
            }
            if {"portrait-hub-api", "portrait-hub-api-canary"} <= set(by_name):
                by_name["portrait-hub-api"]["weight"] = 100 - canary_weight
                by_name["portrait-hub-api-canary"]["weight"] = canary_weight
                weighted_route_found = True
    if not canary_workload_found:
        raise ReleaseManifestError("base does not contain the canary API workload")
    if not weighted_route_found:
        raise ReleaseManifestError("base does not contain the stable/canary weighted HTTPRoute")

    errors = validate_release(resources)
    if errors:
        raise ReleaseManifestError("release manifest preflight failed: " + "; ".join(errors))
    return resources


def dump_release(resources: Iterable[Manifest]) -> str:
    return yaml.safe_dump_all(
        list(resources),
        allow_unicode=True,
        explicit_start=True,
        sort_keys=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize and preflight a deployable PortraitHub Kubernetes release.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="replace base placeholders and emit a release manifest")
    render_parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    render_parser.add_argument("--stable-image", required=True)
    render_parser.add_argument("--canary-image", required=True)
    render_parser.add_argument("--hostname", required=True)
    render_parser.add_argument("--canary-weight", required=True, type=int)
    render_parser.add_argument("--output", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate", help="reject placeholders and mutable release inputs")
    validate_parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "render":
            resources = materialize_release(
                base_dir=args.base_dir,
                stable_image=args.stable_image,
                canary_image=args.canary_image,
                hostname=args.hostname,
                canary_weight=args.canary_weight,
            )
            rendered = dump_release(resources)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            result = {"ok": True, "output": str(args.output), "resources": len(resources)}
        else:
            errors = validate_release(load_release_resources(args.manifest))
            result = {"ok": not errors, "errors": errors}
    except (OSError, ReleaseManifestError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
