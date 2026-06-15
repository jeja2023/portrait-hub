from __future__ import annotations

import argparse
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
import tomllib


DEFAULT_TARGETS = [
    "app/core.py",
    "app/portrait_errors.py",
    "app/gallery_state.py",
    "app/gallery_search.py",
    "app/postgres_core.py",
    "app/postgres_gallery.py",
    "app/postgres_jobs.py",
    "app/portrait_gallery.py",
    "app/portrait_model_runtime.py",
    "app/portrait_postgres.py",
    "app/portrait_tracking.py",
    "app/runtime_face.py",
    "app/runtime_body.py",
    "app/runtime_pose.py",
    "app/runtime_gait.py",
    "app/runtime_appearance.py",
    "app/runtime_common.py",
    "app/tracking_state.py",
    "app/tracking_association.py",
    "tools/type_check.py",
]


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
                errors.append(f"{target}:{node.lineno} {node.name} missing return annotation")
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in {"self", "cls"}:
                    continue
                if arg.annotation is None:
                    errors.append(f"{target}:{node.lineno} {node.name}.{arg.arg} missing annotation")
            if node.args.vararg is not None and node.args.vararg.annotation is None:
                errors.append(f"{target}:{node.lineno} {node.name}.{node.args.vararg.arg} missing annotation")
            if node.args.kwarg is not None and node.args.kwarg.annotation is None:
                errors.append(f"{target}:{node.lineno} {node.name}.{node.args.kwarg.arg} missing annotation")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the focused type gate for typed PortraitHub modules.")
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS)
    args = parser.parse_args()

    missing = [target for target in args.targets if not Path(target).is_file()]
    if missing:
        print(f"missing type-check targets: {', '.join(missing)}", file=sys.stderr)
        return 2

    if not mypy_strict_enabled():
        print("pyproject.toml must enable [tool.mypy] strict = true", file=sys.stderr)
        return 2

    if importlib.util.find_spec("mypy") is not None:
        return subprocess.call([sys.executable, "-m", "mypy", *args.targets])

    errors = annotation_errors(args.targets)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("mypy is not installed; fallback annotation gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
