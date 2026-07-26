from pathlib import Path

from tools.portrait_sdk_clean_smoke import _server_environment, validate_smoke_payload


def test_validate_smoke_payload_requires_installed_live_compatible_sdk(workspace_tmp_path: Path) -> None:
    environment_dir = workspace_tmp_path / "venv"
    module_path = environment_dir / "Lib" / "site-packages" / "portrait_hub_sdk" / "__init__.py"
    payload = {
        "distribution_version": "0.18.0",
        "sdk_version": "0.18.0",
        "module_path": str(module_path),
        "health": {"status": "healthy"},
        "compatibility": {"compatible": True, "api_contract": "v1"},
    }

    assert validate_smoke_payload(payload, environment_dir) == []

    payload["module_path"] = str(workspace_tmp_path / "portrait_hub_sdk" / "__init__.py")
    payload["compatibility"] = {"compatible": False}
    errors = validate_smoke_payload(payload, environment_dir)
    assert "SDK was not imported from the clean virtual environment" in errors
    assert "SDK compatibility check did not pass" in errors


def test_sdk_smoke_server_environment_is_isolated(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "root"
    runtime = workspace_tmp_path / "runtime"

    environment = _server_environment(root, runtime)

    assert environment["AUTH_REQUIRED"] == "false"
    assert environment["REDIS_URL"] == ""
    assert environment["POSTGRES_DSN"] == ""
    assert environment["RUNTIME_STATE_DIR"] == str(runtime)
    assert environment["PORTRAIT_COMMERCIAL_STATE_PATH"].startswith(str(runtime))
    assert environment["MODEL_CONFIG_PATH"] == str(root / "models.yml")
