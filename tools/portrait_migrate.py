from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.portrait_errors import MigrationError
from app.portrait_gallery_records import PersonRecord
from app.portrait_postgres import replace_gallery_snapshot
from app.portrait_vector_store import VECTOR_STORE
from app.portrait_gallery import GALLERY, load_gallery_state
from app.settings import PORTRAIT_STORAGE_BACKEND


def load_gallery_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MigrationError("gallery JSON state file not found")
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("people"), list):
        raise MigrationError("gallery JSON state must contain a people list")
    return payload


def migrate_json_to_postgres(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    payload = load_gallery_json(path)
    people = payload.get("people", [])
    for item in people:
        if not isinstance(item, dict):
            raise MigrationError("gallery person entry must be an object")
        PersonRecord.from_state(item)
    if not dry_run:
        replace_gallery_snapshot(payload)
    return {"source": str(path), "people": len(people), "dry_run": dry_run, "target": "postgres"}


def migrate_gallery_to_vector_store(*, tenant_id: str = "default", dry_run: bool = False, load_state: bool = True) -> dict[str, Any]:
    if load_state and not GALLERY:
        load_gallery_state()
    people = [person for person in GALLERY.values() if person.tenant_id == tenant_id]
    feature_count = 0
    upserted_count = 0
    for person in people:
        person_payload = person.state_dict()
        for feature in person_payload.get("features", []):
            if not isinstance(feature, dict) or "embedding" not in feature:
                continue
            feature_count += 1
            if not dry_run:
                result = VECTOR_STORE.upsert_feature(person_payload, feature)
                if result.get("status") != "skipped":
                    upserted_count += 1
    return {
        "tenant_id": tenant_id,
        "source": PORTRAIT_STORAGE_BACKEND,
        "people": len(people),
        "feature_count": feature_count,
        "upserted_count": upserted_count,
        "dry_run": dry_run,
        "target": VECTOR_STORE.backend_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PortraitHub gallery migration helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    json_pg = subparsers.add_parser("json-to-postgres", help="Import a local gallery JSON state file into PostgreSQL.")
    json_pg.add_argument("--path", type=Path, default=Path("runtime-state/portrait-gallery.json"))
    json_pg.add_argument("--dry-run", action="store_true")

    vector = subparsers.add_parser("gallery-to-vector", help="Rebuild configured vector store from gallery records.")
    vector.add_argument("--tenant-id", default="default")
    vector.add_argument("--dry-run", action="store_true")
    vector.add_argument("--skip-load-state", action="store_true", help="Use the current in-process gallery without loading the configured backend first.")

    args = parser.parse_args()
    if args.command == "json-to-postgres":
        report = migrate_json_to_postgres(args.path, dry_run=args.dry_run)
    else:
        report = migrate_gallery_to_vector_store(tenant_id=args.tenant_id, dry_run=args.dry_run, load_state=not args.skip_load_state)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
