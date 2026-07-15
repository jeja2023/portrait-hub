from __future__ import annotations

import argparse
import ast
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

DEFAULT_TARGET_ROOTS = ("app", "tools", "sdk")
IGNORED_PATH_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".venv", "__pycache__"}
MYPY_CACHE_DIR = Path(".codex-tmp") / "mypy-cache"


def discover_default_targets() -> list[str]:
    targets: list[str] = []
    for root_name in DEFAULT_TARGET_ROOTS:
        root = Path(root_name)
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in IGNORED_PATH_PARTS for part in path.parts):
                continue
            targets.append(path.as_posix())
    if Path("main.py").is_file():
        targets.append("main.py")
    return sorted(dict.fromkeys(targets))


def mypy_strict_enabled() -> bool:
    with Path("pyproject.toml").open("rb") as file:
        config = tomllib.load(file)
    return bool(config.get("tool", {}).get("mypy", {}).get("strict"))


def annotation_errors(targets: list[str]) -> list[str]:
    errors: list[str] = []
    for target in targets:
        tree = ast.parse(Path(target).read_text(encoding="utf-8"), filename=target)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.returns is None:
                errors.append(f"{target}:{node.lineno} {node.name} 缺少返回类型注解")
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in {"self", "cls"}:
                    continue
                if arg.annotation is None:
                    errors.append(f"{target}:{node.lineno} {node.name}.{arg.arg} 缺少类型注解")
            if node.args.vararg is not None and node.args.vararg.annotation is None:
                errors.append(f"{target}:{node.lineno} {node.name}.{node.args.vararg.arg} 缺少类型注解")
            if node.args.kwarg is not None and node.args.kwarg.annotation is None:
                errors.append(f"{target}:{node.lineno} {node.name}.{node.args.kwarg.arg} 缺少类型注解")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the whole-repository strict type gate for PortraitHub Python modules.")
    parser.add_argument(
        "--fallback-ok",
        action="store_true",
        help="Allow the lightweight annotation gate when mypy is unavailable. Intended only for constrained local environments.",
    )
    parser.add_argument("targets", nargs="*")
    args = parser.parse_args()
    targets = args.targets or discover_default_targets()

    missing = [target for target in targets if not Path(target).is_file()]
    if missing:
        print(f"缺少类型检查目标: {', '.join(missing)}", file=sys.stderr)
        return 2

    if not mypy_strict_enabled():
        print("pyproject.toml must enable [tool.mypy] strict = true", file=sys.stderr)
        return 2

    if importlib.util.find_spec("mypy") is not None:
        MYPY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return subprocess.call([sys.executable, "-m", "mypy", "--cache-dir", str(MYPY_CACHE_DIR), *targets])

    if not args.fallback_ok:
        print(
            "mypy is not installed; install requirements/dev.txt or rerun with --fallback-ok for the lightweight annotation gate.",
            file=sys.stderr,
        )
        return 2

    errors = annotation_errors(targets)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("mypy is not installed; fallback annotation gate passed (--fallback-ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
