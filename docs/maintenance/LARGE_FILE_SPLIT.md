# 大型文件拆分维护指南

适用版本：`0.8.3+`

本文档记录大型文件拆分后的模块所有权、兼容边界和发布检查要求。目标不是追求最小文件，而是让单个文件保持清晰职责，并避免后续功能重新集中到门面文件。

## 模块映射

| 原大型文件 | 当前门面或保留文件 | 拆分模块 |
|---|---|---|
| `frontend/console/views/app.js` | 状态、导航、事件绑定与 `init` | `templates/*.js`、`runtime/*.js`、`visuals/results.js`、`views/{access,observability,governance,results,dashboard}.js` |
| `frontend/console/console.css` | 变量、基础控件、登录与控制台框架 | `styles/components.css`、`styles/data-viewer.css`、`styles/responsive.css` |
| `tools/portrait_production_readiness.py` | CLI 与兼容导出 | `tools/readiness/structure.py`、`security.py`、`sources.py` 和 `checks_*.py` |
| `tools/deploy_check.py` | 非容器检查、聚合与 CLI | `tools/deploy_checks/common.py`、`containers.py` |
| `tests/test_portrait_data_backends.py` | 已移除 | `test_portrait_data_backends_01_*.py` 至 `05_*.py` |
| `tests/test_portrait_v1.py` | 已移除 | `test_portrait_v1_01_*.py` 至 `05_*.py` |
| `tests/test_api_contract.py` | 已移除 | `test_api_contract_01_*.py` 至 `04_*.py` |

## 兼容边界

- 控制台使用经典脚本共享运行时符号；`console.html` 中模板、运行时、领域视图、`app.js`、`console.js` 的加载顺序不得随意调整。
- CSS 文件按 `console.css`、`components.css`、`data-viewer.css`、`responsive.css` 的顺序加载，确保拆分前后的级联优先级一致。
- `tools/portrait_production_readiness.py` 和 `tools/deploy_check.py` 是稳定 CLI/导入门面。调用方应从门面导入，内部模块可继续按职责演进。
- 新增控制台资产时，必须同步 `console.html`、`package.json`、`tools/deploy_check.py`、`tools/readiness/structure.py` 和控制台契约测试。
- 测试文件的数字前缀用于保持历史收集顺序。新增用例应进入对应领域文件，不应恢复已移除的单体文件。

## 规模约束

- 生产代码与前端资产优先保持在 700 行以内，硬上限为 1000 行。
- 测试文件优先保持在 900 行以内；达到上限前应按 API、后端或工作流边界继续拆分。
- 门面文件只负责兼容导出、聚合、参数解析和启动，不承载新的领域实现。

## 发布前验证

在仓库根目录执行：

```powershell
npm run check
.\.venv\Scripts\ruff.exe check app tools tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\deploy_check.py --json --skip-node
git diff --check
```

`0.8.3` 拆分完成时的基线为 `519 passed, 4 skipped`，deploy CLI 58 项、readiness strict 203 项检查通过。
