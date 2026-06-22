"""Inspect and optionally clean ignored local cache artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Any, Iterable


CACHE_DIR_NAMES = {
    ".codex-pycache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".test_tmp",
    "__pycache__",
}
CACHE_FILE_SUFFIXES = (".pyc", ".pyo")
SKIPPED_DIR_NAMES = {
    ".codex",
    ".codex-tmp",
    ".git",
    ".venv",
    "env",
    "models",
    "runtime-state",
    "venv",
}


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def iter_cache_artifacts(root: Path) -> Iterable[Path]:
    resolved_root = root.resolve()
    stack = [resolved_root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for path in children:
            try:
                resolved_path = path.resolve()
            except OSError:
                continue
            if not is_relative_to(resolved_path, resolved_root):
                continue
            if path.is_dir():
                if path.name in CACHE_DIR_NAMES:
                    yield path
                    continue
                if path.name in SKIPPED_DIR_NAMES:
                    continue
                stack.append(path)
            elif path.is_file() and path.suffix in CACHE_FILE_SUFFIXES:
                yield path


def safe_cache_artifacts(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    artifacts = []
    for path in iter_cache_artifacts(resolved_root):
        try:
            resolved_path = path.resolve()
        except OSError:
            continue
        if is_relative_to(resolved_path, resolved_root):
            artifacts.append(path)
    return sorted(set(artifacts))


def artifact_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def clean_artifacts(paths: list[Path]) -> None:
    def retry_writable(function: Any, path: str, _exc_info: BaseException) -> None:
        try:
            Path(path).chmod(0o700)
            function(path)
        except OSError:
            pass

    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if not path.exists():
            continue
        if path.is_dir():
            try:
                shutil.rmtree(path, onexc=retry_writable)
            except OSError:
                pass
        else:
            try:
                path.unlink()
            except OSError:
                try:
                    os.chmod(path, 0o600)
                    path.unlink()
                except OSError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Report or remove ignored Python/test cache artifacts.")
    parser.add_argument("--root", default=".", help="Workspace root.")
    parser.add_argument("--apply", action="store_true", help="Remove the reported artifacts.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    artifacts = safe_cache_artifacts(root)
    total_bytes = sum(artifact_size(path) for path in artifacts)
    print(f"cache_artifact_count={len(artifacts)}")
    print(f"cache_artifact_bytes={total_bytes}")
    for path in artifacts[:200]:
        print(path.relative_to(root))
    if len(artifacts) > 200:
        print(f"... {len(artifacts) - 200} more")
    if args.apply:
        clean_artifacts(artifacts)
        print("removed=true")
    else:
        print("removed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
