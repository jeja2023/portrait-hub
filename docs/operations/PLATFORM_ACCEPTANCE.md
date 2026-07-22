# PortraitHub 平台验收

本文档定义 0.17.0 平台能力版本的验收范围。

## 范围

平台将按照强结构化 API、租户/项目隔离、安全、状态、存储适配器、SDK、配置、审计、回滚、保留和脱敏契约进行验收。

本版本必须验收：

- `/v1` 成功与错误外层包含 `schema_version: "1.0"`，核心解析接口 OpenAPI 使用专用响应模型。
- `tenant_id + project_id` 贯穿人员库、任务、流、档案、复核、比对、推理、凭证、Webhook、WebSocket 和调用日志。
- 旧调用未选择项目时使用 `default`，API Key/JWT/OIDC 不能越权选择其他项目。
- PostgreSQL 访问状态 CAS、原子日配额、调用日志/摘要以及连接池并发行为通过真实容器集成测试。
- Console Next 与 Python、Node、Go、Java SDK 的项目头和版本元数据一致。

以下内容明确不纳入本次验收范围：

- 用生产级模型替换兜底或占位模型能力。
- 使用真实 Qdrant、S3 兼容对象存储和 Redis 部署执行完整端到端生产数据栈演练。
- 执行真实运维演练，例如生产压测、故障注入、备份恢复演练、镜像扫描门禁、漏洞门禁、GPU OOM 演练、告警验证和回滚彩排。

## 验收门禁

在将平台范围视为已验收之前，请执行以下门禁：

```powershell
python -m pytest -q
python tools\type_check.py
npm run check
python tools\deploy_check.py --import-app --json
python tools\portrait_production_readiness.py --scope platform --strict
git diff --check
```

预期结果：

- 单元测试和契约测试通过。
- PostgreSQL/pgvector 集成环境可用时，标记为 integration 的真实数据库测试通过。
- 部署检查返回 `"ok": true`。
- 平台就绪检查返回 `"ok": true`，且 `strict_failure_count` 为 `0`。
- `git diff --check` 不报告空白字符错误。Windows 上的 CRLF 转换警告仅供参考。

## 完整切换门禁

最终的生产切换门禁仍然是：

```powershell
python tools\portrait_production_readiness.py --strict
```

在被排除的真实模型能力完成模型接入，且真实数据与运维验证已在本次受限平台验收之外执行之前，不得将该完整门禁视为完成。

## 0.17.0 验证记录

- Python：`601 passed / 4 skipped`。
- 严格 mypy：187 个源文件通过；Ruff 全量通过。
- Console Next：9 个测试文件/36 个测试通过，Node SDK、ESLint、Vue TypeScript 和 Vite production build 通过。
- 真实 PostgreSQL/pgvector 容器：新增 schema、访问状态 CAS、日配额、调用日志和核心存储集成通过。
- 部署检查与 `--scope platform --strict`：通过，`strict_failure_count=0`。
- 当前验证机没有 Go 与 Maven 可执行文件，原生 Go/Java SDK 测试未重复执行；Python/Node SDK 和四套 SDK 的静态版本/项目参数契约通过。
- 完整严格门禁仍缺少 `appearance`、`face_detection`、`face_embedding`、`gait`、`pose` 五类真实模型权重，未达到最终生产切换条件。

## 停止规则

除非某项变更补齐了现有的安全、兼容性、发布契约或验证缺口，否则不要为了满足该验收范围而新增功能。
