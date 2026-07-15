from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.observability import logger, wall_time
from app.portrait_response import exception_log_summary
from app.portrait_state import state_path_fingerprint
from app.portrait_stream_worker import run_stream_worker_session
from app.portrait_streams import (
    StreamRecord,
    StreamStatus,
    acquire_stream_worker_lease,
    load_streams_state,
    normalize_stream_status,
    release_stream_worker_lease,
    renew_stream_worker_lease,
    stream_records_snapshot,
)
from app.settings import (
    STREAM_WORKER_LEASE_TTL_SECONDS,
    STREAM_WORKER_LOCK_DIR,
    STREAM_WORKER_MAX_RECONNECTS,
    STREAM_WORKER_POLL_INTERVAL_SECONDS,
    STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS,
)

STREAM_WORKER_OWNER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


@dataclass(slots=True)
class StreamProcessLock:
    path: Path
    token: str
    acquired: bool = True

    def heartbeat(self) -> bool:
        if not self.acquired:
            return False
        payload = read_stream_process_lock_payload(self.path)
        if payload is None or payload.get("token") != self.token:
            return False
        try:
            os.utime(self.path, None)
            return True
        except OSError:
            return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = read_stream_process_lock_payload(self.path)
            if payload is not None and payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(
                "释放视频流 worker 进程锁失败: path_hash=%s error=%s",
                state_path_fingerprint(self.path),
                exception_log_summary(exc),
            )
        finally:
            self.acquired = False


def stream_process_lock_path(stream: StreamRecord) -> Path:
    digest = hashlib.sha256(f"{stream.tenant_id}\0{stream.stream_id}".encode()).hexdigest()[:32]
    return STREAM_WORKER_LOCK_DIR / f"{digest}.lock"


