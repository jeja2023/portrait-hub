from collections.abc import Iterator
from pathlib import Path
import shutil
import warnings
from uuid import uuid4

import pytest


with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"Please use `import python_multipart` instead\.",
        category=PendingDeprecationWarning,
    )
    import starlette.formparsers  # noqa: F401


@pytest.fixture
def workspace_tmp_path() -> Iterator[Path]:
    root = Path(".test_tmp")
    path = root / f"case-{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            root.rmdir()
        except OSError:
            pass
