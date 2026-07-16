"""安全与契约门禁聚合器。

按领域拆分的分组检查在 checks_* 模块中实现；本聚合器保证
返回列表的内容与顺序与拆分前的单体 check_security_controls 一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.checks_api_limits import check_api_limits_and_tenant
from tools.readiness.checks_governance_audit import check_model_governance_audit
from tools.readiness.checks_http_auth import check_http_auth_hardening
from tools.readiness.checks_model_config import check_model_config_supply
from tools.readiness.checks_platform_quality import check_platform_quality
from tools.readiness.checks_sdk_console import check_sdk_and_console
from tools.readiness.checks_state_integrity import check_state_integrity
from tools.readiness.checks_surface_tools import check_surface_and_tools

__all__ = ["check_security_controls"]


def check_security_controls(root: Path) -> list[dict[str, Any]]:
    return [
        *check_platform_quality(root),
        *check_surface_and_tools(root),
        *check_model_config_supply(root),
        *check_sdk_and_console(root),
        *check_http_auth_hardening(root),
        *check_model_governance_audit(root),
        *check_api_limits_and_tenant(root),
        *check_state_integrity(root),
    ]