def read_stream_process_lock_payload(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning(
            "读取视频流 worker 进程锁失败: path_hash=%s error=%s",
            state_path_fingerprint(path),
            exception_log_summary(exc),
        )
        return None
    return {str(key): value for key, value in payload.items()} if isinstance(payload, dict) else None


def stream_process_lock_created_at(path: Path, payload: dict[str, Any] | None) -> float:
    timestamps: list[float] = []
    if payload is not None:
        raw_created_at = payload.get("created_at")
        try:
            if raw_created_at is not None:
                timestamps.append(float(raw_created_at))
        except (TypeError, ValueError):
            pass
    try:
        timestamps.append(float(path.stat().st_mtime))
    except OSError:
        pass
    return max(timestamps) if timestamps else wall_time()


def stream_process_lock_is_stale(path: Path) -> bool:
    payload = read_stream_process_lock_payload(path)
    created_at = stream_process_lock_created_at(path, payload)
    stale_seconds = max(1.0, float(STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS))
    return wall_time() - created_at >= stale_seconds


def remove_stale_stream_process_lock(path: Path) -> bool:
    if not path.exists():
        return False
    payload = read_stream_process_lock_payload(path)
    created_at = stream_process_lock_created_at(path, payload)
    stale_seconds = max(1.0, float(STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS))
    if wall_time() - created_at < stale_seconds:
        return False
    if payload is not None and read_stream_process_lock_payload(path) != payload:
        return False
    try:
        path.unlink(missing_ok=True)
        logger.warning("已移除过期视频流 worker 进程锁: path_hash=%s", state_path_fingerprint(path))
        return True
    except Exception as exc:
        logger.warning(
            "移除过期视频流 worker 进程锁失败: path_hash=%s error=%s",
            state_path_fingerprint(path),
            exception_log_summary(exc),
        )
        return False


def create_stream_process_lock_file(lock: StreamProcessLock, owner_id: str) -> None:
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    payload = {
        "owner_id": owner_id,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "token": lock.token,
        "created_at": wall_time(),
    }
    fd = os.open(lock.path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, sort_keys=True)
        file.write("\n")


def acquire_stream_process_lock(stream: StreamRecord, owner_id: str) -> StreamProcessLock | None:
    path = stream_process_lock_path(stream)
    for attempt in range(2):
        lock = StreamProcessLock(path=path, token=uuid4().hex)
        try:
            create_stream_process_lock_file(lock, owner_id)
            return lock
        except FileExistsError:
            if attempt == 0 and remove_stale_stream_process_lock(path):
                continue
            return None
        except Exception as exc:
            logger.warning(
                "stream worker process lock unavailable: tenant=%s stream=%s path_hash=%s error=%s",
                stream.tenant_id,
                stream.stream_id,
                state_path_fingerprint(path),
                exception_log_summary(exc),
            )
            return None
    return None


async def maintain_stream_worker_ownership(
    stream: StreamRecord,
    owner_id: str,
    process_lock: StreamProcessLock | None,
) -> None:
    interval = max(0.01, float(STREAM_WORKER_LEASE_TTL_SECONDS) / 3.0)
    while True:
        await asyncio.sleep(interval)
        renewed = await asyncio.to_thread(
            renew_stream_worker_lease,
            stream,
            owner_id,
            STREAM_WORKER_LEASE_TTL_SECONDS,
        )
        if not renewed:
            raise RuntimeError("stream worker lease lost")
        if process_lock is not None and not await asyncio.to_thread(process_lock.heartbeat):
            raise RuntimeError("stream worker process lock lost")


async def run_leased_stream_worker_session(
    stream: StreamRecord,
    *,
    owner_id: str,
    max_reconnects: int,
    process_lock: StreamProcessLock | None = None,
) -> dict[str, Any]:
    session_task = asyncio.create_task(
        run_stream_worker_session(stream, max_reconnects=max_reconnects)
    )
    ownership_task = asyncio.create_task(
        maintain_stream_worker_ownership(stream, owner_id, process_lock)
    )
    try:
        done, _ = await asyncio.wait(
            {session_task, ownership_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if ownership_task in done:
            ownership_error = ownership_task.exception()
            if ownership_error is not None:
                session_task.cancel()
                try:
                    await session_task
                except asyncio.CancelledError:
                    pass
                raise ownership_error
        return await session_task
    finally:
        for task in (session_task, ownership_task):
            if not task.done():
                task.cancel()
        for task in (session_task, ownership_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            release_stream_worker_lease(stream, owner_id)
        except Exception as exc:
            logger.warning(
                "释放视频流 worker 租约失败: tenant=%s stream=%s error=%s",
                stream.tenant_id,
                stream.stream_id,
                exception_log_summary(exc),
            )
        finally:
            if process_lock is not None:
                process_lock.release()


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
        process_lock = acquire_stream_process_lock(stream, STREAM_WORKER_OWNER_ID)
        if process_lock is None:
            continue
        try:
            leased_stream = acquire_stream_worker_lease(stream, STREAM_WORKER_OWNER_ID, STREAM_WORKER_LEASE_TTL_SECONDS)
            if leased_stream is None:
                process_lock.release()
                continue
            results.append(
                await run_leased_stream_worker_session(
                    leased_stream,
                    owner_id=STREAM_WORKER_OWNER_ID,
                    max_reconnects=max_reconnects,
                    process_lock=process_lock,
                )
            )
        except Exception as exc:
            process_lock.release()
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
            if key not in running_keys and not task.done():
                task.cancel()
            if task.done() or key not in running_keys:
                active.pop(key, None)
        for stream in running:
            key = (stream.tenant_id, stream.stream_id)
            if key in active:
                continue
            process_lock = acquire_stream_process_lock(stream, STREAM_WORKER_OWNER_ID)
            if process_lock is None:
                continue
            try:
                leased_stream = acquire_stream_worker_lease(stream, STREAM_WORKER_OWNER_ID, STREAM_WORKER_LEASE_TTL_SECONDS)
            except Exception as exc:
                process_lock.release()
                logger.warning(
                    "视频流守护进程获取租约失败: tenant=%s stream=%s error=%s",
                    stream.tenant_id,
                    stream.stream_id,
                    exception_log_summary(exc),
                )
                continue
            if leased_stream is None:
                process_lock.release()
                continue
            active[key] = asyncio.create_task(
                run_leased_stream_worker_session(
                    leased_stream,
                    owner_id=STREAM_WORKER_OWNER_ID,
                    max_reconnects=max_reconnects,
                    process_lock=process_lock,
                )
            )
        await asyncio.sleep(max(0.1, float(poll_interval_seconds)))


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 PortraitHub 视频流工作守护进程。")
    parser.add_argument("--tenant-id", help="只处理某个租户的视频流。")
    parser.add_argument("--stream-id", help="只处理一个视频流 ID。")
    parser.add_argument("--once", action="store_true", help="处理当前运行中的视频流一次后退出。")
    parser.add_argument("--poll-interval", type=float, default=STREAM_WORKER_POLL_INTERVAL_SECONDS)
    parser.add_argument("--max-reconnects", type=int, default=STREAM_WORKER_MAX_RECONNECTS)
    parser.add_argument("--json", action="store_true", help="在 --once 模式下输出 JSON 报告。")
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
            print(f"视频流 worker 守护进程：{report['status']} 选中={report['selected_count']}")
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
