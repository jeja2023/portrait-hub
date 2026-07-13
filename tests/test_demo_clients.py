import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DEMO = ROOT / "examples" / "demo-clients" / "python_demo_client.py"
NODE_DEMO = ROOT / "examples" / "demo-clients" / "node_demo_client.js"
INTEGRATION_GUIDE = ROOT / "docs" / "operations" / "INTEGRATION_GUIDE.md"


def test_python_demo_client_dry_run_uses_application_api_key() -> None:
    result = subprocess.run(
        [sys.executable, str(PYTHON_DEMO), "--dry-run", "--image", "person-a.jpg", "--image-b", "person-b.jpg", "--video", "clip.mp4"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["auth_scheme"] == "api_key"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["planned_steps"] == ["health", "models", "thresholds", "enroll", "search", "compare_persons", "create_video_job"]
    assert "phk_" not in result.stdout


def test_node_demo_client_dry_run_uses_second_tenant_and_application_api_key() -> None:
    result = subprocess.run(
        ["node", str(NODE_DEMO), "--dry-run", "--image", "person-a.jpg", "--image-b", "person-b.jpg", "--video", "clip.mp4"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["auth_scheme"] == "api_key"
    assert payload["tenant_id"] == "tenant-b"
    assert payload["planned_steps"] == ["health", "models", "thresholds", "enroll", "search", "comparePersons", "createVideoJob"]
    assert "phk_" not in result.stdout


def test_demo_clients_document_stage_two_acceptance_calls() -> None:
    python_source = PYTHON_DEMO.read_text(encoding="utf-8")
    node_source = NODE_DEMO.read_text(encoding="utf-8")
    readme = (ROOT / "examples" / "demo-clients" / "README.md").read_text(encoding="utf-8")
    guide = INTEGRATION_GUIDE.read_text(encoding="utf-8")

    for marker in ["health", "models", "thresholds", "enroll", "search", "compare", "video"]:
        assert marker in readme.lower()
    assert "import os" in guide
    assert "os.getenv(\"PORTRAIT_HUB_API_TOKEN\")" in guide
    for marker in ["client.health()", "client.models()", "client.thresholds()", "client.enroll", "client.search", "client.compare_persons", "client.create_video_job"]:
        assert marker in python_source
    for marker in ["client.health()", "client.models()", "client.thresholds()", "client.enroll", "client.search", "client.comparePersons", "client.createVideoJob"]:
        assert marker in node_source