from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app import portrait_analysis_archive, portrait_object_storage
from main import app


def image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (96, 80), color=(24, 96, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_inference_returns_the_archived_frame_preview(
    monkeypatch, workspace_tmp_path
) -> None:
    archive_path = workspace_tmp_path / "analysis-archive.sqlite3"
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(
        portrait_analysis_archive, "PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH", archive_path
    )
    monkeypatch.setattr(portrait_analysis_archive, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(portrait_analysis_archive, "ANALYSIS_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)

    response = TestClient(app).post(
        "/v1/infer/faces",
        files={"files": ("source.png", image_bytes(), "image/png")},
    )

    assert response.status_code == 200, response.text
    thumbnail = response.json()["data"]["frames"][0]["thumbnail"]
    assert thumbnail.startswith("data:image/jpeg;base64,")
    assert archive_path.exists()
    assert "data:image" not in archive_path.read_bytes().decode("latin-1")
