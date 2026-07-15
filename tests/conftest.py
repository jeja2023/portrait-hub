import os
import shutil
import warnings
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

# 生产代码现在把安全敏感开关默认设为 fail-closed 值（见 app/settings.py）。测试套件会在
# 无鉴权、且以 "testserver" 主机访问端点，因此在导入任何 `app` 模块之前就在这里重新选用
# 宽松值（settings.py 在导入时读取这些值）。各测试仍会按模块 monkeypatch 这些值来断言加固后
# 的行为。这里用强制赋值（而非 setdefault），使运行不受开发者 shell 环境影响而保持确定性。
_TEST_ENV_DEFAULTS = {
    "AUTH_REQUIRED": "false",
    "RBAC_ENABLED": "false",
    "TENANT_HEADER_REQUIRED": "false",
    "ENABLE_API_DOCS": "true",
    "TRUSTED_HOSTS": "*",
    "REQUIRE_ENCRYPTION": "false",
    "HSTS_ENABLED": "false",
}
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ[_key] = _value


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
