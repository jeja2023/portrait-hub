from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.portrait_acceptance_evidence import validate_report
from tools.portrait_support_matrix import load_matrix, matrix_status

REQUIRED_ARTIFACT_KINDS = {
    "system_inventory",
    "sbom",
    "vulnerability_scan",
    "supply_chain",
    "model_inventory",
    "configuration_baseline",
    "privacy_compliance",
    "audit_chain",
    "capacity_report",
    "recovery_drill",
    "sla_report",
    "support_matrix",
}
GENERIC_KINDS = {"system_inventory", "configuration_baseline", "audit_chain"}
CONTROL_IDS = {f"COM-{index:03d}" for index in range(1, 13)}
SAFE_KIND = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class EvidencePackageError(ValueError):
    pass


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_file(path: Path, kind: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackageError(f"{kind} is not readable JSON: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise EvidencePackageError(f"{kind} root must be an object")
    return payload


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC) <= datetime.now(UTC)


def _generic_validation(payload: dict[str, Any], kind: str, environment_id: str) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != "1.0" or payload.get("kind") != kind:
        errors.append("schema_version/kind mismatch")
    if payload.get("ok") is not True:
        errors.append("artifact conclusion is not passing")
    if payload.get("environment_id") != environment_id:
        errors.append("artifact environment does not match package environment")
    if not _valid_timestamp(payload.get("completed_at")):
        errors.append("completed_at is invalid")
    source_refs = payload.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        errors.append("source_refs must be non-empty")
    elif any(not isinstance(item, dict) or len(str(item.get("sha256") or "")) != 64 for item in source_refs):
        errors.append("every source_ref must include sha256")
    return errors


def _validate_sbom(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("bomFormat") != "CycloneDX":
        errors.append("SBOM must use CycloneDX JSON")
    if not isinstance(payload.get("specVersion"), str):
        errors.append("SBOM specVersion is required")
    if not isinstance(payload.get("components"), list) or not payload["components"]:
        errors.append("SBOM components must be non-empty")
    return errors


def _validate_vulnerability_scan(payload: dict[str, Any], environment_id: str) -> list[str]:
    if payload.get("kind") == "vulnerability_scan":
        errors = _generic_validation(payload, "vulnerability_scan", environment_id)
        if payload.get("critical_vulnerabilities") != 0 or payload.get("high_vulnerabilities") != 0:
            errors.append("Critical/High vulnerabilities must be zero after approved exceptions")
        return errors
    vulnerabilities = [
        vulnerability
        for result in payload.get("Results", [])
        if isinstance(result, dict)
        for vulnerability in result.get("Vulnerabilities") or []
        if isinstance(vulnerability, dict)
    ]
    blocking = [item for item in vulnerabilities if str(item.get("Severity") or "").upper() in {"CRITICAL", "HIGH"}]
    return [] if not blocking else [f"Trivy report contains {len(blocking)} Critical/High vulnerabilities"]


def _validate_supply_chain(payload: dict[str, Any], environment_id: str, image_digest: str) -> list[str]:
    errors = _generic_validation(payload, "supply_chain", environment_id)
    if payload.get("image_digest") != image_digest:
        errors.append("supply-chain image digest does not match package image digest")
    if payload.get("signature_verified") is not True or payload.get("sbom_attestation_verified") is not True:
        errors.append("image signature and SBOM attestation must both be verified")
    return errors


def _validate_model_inventory(payload: dict[str, Any], environment_id: str) -> list[str]:
    errors = _generic_validation(payload, "model_inventory", environment_id)
    models = payload.get("models")
    required_fields = {
        "model_id",
        "capability",
        "version",
        "sha256",
        "license_name",
        "source_ref",
        "model_card_ref",
        "governance_ref",
        "redistribution_allowed",
    }
    if not isinstance(models, list) or not models:
        return [*errors, "model inventory must be non-empty"]
    for index, model in enumerate(models):
        if not isinstance(model, dict) or not required_fields.issubset(model):
            errors.append(f"models[{index}] is missing governance fields")
        elif len(str(model.get("sha256") or "")) != 64:
            errors.append(f"models[{index}].sha256 is invalid")
    return errors


def _validate_privacy(payload: dict[str, Any], environment_id: str) -> list[str]:
    errors = _generic_validation(payload, "privacy_compliance", environment_id)
    controls = payload.get("controls")
    if not isinstance(controls, list):
        return [*errors, "privacy controls must be an array"]
    indexed = {str(item.get("control_id")): item for item in controls if isinstance(item, dict)}
    missing = sorted(CONTROL_IDS - set(indexed))
    if missing:
        errors.append("missing privacy controls: " + ", ".join(missing))
    for control_id, control in indexed.items():
        if control_id in CONTROL_IDS and control.get("status") not in {"approved", "not_applicable_approved"}:
            errors.append(f"{control_id} is not approved")
    return errors


def _validate_sla(payload: dict[str, Any], environment_id: str) -> list[str]:
    errors = _generic_validation(payload, "sla_report", environment_id)
    if payload.get("source_complete") is not True:
        errors.append("SLA source data is incomplete")
    if payload.get("met") is not True:
        errors.append("SLA report did not meet its defined target")
    if not isinstance(payload.get("definition_version"), str) or not payload["definition_version"]:
        errors.append("SLA definition_version is required")
    return errors


def validate_artifact(
    kind: str,
    path: Path,
    *,
    environment_id: str,
    profile: str,
    image_digest: str,
) -> dict[str, Any]:
    if kind not in REQUIRED_ARTIFACT_KINDS:
        return {"ok": False, "validator": "artifact-kind", "errors": [f"unsupported artifact kind: {kind}"]}
    if not path.is_file():
        return {"ok": False, "validator": kind, "errors": ["artifact file does not exist"]}
    try:
        if kind == "support_matrix":
            payload = load_matrix(path)
            status = matrix_status(payload, target_profile=profile)
            errors = list(status.get("errors") or [])
            validation = {
                "ok": not errors,
                "validator": "portrait_support_matrix",
                "errors": errors,
                "support_level": status.get("support_level"),
                "commercial_ready": status.get("commercial_ready"),
                "blockers": status.get("blockers", []),
            }
        elif kind in {"capacity_report", "recovery_drill"}:
            payload = _json_file(path, kind)
            report = validate_report(payload, base_dir=path.resolve().parent, verify_sources=True)
            errors = list(report.get("errors") or [])
            if payload.get("environment_id") != environment_id:
                errors.append("report environment does not match package environment")
            if payload.get("profile") != profile:
                errors.append("report profile does not match package profile")
            validation = {"ok": not errors, "validator": "portrait_acceptance_evidence", "errors": errors}
        else:
            payload = _json_file(path, kind)
            if kind == "sbom":
                errors = _validate_sbom(payload)
            elif kind == "vulnerability_scan":
                errors = _validate_vulnerability_scan(payload, environment_id)
            elif kind == "supply_chain":
                errors = _validate_supply_chain(payload, environment_id, image_digest)
            elif kind == "model_inventory":
                errors = _validate_model_inventory(payload, environment_id)
            elif kind == "privacy_compliance":
                errors = _validate_privacy(payload, environment_id)
            elif kind == "sla_report":
                errors = _validate_sla(payload, environment_id)
            else:
                errors = _generic_validation(payload, kind, environment_id)
            validation = {"ok": not errors, "validator": f"portrait_evidence:{kind}", "errors": errors}
    except (EvidencePackageError, ValueError) as exc:
        validation = {"ok": False, "validator": kind, "errors": [str(exc)]}
    return validation


def parse_artifacts(values: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        kind, separator, raw_path = value.partition("=")
        if not separator or not SAFE_KIND.fullmatch(kind) or not raw_path:
            raise EvidencePackageError(f"invalid artifact mapping: {value}")
        if kind in artifacts:
            raise EvidencePackageError(f"duplicate artifact kind: {kind}")
        artifacts[kind] = Path(raw_path).resolve()
    missing = sorted(REQUIRED_ARTIFACT_KINDS - set(artifacts))
    extra = sorted(set(artifacts) - REQUIRED_ARTIFACT_KINDS)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if extra:
            parts.append("unsupported=" + ",".join(extra))
        raise EvidencePackageError("artifact set is incomplete: " + " ".join(parts))
    return artifacts


def _private_key(path: Path) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise EvidencePackageError(f"private key cannot be loaded: {type(exc).__name__}") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise EvidencePackageError("private key must be Ed25519 PEM")
    return loaded


def _public_key(path: Path) -> Ed25519PublicKey:
    try:
        loaded = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise EvidencePackageError(f"public key cannot be loaded: {type(exc).__name__}") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise EvidencePackageError("public key must be Ed25519 PEM")
    return loaded


def _public_pem(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _write_zip_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100640 << 16
    archive.writestr(info, data)


def build_evidence_package(
    *,
    artifacts: dict[str, Path],
    output: Path,
    private_key_path: Path,
    audience: str,
    environment_id: str,
    profile: str,
    tenant_id: str,
    project_id: str,
    git_commit: str,
    image_digest: str,
    actor: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if audience not in {"internal", "customer"}:
        raise EvidencePackageError("audience must be internal or customer")
    if output.exists():
        raise EvidencePackageError(f"output already exists: {output}")
    if set(artifacts) != REQUIRED_ARTIFACT_KINDS:
        missing = sorted(REQUIRED_ARTIFACT_KINDS - set(artifacts))
        raise EvidencePackageError("required artifacts are missing: " + ", ".join(missing))
    if not image_digest.startswith("sha256:") or len(image_digest) != 71:
        raise EvidencePackageError("image_digest must be a sha256 OCI digest")
    timestamp = generated_at or datetime.now(UTC).isoformat()
    if not _valid_timestamp(timestamp):
        raise EvidencePackageError("generated_at must be a timezone-aware timestamp that is not in the future")

    included_files: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    failures: dict[str, list[str]] = {}
    for kind in sorted(artifacts):
        path = artifacts[kind]
        validation = validate_artifact(
            kind,
            path,
            environment_id=environment_id,
            profile=profile,
            image_digest=image_digest,
        )
        if not validation["ok"]:
            failures[kind] = list(validation["errors"])
        source_hash = file_sha256(path) if path.is_file() else ""
        if audience == "internal" and path.is_file():
            suffix = path.suffix.lower() or ".bin"
            included_path = f"artifacts/{kind}{suffix}"
            included_data = path.read_bytes()
        else:
            included_path = f"summaries/{kind}.json"
            included_data = canonical_json(
                {
                    "kind": kind,
                    "source_sha256": source_hash,
                    "source_size": path.stat().st_size if path.is_file() else 0,
                    "validation": validation,
                    "redacted": True,
                }
            )
        included_files[included_path] = included_data
        entries.append(
            {
                "kind": kind,
                "source_name": path.name,
                "source_sha256": source_hash,
                "source_size": path.stat().st_size if path.is_file() else 0,
                "included_path": included_path,
                "included_sha256": hashlib.sha256(included_data).hexdigest(),
                "included_size": len(included_data),
                "validation": validation,
            }
        )
    if failures:
        raise EvidencePackageError("artifact validation failed: " + json.dumps(failures, ensure_ascii=False, sort_keys=True))

    identity = {
        "generated_at": timestamp,
        "environment_id": environment_id,
        "profile": profile,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "git_commit": git_commit,
        "image_digest": image_digest,
        "audience": audience,
        "artifact_hashes": {entry["kind"]: entry["source_sha256"] for entry in entries},
    }
    package_id = "evp_" + hashlib.sha256(canonical_json(identity)).hexdigest()[:32]
    unsigned_manifest = {
        "schema_version": "1.0",
        "package_id": package_id,
        "status": "complete",
        "audience": audience,
        "environment_id": environment_id,
        "profile": profile,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "git_commit": git_commit,
        "image_digest": image_digest,
        "generated_at": timestamp,
        "generated_by": actor,
        "artifact_count": len(entries),
        "required_artifact_kinds": sorted(REQUIRED_ARTIFACT_KINDS),
        "artifacts": entries,
    }
    private_key = _private_key(private_key_path)
    public_pem = _public_pem(private_key.public_key())
    public_fingerprint = hashlib.sha256(public_pem).hexdigest()
    signature_value = base64.b64encode(private_key.sign(canonical_json(unsigned_manifest))).decode("ascii")
    manifest = {
        **unsigned_manifest,
        "signature": {
            "algorithm": "Ed25519",
            "public_key_sha256": public_fingerprint,
            "value": signature_value,
        },
    }
    manifest_bytes = canonical_json(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x") as archive:
        _write_zip_member(archive, "manifest.json", manifest_bytes)
        _write_zip_member(archive, "PUBLIC_KEY.pem", public_pem)
        for name, data in sorted(included_files.items()):
            _write_zip_member(archive, name, data)
    return {
        "ok": True,
        "package_id": package_id,
        "status": "complete",
        "audience": audience,
        "output": str(output),
        "package_sha256": file_sha256(output),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature_algorithm": "Ed25519",
        "signature": signature_value,
        "public_key_sha256": public_fingerprint,
        "artifact_count": len(entries),
        "created_at": time.time(),
    }


def verify_evidence_package(
    package_path: Path,
    public_key_path: Path,
    *,
    expected_environment: str | None = None,
    expected_profile: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                errors.append("archive contains duplicate members")
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    errors.append(f"unsafe archive member: {name}")
            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes)
            embedded_public_pem = archive.read("PUBLIC_KEY.pem")
            if not isinstance(manifest, dict):
                raise EvidencePackageError("manifest root must be an object")
            signature = manifest.get("signature")
            if not isinstance(signature, dict) or signature.get("algorithm") != "Ed25519":
                errors.append("manifest signature metadata is invalid")
                signature = {}
            trusted_key = _public_key(public_key_path)
            trusted_public_pem = _public_pem(trusted_key)
            trusted_fingerprint = hashlib.sha256(trusted_public_pem).hexdigest()
            if embedded_public_pem != trusted_public_pem:
                errors.append("embedded public key does not match trusted public key")
            if signature.get("public_key_sha256") != trusted_fingerprint:
                errors.append("public key fingerprint does not match")
            unsigned_manifest = {key: value for key, value in manifest.items() if key != "signature"}
            try:
                decoded_signature = base64.b64decode(str(signature.get("value") or ""), validate=True)
                trusted_key.verify(decoded_signature, canonical_json(unsigned_manifest))
            except (InvalidSignature, ValueError):
                errors.append("manifest signature verification failed")
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, list):
                errors.append("manifest artifacts must be an array")
                artifacts = []
            kinds = {str(item.get("kind")) for item in artifacts if isinstance(item, dict)}
            if kinds != REQUIRED_ARTIFACT_KINDS:
                errors.append("manifest required artifact set is incomplete")
            for index, artifact in enumerate(artifacts):
                if not isinstance(artifact, dict):
                    errors.append(f"artifacts[{index}] is invalid")
                    continue
                included_path = str(artifact.get("included_path") or "")
                if included_path not in names:
                    errors.append(f"artifact member is missing: {included_path}")
                    continue
                data = archive.read(included_path)
                if hashlib.sha256(data).hexdigest() != artifact.get("included_sha256"):
                    errors.append(f"artifact member digest mismatch: {included_path}")
                if len(data) != artifact.get("included_size"):
                    errors.append(f"artifact member size mismatch: {included_path}")
                validation = artifact.get("validation")
                if not isinstance(validation, dict) or validation.get("ok") is not True:
                    errors.append(f"artifact validation is not passing: {artifact.get('kind')}")
            if expected_environment is not None and manifest.get("environment_id") != expected_environment:
                errors.append("package environment does not match expected environment")
            if expected_profile is not None and manifest.get("profile") != expected_profile:
                errors.append("package profile does not match expected profile")
            if manifest.get("status") != "complete" or manifest.get("artifact_count") != len(artifacts):
                errors.append("manifest completion state is inconsistent")
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, EvidencePackageError) as exc:
        errors.append(f"package cannot be verified: {type(exc).__name__}: {exc}")
        manifest = {}
    return {
        "ok": not errors,
        "package_id": manifest.get("package_id"),
        "status": manifest.get("status"),
        "audience": manifest.get("audience"),
        "environment_id": manifest.get("environment_id"),
        "profile": manifest.get("profile"),
        "package_sha256": file_sha256(package_path) if package_path.is_file() else None,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify signed PortraitHub commercial evidence packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--artifact", action="append", default=[], metavar="KIND=PATH")
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--private-key", required=True, type=Path)
    build.add_argument("--audience", required=True, choices=["internal", "customer"])
    build.add_argument("--environment", required=True)
    build.add_argument("--profile", required=True, choices=["private_standard", "private_ha", "platform_api"])
    build.add_argument("--tenant", required=True)
    build.add_argument("--project", required=True)
    build.add_argument("--git-commit", required=True)
    build.add_argument("--image-digest", required=True)
    build.add_argument("--actor", required=True)
    build.add_argument("--register", action="store_true")
    build.add_argument("--json", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("package", type=Path)
    verify.add_argument("--public-key", required=True, type=Path)
    verify.add_argument("--environment")
    verify.add_argument("--profile", choices=["private_standard", "private_ha", "platform_api"])
    verify.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "build":
            artifacts = parse_artifacts(args.artifact)
            result = build_evidence_package(
                artifacts=artifacts,
                output=args.output.resolve(),
                private_key_path=args.private_key.resolve(),
                audience=args.audience,
                environment_id=args.environment,
                profile=args.profile,
                tenant_id=args.tenant,
                project_id=args.project,
                git_commit=args.git_commit,
                image_digest=args.image_digest,
                actor=args.actor,
            )
            if args.register:
                from app.portrait_commercial import register_evidence_package

                result["registry"] = register_evidence_package(
                    {
                        "tenant_id": args.tenant,
                        "project_id": args.project,
                        "evidence_package_id": result["package_id"],
                        "package_type": "commercial_release",
                        "audience": args.audience,
                        "environment": args.environment,
                        "status": "complete",
                        "definition_version": "1.0",
                        "manifest_object_key": result["output"],
                        "manifest_sha256": result["manifest_sha256"],
                        "signature_algorithm": result["signature_algorithm"],
                        "signature": result["signature"],
                        "artifact_count": result["artifact_count"],
                        "missing_required_artifacts": [],
                        "version": 1,
                        "effective_at": result["created_at"],
                        "created_at": result["created_at"],
                        "created_by": args.actor,
                    }
                )
        else:
            result = verify_evidence_package(
                args.package.resolve(),
                args.public_key.resolve(),
                expected_environment=args.environment,
                expected_profile=args.profile,
            )
    except EvidencePackageError as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
