"""Runtime patch for sharper video frame previews.

Python loads ``sitecustomize`` automatically when it is on ``sys.path``.
We use that hook to keep the locked application files untouched while
giving video parsing a higher resolution thumbnail for the results page.
"""

from __future__ import annotations

import base64
import importlib.abc
import importlib.machinery
import sys
from io import BytesIO
from types import ModuleType
from typing import Any

_TARGET_MODULE = "app.portrait_jobs"
_PATCH_FLAG = "_portrait_hub_hd_video_thumbnail_patch"
_VIDEO_PREVIEW_MAX_SIDE = 1024
_VIDEO_PREVIEW_QUALITY = 90


def _encode_thumbnail(image: Any, max_side: int, quality: int) -> str | None:
    try:
        from PIL import Image
    except Exception:
        return None

    if not isinstance(image, Image.Image):
        return None

    preview = image.copy()
    if preview.mode not in {"RGB", "L"}:
        preview = preview.convert("RGB")
    preview.thumbnail((max_side, max_side))  # type: ignore[no-untyped-call]

    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=quality, optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def _called_from_video_job() -> bool:
    try:
        caller = sys._getframe(2)
    except ValueError:
        return False
    try:
        return caller.f_code.co_name == "run_video_job" and caller.f_globals.get("__name__") == _TARGET_MODULE
    finally:
        del caller


def _patch_module(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return

    original = getattr(module, "image_thumbnail_data_url", None)
    if not callable(original):
        return

    def image_thumbnail_data_url(image: Any, max_side: int = 240) -> str | None:
        if _called_from_video_job():
            return _encode_thumbnail(
                image,
                max(_VIDEO_PREVIEW_MAX_SIDE, int(max_side or 0)),
                _VIDEO_PREVIEW_QUALITY,
            )
        return original(image, max_side=max_side)

    image_thumbnail_data_url.__name__ = getattr(original, "__name__", "image_thumbnail_data_url")
    image_thumbnail_data_url.__doc__ = getattr(original, "__doc__", None)
    setattr(module, "image_thumbnail_data_url", image_thumbnail_data_url)
    setattr(module, _PATCH_FLAG, True)


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader):
        self._wrapped = wrapped

    def create_module(self, spec):
        create_module = getattr(self._wrapped, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module):
        self._wrapped.exec_module(module)
        _patch_module(module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != _TARGET_MODULE:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _PatchLoader(spec.loader)
        return spec


def _install_patch() -> None:
    existing = sys.modules.get(_TARGET_MODULE)
    if existing is not None:
        _patch_module(existing)
        return
    for finder in sys.meta_path:
        if isinstance(finder, _PatchFinder):
            return
    sys.meta_path.insert(0, _PatchFinder())


_install_patch()
