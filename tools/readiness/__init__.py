"""生产就绪度检查包：按领域拆分的静态源码契约门禁。

每个 checks_* 模块暴露一个 `check_*(root)` 函数，返回与拆分前
tools/portrait_production_readiness.py 完全一致的检查字典列表；
聚合顺序由 security.check_security_controls 保证与历史输出一致。
"""

from tools.readiness.security import check_security_controls
from tools.readiness.structure import check_data_stack, check_templates

__all__ = [
    "check_data_stack",
    "check_security_controls",
    "check_templates",
]
