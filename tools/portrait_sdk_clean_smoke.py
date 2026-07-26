"""Build and verify the Python SDK from an isolated virtual environment."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

_CLIENT_PROGRAM = r"""
import importlib.metadata
import json
import sys

import portrait_hub_sdk
from portrait_hub_sdk import PortraitHubClient, SDK_VERSION

client = PortraitHubClient(sys.argv[1], timeout=10, max_retries=1)
health = client.health()
compatibility = client.check_compatibility(warn_deprecations=False)
print(json.dumps({
    "distribution_version": importlib.metadata.version("portrait-hub-sdk"),
    "sdk_version": SDK_VERSION,
    "module_path": portrait_hub_sdk.__file__,
    "health": health,
    "compatibility": compatibility,
}, ensure_ascii=False))
"""


def _venv_python(environment_dir: Path) -> Path:
    if os.name == "nt":
        return environment_dir / "Scripts" / "python.exe"
    return environment_dir / "bin" / "python"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server_environment(root: Path, runtime_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    state_paths = {
        "ADMIN_CONFIG_STATE_PATH": "admin-configuration.json",
        "PORTRAIT_ACCESS_STATE_PATH": "portrait-access.json",
        "PORTRAIT_AUDIT_PATH": "portrait-audit.jsonl",
        "PORTRAIT_COMMERCIAL_STATE_PATH": "portrait-commercial.json",
        "PORTRAIT_FEEDBACK_STATE_PATH": "portrait-feedback.json",
        "PORTRAIT_GALLERY_STATE_PATH": "portrait-gallery.json",
        "PORTRAIT_JOBS_STATE_PATH": "portrait-jobs.json",
        "PORTRAIT_MODEL_REGISTRY_STATE_PATH": "portrait-model-registry.json",
        "PORTRAIT_REVIEW_STATE_PATH": "portrait-review.json",
        "PORTRAIT_STREAMS_STATE_PATH": "portrait-streams.json",
        "PORTRAIT_THRESHOLDS_STATE_PATH": "portrait-thresholds.json",
        "STREAM_EVENT_STATE_PATH": "portrait-stream-events.jsonl",
        "TASK_QUEUE_STATE_PATH": "portrait-task-queue.jsonl",
        "VIDEO_UPLOAD_SESSION_STATE_PATH": "video-upload-sessions.json",
        "WEBHOOK_DELIVERY_STATE_PATH": "webhook-deliveries.json",
    }
    environment.update(
        {
            "ADMIN_CONFIG_STATE_PATH": str(runtime_dir / "admin-configuration.json"),
            "ANALYSIS_ARCHIVE_ENABLED": "false",
            "API_TOKEN": "",
            "AUTH_REQUIRED": "false",
            "COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED": "false",
            "COMMERCIAL_LICENSE_REQUIRED": "false",
            "ENABLE_API_DOCS": "false",
            "ENV_PATH": str(runtime_dir / "missing.env"),
            "HSTS_ENABLED": "false",
            "MODEL_CAPABILITIES_PATH": str(root / "model-capabilities.yml"),
            "MODEL_CONFIG_PATH": str(root / "models.yml"),
            "MODEL_CONFIG_READ_FAIL_CLOSED": "false",
            "MODELS_ROOT": str(root / "models"),
            "PORTRAIT_OBJECT_STORAGE_BACKEND": "local",
            "PORTRAIT_RUNTIME_PROFILE": "development",
            "PORTRAIT_STORAGE_BACKEND": "json",
            "PORTRAIT_VECTOR_BACKEND": "local",
            "POSTGRES_DSN": "",
            "PRODUCTION_EXTERNAL_SERVICES_REQUIRED": "false",
            "QDRANT_URL": "",
            "RBAC_ENABLED": "false",
            "REDIS_URL": "",
            "REQUIRE_ENCRYPTION": "false",
            "RUNTIME_STATE_DIR": str(runtime_dir),
            "S3_BUCKET": "",
            "S3_ENDPOINT_URL": "",
            "STATE_READ_FAIL_CLOSED": "true",
            "STATE_WRITE_FAIL_CLOSED": "true",
            "TASK_QUEUE_BACKEND": "local",
            "TASK_QUEUE_DIR": str(runtime_dir / "task-queue"),
            "TENANT_HEADER_REQUIRED": "false",
            "TRUSTED_HOSTS": "127.0.0.1,localhost",
            "VIDEO_JOB_INPUT_DIR": str(runtime_dir / "video-job-inputs"),
            "VIDEO_JOB_WORKER_IN_PROCESS": "false",
            "VIDEO_UPLOAD_PART_DIR": str(runtime_dir / "video-upload-parts"),
        }
    )
    environment.update({key: str(runtime_dir / filename) for key, filename in state_paths.items()})
    return environment


def _wait_for_health(base_url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "service did not start"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated service exited with code {process.returncode}")
        try:
            with urlopen(base_url + "/health", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "healthy":
                    return
                last_error = f"unexpected health response: {payload!r}"
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError(f"isolated service was not healthy within {timeout:g}s: {last_error}")


def validate_smoke_payload(payload: dict[str, Any], environment_dir: Path) -> list[str]:
    errors: list[str] = []
    sdk_version = str(payload.get("sdk_version", ""))
    if not sdk_version or payload.get("distribution_version") != sdk_version:
        errors.append("installed distribution version does not match SDK_VERSION")
    health = payload.get("health")
    if not isinstance(health, dict) or health.get("status") != "healthy":
        errors.append("SDK health request did not return healthy")
    compatibility = payload.get("compatibility")
    if not isinstance(compatibility, dict) or compatibility.get("compatible") is not True:
        errors.append("SDK compatibility check did not pass")
    module_path = Path(str(payload.get("module_path", ""))).resolve()
    try:
        module_path.relative_to(environment_dir.resolve())
    except ValueError:
        errors.append("SDK was not imported from the clean virtual environment")
    return errors


def run_clean_smoke(root: Path, *, base_url: str | None = None, timeout: float = 60) -> dict[str, Any]:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="portrait-sdk-smoke-") as temporary:
        work_dir = Path(temporary)
        runtime_dir = work_dir / "runtime"
        wheel_dir = work_dir / "wheelhouse"
        environment_dir = work_dir / "venv"
        runtime_dir.mkdir()
        wheel_dir.mkdir()
        server: subprocess.Popen[bytes] | None = None
        server_log_path = work_dir / "server.log"
        server_log = None
        try:
            if base_url is None:
                port = _free_loopback_port()
                base_url = f"http://127.0.0.1:{port}"
                server_log = server_log_path.open("wb")
                server = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=root,
                    env=_server_environment(root, runtime_dir),
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for_health(base_url, server, timeout)

            wheel = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--disable-pip-version-check",
                    "--no-deps",
                    "--wheel-dir",
                    str(wheel_dir),
                    str(root / "sdk" / "python"),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if wheel.returncode != 0:
                raise RuntimeError(f"SDK wheel build failed: {wheel.stderr[-4000:]}")
            wheels = sorted(wheel_dir.glob("portrait_hub_sdk-*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(f"expected one SDK wheel, found {len(wheels)}")

            venv.EnvBuilder(with_pip=True, clear=True).create(environment_dir)
            clean_python = _venv_python(environment_dir)
            install = subprocess.run(
                [
                    str(clean_python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-deps",
                    "--no-index",
                    str(wheels[0]),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if install.returncode != 0:
                raise RuntimeError(f"SDK installation failed: {install.stderr[-4000:]}")
            smoke = subprocess.run(
                [str(clean_python), "-c", _CLIENT_PROGRAM, base_url],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if smoke.returncode != 0:
                raise RuntimeError(f"SDK live smoke failed: {smoke.stderr[-4000:]}")
            payload = json.loads(smoke.stdout)
            errors = validate_smoke_payload(payload, environment_dir)
            return {
                "ok": not errors,
                "base_url": base_url,
                "clean_environment": True,
                "wheel": wheels[0].name,
                "sdk": payload,
                "errors": errors,
            }
        except Exception as exc:
            detail = str(exc)
            if server_log_path.is_file():
                detail += "\n" + server_log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            return {"ok": False, "clean_environment": True, "errors": [detail]}
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
            if server_log is not None:
                server_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Python SDK in a clean venv and run live checks.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--base-url", default=None, help="Use an existing service instead of an isolated one.")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_clean_smoke(Path(args.root), base_url=args.base_url, timeout=max(5, args.timeout))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["ok"]:
        sdk = report["sdk"]
        print(f"SDK clean-environment smoke passed: {sdk['sdk_version']} ({report['wheel']})")
    else:
        print("SDK clean-environment smoke failed:", file=sys.stderr)
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
