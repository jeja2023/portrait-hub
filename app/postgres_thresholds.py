from __future__ import annotations

from typing import Any

from app.observability import logger
from app.portrait_response import exception_log_summary
from app import postgres_core as _core


def load_threshold_snapshot() -> dict[str, Any]:
    if not _core.postgres_configured() or _core.psycopg is None:
        logger.warning("postgres threshold load skipped because PostgreSQL is not ready")
        return {}
    query = "SELECT profile, modality, threshold FROM portrait_thresholds ORDER BY modality, profile"
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres threshold load failed: %s", exception_log_summary(exc))
        return {}

    thresholds: dict[str, dict[str, float]] = {}
    for row in rows:
        thresholds.setdefault(str(row["modality"]), {})[str(row["profile"])] = float(row["threshold"])
    return {"version": 1, "thresholds": thresholds}


def save_threshold_snapshot(thresholds: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            for modality, profiles in thresholds.items():
                if not isinstance(profiles, dict):
                    continue
                for profile, value in profiles.items():
                    cursor.execute(
                        """
                        INSERT INTO portrait_thresholds (profile, modality, threshold, updated_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (profile, modality) DO UPDATE SET
                          threshold = EXCLUDED.threshold,
                          updated_at = EXCLUDED.updated_at
                        """,
                        (str(profile), str(modality), float(value)),
                    )

