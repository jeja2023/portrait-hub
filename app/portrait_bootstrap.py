import asyncio

from app.portrait_async import run_blocking_io

_STATE_LOADED = False
_STATE_LOAD_LOCK: asyncio.Lock | None = None


def load_portrait_runtime_state() -> None:
    from app.portrait_access import load_access_state
    from app.portrait_analysis_archive import load_analysis_archives_state
    from app.portrait_gallery import load_gallery_state
    from app.portrait_jobs import load_video_jobs_state
    from app.portrait_review import load_review_state
    from app.portrait_streams import load_streams_state
    from app.portrait_thresholds import load_threshold_state

    load_threshold_state()
    load_access_state()
    load_gallery_state()
    load_analysis_archives_state()
    load_video_jobs_state()
    load_review_state()
    load_streams_state()


def mark_portrait_runtime_state_unloaded() -> None:
    global _STATE_LOADED
    _STATE_LOADED = False


async def ensure_portrait_runtime_state_loaded() -> None:
    global _STATE_LOADED, _STATE_LOAD_LOCK
    if _STATE_LOADED:
        return
    if _STATE_LOAD_LOCK is None:
        _STATE_LOAD_LOCK = asyncio.Lock()
    async with _STATE_LOAD_LOCK:
        if _STATE_LOADED:
            return
        await run_blocking_io(load_portrait_runtime_state)
        _STATE_LOADED = True
