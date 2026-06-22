from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app.observability import logger
from app.portrait_response import exception_log_summary
from app.portrait_stream_worker import run_stream_worker_session
from app.portrait_streams import StreamRecord, StreamStatus, load_streams_state, normalize_stream_status, stream_records_snapshot
from app.settings import STREAM_WORKER_MAX_RECONNECTS, STREAM_WORKER_POLL_INTERVAL_SECONDS


def selected_running_streams(*, tenant_id: str | None = None, stream_id: str | None = None) -> list[StreamRecord]:
    streams = []
    for stream in stream_records_snapshot():
        if tenant_id is not None and stream.tenant_id != tenant_id:
            continue
        if stream_id is not None and stream.stream_id != stream_id:
            continue
        if normalize_stream_status(stream.status) == StreamStatus.RUNNING:
            streams.append(stream)
    return sorted(streams, key=lambda item: (item.tenant_id, item.stream_id))


async def run_daemon_once(
    *,
    tenant_id: str | None = None,
    stream_id: str | None = None,
    max_reconnects: int = STREAM_WORKER_MAX_RECONNECTS,
) -> dict[str, Any]:
    load_streams_state()
    streams = selected_running_streams(tenant_id=tenant_id, stream_id=stream_id)
    results = []
    for stream in streams:
        try:
            results.append(await run_stream_worker_session(stream, max_reconnects=max_reconnects))
        except Exception as exc:
            logger.warning(
                "stream daemon failed one session: tenant=%s stream=%s error=%s",
                stream.tenant_id,
                stream.stream_id,
                exception_log_summary(exc),
            )
            results.append({"tenant_id": stream.tenant_id, "stream_id": stream.stream_id, "status": "error"})
    return {
        "status": "idle" if not results else "processed",
        "selected_count": len(streams),
        "processed_count": len(results),
        "sessions": results,
    }


async def run_daemon_forever(
    *,
    tenant_id: str | None = None,
    stream_id: str | None = None,
    poll_interval_seconds: float = STREAM_WORKER_POLL_INTERVAL_SECONDS,
    max_reconnects: int = STREAM_WORKER_MAX_RECONNECTS,
) -> None:
    active: dict[tuple[str, str], asyncio.Task[Any]] = {}
    while True:
        load_streams_state()
        running = selected_running_streams(tenant_id=tenant_id, stream_id=stream_id)
        running_keys = {(stream.tenant_id, stream.stream_id) for stream in running}
        for key, task in list(active.items()):
            if task.done() or key not in running_keys:
                active.pop(key, None)
        for stream in running:
            key = (stream.tenant_id, stream.stream_id)
            if key not in active:
                active[key] = asyncio.create_task(run_stream_worker_session(stream, max_reconnects=max_reconnects))
        await asyncio.sleep(max(0.1, float(poll_interval_seconds)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PortraitHub stream worker daemon.")
    parser.add_argument("--tenant-id", help="Only process streams for one tenant.")
    parser.add_argument("--stream-id", help="Only process one stream id.")
    parser.add_argument("--once", action="store_true", help="Process currently running streams once and exit.")
    parser.add_argument("--poll-interval", type=float, default=STREAM_WORKER_POLL_INTERVAL_SECONDS)
    parser.add_argument("--max-reconnects", type=int, default=STREAM_WORKER_MAX_RECONNECTS)
    parser.add_argument("--json", action="store_true", help="Print JSON report in --once mode.")
    args = parser.parse_args()

    if args.once:
        report = asyncio.run(
            run_daemon_once(
                tenant_id=args.tenant_id,
                stream_id=args.stream_id,
                max_reconnects=args.max_reconnects,
            )
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"portrait stream worker daemon: {report['status']} selected={report['selected_count']}")
        return 0

    asyncio.run(
        run_daemon_forever(
            tenant_id=args.tenant_id,
            stream_id=args.stream_id,
            poll_interval_seconds=args.poll_interval,
            max_reconnects=args.max_reconnects,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
