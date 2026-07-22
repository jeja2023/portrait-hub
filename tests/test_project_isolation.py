from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import portrait_access, portrait_auth, routes_portrait_access, routes_portrait_ws, security
from app.portrait_call_logs import clear_call_logs, list_call_logs
from app.portrait_console_access import (
    clear_console_ws_tickets,
    consume_console_ws_ticket,
    issue_console_ws_ticket,
)
from app.portrait_gallery import GALLERY
from main import app


@pytest.fixture(autouse=True)
def isolated_project_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    portrait_access.clear_access_state()
    clear_call_logs()
    clear_console_ws_tickets()
    GALLERY.clear()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    monkeypatch.setattr(routes_portrait_access, "audit_event", lambda *args, **kwargs: None)
    yield
    portrait_access.clear_access_state()
    clear_call_logs()
    clear_console_ws_tickets()
    GALLERY.clear()


def create_project_and_application(
    client: TestClient,
    project_id: str,
) -> str:
    headers = {"X-Tenant-ID": "tenant-projects"}
    project_response = client.post(
        "/v1/access/projects",
        headers=headers,
        json={"project_id": project_id, "name": project_id.title()},
    )
    assert project_response.status_code == 200, project_response.text

    application_response = client.post(
        "/v1/access/applications",
        headers={**headers, "X-Project-ID": project_id},
        json={
            "app_id": f"app-{project_id}",
            "project_id": project_id,
            "name": f"{project_id.title()} App",
            "owner": "integration",
            "scopes": ["access:read", "access:write", "gallery:read", "gallery:write", "jobs:read"],
        },
    )
    assert application_response.status_code == 200, application_response.text
    return str(application_response.json()["data"]["one_time_secret"])


def enable_application_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)


def test_application_keys_isolate_project_data_and_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    alpha_key = create_project_and_application(client, "alpha")
    beta_key = create_project_and_application(client, "beta")
    enable_application_auth(monkeypatch)

    alpha_enroll = client.post(
        "/v1/gallery/enroll",
        headers={"X-API-Key": alpha_key},
        files={"files": ("alpha.png", _image_bytes((20, 80, 180)), "image/png")},
        data={"person_id": "shared-person", "display_name": "Alpha Person", "modality": "body"},
    )
    beta_enroll = client.post(
        "/v1/gallery/enroll",
        headers={"X-API-Key": beta_key},
        files={"files": ("beta.png", _image_bytes((180, 80, 20)), "image/png")},
        data={"person_id": "shared-person", "display_name": "Beta Person", "modality": "body"},
    )
    assert alpha_enroll.status_code == 200, alpha_enroll.text
    assert beta_enroll.status_code == 200, beta_enroll.text

    alpha_people = client.get("/v1/gallery", headers={"X-API-Key": alpha_key}).json()["data"]["people"]
    beta_people = client.get("/v1/gallery", headers={"X-API-Key": beta_key}).json()["data"]["people"]
    assert [item["display_name"] for item in alpha_people] == ["Alpha Person"]
    assert [item["display_name"] for item in beta_people] == ["Beta Person"]

    wrong_project = client.get(
        "/v1/gallery",
        headers={"X-API-Key": alpha_key, "X-Project-ID": "beta"},
    )
    assert wrong_project.status_code == 403
    assert wrong_project.json()["error"]["code"] == "forbidden"

    alpha_apps = client.get("/v1/access/applications", headers={"X-API-Key": alpha_key}).json()["data"]
    beta_apps = client.get("/v1/access/applications", headers={"X-API-Key": beta_key}).json()["data"]
    assert [item["app_id"] for item in alpha_apps["applications"]] == ["app-alpha"]
    assert [item["app_id"] for item in beta_apps["applications"]] == ["app-beta"]

    alpha_projects = client.get("/v1/access/projects", headers={"X-API-Key": alpha_key}).json()["data"]
    assert [item["project_id"] for item in alpha_projects["projects"]] == ["alpha"]

    cross_project_update = client.patch(
        "/v1/access/applications/app-beta",
        headers={"X-API-Key": alpha_key},
        json={"name": "Unauthorized rename"},
    )
    assert cross_project_update.status_code == 404

    cross_project_webhook = client.post(
        "/v1/access/webhooks",
        headers={"X-API-Key": alpha_key},
        json={
            "name": "Cross project",
            "application_id": "app-beta",
            "events": ["job.completed"],
        },
    )
    assert cross_project_webhook.status_code == 404

    alpha_logs = list_call_logs("tenant-projects", project_id="alpha", limit=500)
    beta_logs = list_call_logs("tenant-projects", project_id="beta", limit=500)
    assert any(row["path"] == "/v1/gallery" for row in alpha_logs)
    assert any(row["path"] == "/v1/gallery" for row in beta_logs)
    assert all(row["project_id"] == "alpha" for row in alpha_logs)
    assert all(row["project_id"] == "beta" for row in beta_logs)


def test_application_key_websocket_is_bound_to_project(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    alpha_key = create_project_and_application(client, "alpha")
    create_project_and_application(client, "beta")
    enable_application_auth(monkeypatch)
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", True)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", True)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", None)

    with client.websocket_connect(
        "/ws/jobs/job-missing?tenant_id=tenant-projects&project_id=alpha",
        headers={"X-API-Key": alpha_key},
    ) as websocket:
        payload = websocket.receive_json()
    assert payload["schema_version"] == "1.0"
    assert payload["project_id"] == "alpha"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/jobs/job-missing?tenant_id=tenant-projects&project_id=beta",
            headers={"X-API-Key": alpha_key},
        ):
            pass
    assert exc_info.value.code == 1008


def test_websocket_tickets_are_bound_to_project() -> None:
    wrong_ticket, _ = issue_console_ws_ticket(
        tenant_id="tenant-projects",
        project_id="alpha",
        resource_type="job",
        resource_id="job-1",
        permission="jobs:read",
        now=100.0,
    )
    assert not consume_console_ws_ticket(
        wrong_ticket,
        tenant_id="tenant-projects",
        project_id="beta",
        resource_type="job",
        resource_id="job-1",
        permission="jobs:read",
        now=101.0,
    )

    valid_ticket, _ = issue_console_ws_ticket(
        tenant_id="tenant-projects",
        project_id="alpha",
        resource_type="job",
        resource_id="job-1",
        permission="jobs:read",
        now=100.0,
    )
    assert consume_console_ws_ticket(
        valid_ticket,
        tenant_id="tenant-projects",
        project_id="alpha",
        resource_type="job",
        resource_id="job-1",
        permission="jobs:read",
        now=101.0,
    )


def _image_bytes(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (64, 96), color=color).save(output, format="PNG")
    return output.getvalue()
