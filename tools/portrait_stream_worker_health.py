from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.portrait_stream_worker import stream_worker_sessions_snapshot


def session_age(now: float, session: dict[str, Any]) -> float | None:
    value = session.get("last_heartbeat_at")
    try:
        return now - float(value)
    except (TypeError, ValueError):
        return None


def evaluate_stream_worker_health(*, max_heartbeat_age_seconds: float = 30.0) -> dict[str, Any]:
    now = time.time()
    sessions = [
        {
            **session,
            "heartbeat_age_seconds": session_age(now, session),
        }
        for session in stream_worker_sessions_snapshot().values()
    ]
    stale = [
        session
        for session in sessions
        if session.get("status") == "running"
        and (
            session.get("heartbeat_age_seconds") is None
            or float(session["heartbeat_age_seconds"]) > max_heartbeat_age_seconds
        )
    ]
    return {
        "ok": not stale,
        "active_sessions": sum(1 for session in sessions if session.get("status") == "running"),
        "stale_session_count": len(stale),
        "max_heartbeat_age_seconds": max_heartbeat_age_seconds,
        "sessions": sessions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check in-process PortraitHub stream worker session health.")
    parser.add_argument("--max-heartbeat-age-seconds", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate_stream_worker_health(max_heartbeat_age_seconds=args.max_heartbeat_age_seconds)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"portrait stream worker health: {'OK' if report['ok'] else 'FAILED'}")
        print(f"active={report['active_sessions']} stale={report['stale_session_count']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
