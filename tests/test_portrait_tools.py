from __future__ import annotations

import json

from app.portrait_gallery import GALLERY, add_feature, upsert_person
from tools import load_test, portrait_migrate


def test_migrate_json_to_postgres_dry_run_validates_gallery_state(monkeypatch, workspace_tmp_path) -> None:
    path = workspace_tmp_path / "gallery.json"
    path.write_text(
        json.dumps(
            {
                "people": [
                    {
                        "tenant_id": "tenant-a",
                        "person_id": "p1",
                        "display_name": "Person",
                        "metadata": {},
                        "features": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(portrait_migrate, "replace_gallery_snapshot", lambda payload: called.append(payload))

    report = portrait_migrate.migrate_json_to_postgres(path, dry_run=True)

    assert report["people"] == 1
    assert report["target"] == "postgres"
    assert called == []


def test_migrate_gallery_to_vector_store_dry_run_counts_features() -> None:
    snapshot = dict(GALLERY)
    GALLERY.clear()
    try:
        person = upsert_person("p_vector", "Vector", tenant_id="default")
        add_feature(
            person,
            modality="body",
            embedding=[1.0, 0.0],
            model_id="model",
            model_version="v1",
            quality_score=0.9,
            source_id="source",
        )

        report = portrait_migrate.migrate_gallery_to_vector_store(dry_run=True, load_state=False)

        assert report["people"] == 1
        assert report["feature_count"] == 1
        assert report["dry_run"] is True
        assert report["source"]
    finally:
        GALLERY.clear()
        GALLERY.update(snapshot)


def test_load_test_report_summarizes_status_and_latency(monkeypatch) -> None:
    calls = []

    def fake_request_once(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": 200, "seconds": 0.01}

    monkeypatch.setattr(load_test, "request_once", fake_request_once)

    report = load_test.run_load_test(
        url="http://testserver/health",
        method="GET",
        requests=3,
        concurrency=2,
        token=None,
        tenant_id="default",
        timeout=1.0,
    )

    assert report["requests"] == 3
    assert report["status_counts"] == {"200": 3}
    assert report["latency_seconds"]["p95"] == 0.01


def test_load_test_records_unreachable_targets_as_error() -> None:
    result = load_test.request_once(
        "http://127.0.0.1:1/health",
        method="GET",
        token=None,
        tenant_id="default",
        timeout=0.01,
    )

    assert str(result["status"]).startswith("error:")
