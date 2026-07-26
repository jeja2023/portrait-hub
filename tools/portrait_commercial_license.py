from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.portrait_commercial_license import (
    CommercialLicenseError,
    canonical_license_bytes,
    verify_license_document,
)


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommercialLicenseError(f"JSON input cannot be read: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise CommercialLicenseError("JSON input root must be an object")
    return value


def private_key(path: Path) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise CommercialLicenseError("private key cannot be loaded") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise CommercialLicenseError("private key must be Ed25519 PEM")
    return loaded


def issue_document(payload: dict[str, Any], key: Ed25519PrivateKey, *, key_id: str) -> dict[str, Any]:
    if "signature" in payload or "license" in payload:
        raise CommercialLicenseError("issue input must be the unsigned license payload only")
    if not key_id.strip():
        raise CommercialLicenseError("key_id is required")
    signature = key.sign(canonical_license_bytes(payload))
    return {
        "license": payload,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": key_id.strip()[:128],
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue or verify fail-closed PortraitHub offline commercial licenses.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    issue = subparsers.add_parser("issue", help="Sign an approved unsigned license payload.")
    issue.add_argument("--payload", required=True, type=Path)
    issue.add_argument("--private-key", required=True, type=Path)
    issue.add_argument("--key-id", required=True)
    issue.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify", help="Verify a signed license for a deployment instance.")
    verify.add_argument("--license", required=True, type=Path)
    verify.add_argument("--public-key", required=True, type=Path)
    verify.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "issue":
            document = issue_document(load_object(args.payload), private_key(args.private_key), key_id=args.key_id)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = {"ok": True, "output": str(args.output), "license_id": document["license"].get("license_id")}
        else:
            verified = verify_license_document(
                load_object(args.license),
                args.public_key,
                expected_instance_id=args.instance_id,
            )
            result = {key: value for key, value in verified.items() if key != "entitlements"}
            result["entitlement_count"] = len(verified["entitlements"])
    except CommercialLicenseError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
