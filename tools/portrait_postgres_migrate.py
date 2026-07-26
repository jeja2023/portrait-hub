from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
DEFAULT_MIGRATIONS_DIR = Path(__file__).with_name("postgres_migrations")
LOCK_NAME = "portrait-hub-postgres-migrations-v1"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sha256: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_migrations(directory: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"migration directory does not exist: {directory}")
    migrations: list[Migration] = []
    seen: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen:
            raise MigrationError(f"duplicate migration version: {version:04d}")
        seen.add(version)
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                path=path,
                sha256=file_sha256(path),
            )
        )
    if not migrations:
        raise MigrationError("no PostgreSQL migrations were found")
    return migrations


def _driver() -> Any:
    try:
        import psycopg
    except Exception as exc:
        raise MigrationError(f"psycopg is unavailable: {type(exc).__name__}") from exc
    return psycopg


def _ensure_migration_table(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS portrait_schema_migrations (
              version INTEGER PRIMARY KEY CHECK (version > 0),
              name TEXT NOT NULL,
              sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              applied_by TEXT NOT NULL,
              execution_ms BIGINT NOT NULL CHECK (execution_ms >= 0)
            )
            """
        )
    connection.commit()


def _applied_migrations(connection: Any) -> dict[int, dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT version, name, sha256, applied_at, applied_by, execution_ms "
            "FROM portrait_schema_migrations ORDER BY version"
        )
        rows = cursor.fetchall()
    return {
        int(row[0]): {
            "version": int(row[0]),
            "name": str(row[1]),
            "sha256": str(row[2]),
            "applied_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
            "applied_by": str(row[4]),
            "execution_ms": int(row[5]),
        }
        for row in rows
    }


def _validate_history(migrations: Iterable[Migration], applied: dict[int, dict[str, Any]]) -> None:
    known = {migration.version: migration for migration in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise MigrationError("database contains migrations absent from this release: " + ", ".join(f"{item:04d}" for item in unknown))
    drifted = [
        f"{version:04d}"
        for version, record in applied.items()
        if record["sha256"] != known[version].sha256 or record["name"] != known[version].name
    ]
    if drifted:
        raise MigrationError("applied migration checksum/name drift detected: " + ", ".join(drifted))


def migration_status(
    dsn: str,
    *,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    target: int | None = None,
) -> dict[str, Any]:
    if not dsn.strip():
        raise MigrationError("POSTGRES_DSN is required")
    migrations = discover_migrations(migrations_dir)
    if target is not None:
        migrations = [migration for migration in migrations if migration.version <= target]
        if not migrations or migrations[-1].version != target:
            raise MigrationError(f"migration target does not exist: {target:04d}")
    psycopg = _driver()
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        _ensure_migration_table(connection)
        applied = _applied_migrations(connection)
        _validate_history(discover_migrations(migrations_dir), applied)
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            server_version_num = int(cursor.fetchone()[0])
    pending = [migration for migration in migrations if migration.version not in applied]
    return {
        "ok": not pending and server_version_num >= 150000,
        "server_version_num": server_version_num,
        "target_version": migrations[-1].version,
        "applied_versions": sorted(applied),
        "pending_versions": [migration.version for migration in pending],
        "migrations": [
            {
                "version": migration.version,
                "name": migration.name,
                "sha256": migration.sha256,
                "status": "applied" if migration.version in applied else "pending",
            }
            for migration in migrations
        ],
    }


def apply_migrations(
    dsn: str,
    *,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    target: int | None = None,
    applied_by: str = "portrait-release-preflight",
) -> dict[str, Any]:
    if not dsn.strip():
        raise MigrationError("POSTGRES_DSN is required")
    migrations = discover_migrations(migrations_dir)
    if target is not None:
        migrations = [migration for migration in migrations if migration.version <= target]
        if not migrations or migrations[-1].version != target:
            raise MigrationError(f"migration target does not exist: {target:04d}")
    psycopg = _driver()
    applied_now: list[int] = []
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        _ensure_migration_table(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        connection.commit()
        try:
            applied = _applied_migrations(connection)
            _validate_history(discover_migrations(migrations_dir), applied)
            connection.commit()
            for migration in migrations:
                if migration.version in applied:
                    continue
                sql = migration.path.read_text(encoding="utf-8")
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT clock_timestamp()")
                        started_at = cursor.fetchone()[0]
                        cursor.execute(sql, prepare=False)
                        cursor.execute(
                            """
                            INSERT INTO portrait_schema_migrations
                              (version, name, sha256, applied_by, execution_ms)
                            VALUES
                              (%s, %s, %s, %s,
                               GREATEST(0, (EXTRACT(EPOCH FROM (clock_timestamp() - %s)) * 1000)::BIGINT))
                            """,
                            (migration.version, migration.name, migration.sha256, applied_by[:256], started_at),
                        )
                applied_now.append(migration.version)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
            connection.commit()
    status = migration_status(dsn, migrations_dir=migrations_dir, target=target)
    return {**status, "applied_now": applied_now}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply and verify ordered PortraitHub PostgreSQL migrations.")
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN", ""))
    parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    parser.add_argument("--target", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--applied-by", default=os.getenv("PORTRAIT_RELEASE_ACTOR", "portrait-release-preflight"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            apply_migrations(
                args.dsn,
                migrations_dir=args.migrations_dir,
                target=args.target,
                applied_by=args.applied_by,
            )
            if args.apply
            else migration_status(args.dsn, migrations_dir=args.migrations_dir, target=args.target)
        )
    except MigrationError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
