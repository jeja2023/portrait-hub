# 后审计优化升级记录

版本：`0.7.1`

日期：2026-07-14

本文记录本轮全面检查后的已落地优化，以及仍需要真实生产依赖或业务数据配合的扩展验收项。它补充 `影鉴优化方案.md` 和 `PLATFORM_ACCEPTANCE.md`，作为后续升级的执行清单。

## 2026-07-14 / v0.7.1 租户目录与管理员开通体验收口

- 在 `0.7.0` 单凭证优先底座上，新增租户目录接口和控制台“租户开通”面板，管理员只需维护租户名称、用户/角色和接入应用。
- 后台负责生成稳定 `tenant_id`、默认接入应用、一次性 API Key、scope、限流、配额和审计归属，业务系统默认只携带单租户 API Key。
- 租户目录读写独立为 `tenants:read` / `tenants:write` 管理权限，默认业务应用不授予该能力，保持后台管理面和业务调用面的安全边界。
- 文档、SDK 示例、OpenAPI 契约测试和控制台契约测试已同步，版本启用为 `0.7.1`。
## 2026-07-14 / v0.7.0 租户凭证体验升级

- 保留租户作为数据隔离、审计和配额边界，但将系统对接升级为“单凭证优先”：单租户 API Key/JWT 自动解析租户，旧版 `X-Tenant-ID + 凭证` 继续兼容。
- 接入应用密钥现在可直接驱动权限、限流、配额和调用日志归属，减少外部系统手工维护 `tenant_id`、scope 和 token claim 的成本。
- 后续用户中心应建立在该底座之上：管理员维护租户名称、用户、角色和接入应用，后台自动生成稳定 `tenant_id` 与凭证配置。

## 2026-07-13 / v0.6.2 控制台信息架构与导航模块化

本轮延续“控制台模块化”扩展方向，在不改变后端 API 契约的前提下完成前端信息架构和导航维护边界收口：

- 侧栏按「总览 / 智能分析 / 比对检索 / 人员库 / 接入中心 / 模型与评估 / 运维合规」重组，减少业务操作、接入开发、模型治理和运维审计入口混排。
- 新增 `frontend/console/views/navigation.js`，集中维护导航分组与总览快捷入口；`console.html` 加载该资源，`npm run check` 纳入语法检查。
- `frontend/console/views/app.js` 改为消费 `PortraitConsoleModules.navigation`，并将“刷新当前”逻辑收敛为 `viewRefreshHandlers` 映射表。
- 人员库新增独立“特征重建”页面，复用 `/v1/gallery/reindex` 按模态和模型重建向量索引，默认 dry-run 预演；人员管理页回归查询、更新、删除和特征核验职责。
- `frontend/console/views/gallery.js` 与 `frontend/console/views/operations.js` 同步新的视图注册分组，避免产品侧导航和代码层注册再次漂移。

验收：`npm run check` 与 `git diff --check` 均通过。

## 本轮已落地

- 修复类型与部署门禁阻断：`tools/type_check.py`、`tools/deploy_check.py --skip-node` 已恢复通过，模型治理 YAML 读取、流 worker 锁时间解析和 UTF-8 BOM 解析边界均已加固。
- 增加源码编码卫生门禁：部署检查现在扫描 app、tools、sdk、tests、frontend、docs、ops、deploy、requirements 和 GitHub workflow，阻止 UTF-8 BOM 再次进入源码与配置。
- 拆分控制台配置边界：`frontend/console/console.config.js` 承载端点映射和告警默认值，主控制台脚本只消费 `window.PortraitConsoleConfig`，并由 API 契约、部署检查和生产就绪检查共同守住。
- 收口图库变更回滚边界：新增 `app/portrait_gallery_mutations.py`，将对象清理、图库快照恢复和回滚失败脱敏从路由中抽出，路由只保留 HTTP 编排和依赖注入。
- 扩充生产就绪门禁：`tools/portrait_production_readiness.py` 已将控制台配置资产和外置配置契约纳入平台级检查。
- 标准化 Node 检查入口：新增根目录 `package.json`，`npm run check` 统一执行 Node SDK 契约测试，并同步 CI、部署检查和生产就绪模板。


## 2026-07-13 / v0.6.0 三阶段平台收口

本轮按 `PORTRAIT_HUB_THREE_PHASE_OPTIMIZED_PLAN.md` 复核现状后，确认 ReID-only 平台闭环、图库/比对/视频任务/视频流、模型治理、阈值、审计、保留清理、SLO 和多模态分析骨架均已有代码落点。剩余可在无新增真实模型资产情况下完成的缺口集中在阶段二接入中心的信息架构。`0.6.0` 将这批三阶段平台能力正式纳入版本记录；真实模型包、真实回归报告、真实 cutover 证据和真实 staging 服务联调继续作为完整生产切换前置验收。

本轮同时启用项目版本 `0.6.0`，同步更新 `app/settings.py`、`pyproject.toml`、`package.json`、`更新日志.md` 和三阶段计划版本号。

已完成：

- 控制台“接入中心”补齐 OpenAPI 页面，可读取 `/openapi.json`、展示核心 `/v1` 路径声明状态，并提供受控环境检查命令。
- 控制台新增 Webhook 页面，可维护租户回调端点、接入应用、事件订阅、重试和超时策略，支持一次性签名密钥轮换预览和 dry-run 样例事件生成。
- 新增 `/v1/access/applications` 与 `/v1/access/webhooks` 管理接口，持久化租户应用、API Key 哈希、Webhook 签名密钥哈希和轮换宽限窗口，并将 `phk_...` 应用密钥接入 `require_api_token` 与 RBAC scope 校验。

- Python/Node SDK 新增显式 `auth_scheme` / `authScheme`，应用 API Key 走 `X-API-Key`，默认 Bearer/JWT 兼容既有调用。
- SDK 与控制台示例补齐批量异步和视频轮询：Python/Node SDK 新增 `search_batch`/`searchBatch` 与 `compare_batch`/`compareBatch`，控制台 SDK 页展示批量 `batch_id` 和离线视频任务轮询示例，并纳入 API 契约与 readiness 门禁。
- 接口调试台 扩展为阶段二受控调试面：覆盖批量检索、批量比对、实时流创建和流事件查询，按接口模板自动构造 multipart/JSON/GET 请求，并保留统一响应外层的 `request_id`、HTTP 状态和 `detail.code` 供调用日志交叉排障。
- SLO 面板补齐运维口径：优先使用近 30 天调用日志计算成功率、错误数和错误预算燃烧率，回退到 Prometheus 计数；同时展示推理 p95/p99、GPU 队列 p95/p99、全局/按设备队列深度、活跃流和模型热状态，并纳入 API 契约与 readiness 门禁。
- 合规审计页接入审计链校验：新增受 `admin:status` 权限保护的 `/v1/admin/audit/verify` 和 `/v1/admin/audit/events`，返回脱敏 `path_hash`、记录数、错误数、链头哈希与当前租户最近审计事件白名单字段，支持按事件、结果、分类、request_id 和时间窗口过滤，并汇总删除、导出、模型版本和保留清理分类；控制台合规页展示校验结果、筛选控件、分类摘要和审计事件表，并纳入 API 契约、deploy check 与 readiness 门禁。
- 数据保留与备份页补齐备份快照读回：新增受 `admin:export` 权限保护的 `/v1/admin/backups`，从审计链读取当前租户 `admin_backup` 快照，只返回时间、request_id、对象后端、字节数、增量起点和审计哈希；控制台展示最近快照摘要与表格，并纳入 API 契约、deploy check 与 readiness 门禁。
- 对外验收工具链同步应用 API Key：`service_smoke_test.py`、`regression_check.py`、`load_test.py`、`worker_control.py` 支持 `--auth-scheme api-key`，Postman 集合默认使用 `X-Tenant-ID` + `X-API-Key`。
- 新增 `examples/demo-clients/` 两套业务 demo client：Python/tenant-a 与 Node/tenant-b 均默认应用 API Key，可 dry-run 验证 health、models、thresholds、enroll、search、compare 和 video job 接入步骤。
- 接入应用补齐项目级限流与配额字段：`rate_limit_per_minute`、`rate_limit_burst`、`daily_quota` 已接入入口限流，应用级配置可覆盖全局令牌桶并独立执行每日配额。
- 调用日志页从当前会话 payload 升级为服务端 `/v1/access/call-logs` 环形日志，支持按租户、request_id、应用、接口、状态、错误码和时间窗口筛选；调用日志会回写应用调用次数、错误次数、错误率、最近调用和最近错误时间，接入应用状态读写已加全局锁保护。
- 接入中心新增 `/v1/access/error-codes` 稳定错误码目录与控制台“错误码”页，向调用方解释 `detail.code`、HTTP 状态、是否建议重试和运维处理动作，并纳入 API 契约、deploy check 与平台 strict readiness 门禁。
- `frontend/console/views/operations.js` 同步注册 OpenAPI、Webhook、SLO、多模态、评估、发布和审计相关视图目标，避免模块化控制台漏挂新增页面。
- `docs/operations/INTEGRATION_GUIDE.md` 补充 OpenAPI 契约快照要求、Webhook 外层事件体、签名头、幂等去重和重试规则。
- 接入中心二次优化：接入状态读写增加线程锁，调用日志回写应用 `call_count`/`error_count`/`error_rate`/最近调用时间，限流阶段缓存应用 ID 供调用日志复用；控制台应用列表与日志页展示这些排障信号，复制示例改用环境变量占位，Python SDK 示例统一使用 `os.getenv("PORTRAIT_HUB_API_TOKEN")`，避免泄露真实 API Key。
- 模型发布中心补齐 rollout 审计读回：新增 `GET /rollout/audit` 返回最近非 dry-run 切换/灰度/回滚记录，控制台发布中心展示审计表；readiness 新增 `security:rollout_audit_readback`，确保发布审计不再只写不可查。
- 轨迹审阅补齐人工标注与评估数据池：新增 `GET/POST /v1/evaluation/track-reviews`，支持误检、错配、低质量、确认正确和待复核标注；状态按租户隔离并写入 `PORTRAIT_REVIEW_STATE_PATH`，控制台轨迹审阅页可直接录入和查看，评估中心通过 `/v1/evaluation/track-reviews/summary` 展示标注汇总、最近样本和证据索引，通过 `/v1/evaluation/datasets` 展示由标注池派生的动态数据集列表，并通过 `/v1/evaluation/threshold-recommendations` 基于标注池生成只读阈值推荐且不自动应用，readiness 新增 `security:track_review_annotation_pool`。

本轮验收：

```powershell
python -m pytest -q
$env:PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_production_integration_containers.py -q
.\.venv\Scripts\python.exe tools\type_check.py
npm run check
python tools\deploy_check.py --import-app --json
python tools\portrait_production_readiness.py --scope platform --strict
python tools\portrait_production_readiness.py --strict
git diff --check
```

结果：主测试 `483 passed, 4 skipped`；testcontainers 数据栈代理测试 `4 passed`；strict mypy 通过 `158 source files`；Node 检查和部署检查通过；`deploy_check.py --import-app --json` 为 `ok=true`、60 项全绿；平台 strict readiness `ok=true`、`strict_failure_count=0`、180 项全绿；`git diff --check` 无 whitespace error。完整 `portrait_production_readiness.py --strict` 仍按预期阻断在 5 个真实模型能力：appearance、face_detection、face_embedding、gait、pose，不能用 fallback/placeholder 冒充生产能力。

## 2026-07-11 / v0.5.58 批量并发稳定化

本轮在 v0.5.55 的批量异步处理基础上收口并发风险：新增有界并发 helper，限制多图片解码、图库批量检索和批量比对的批内并发；失败时停止启动后续工作并取消进行中任务；批量进度回调改为串行化更新；图片像素上限改为完整加载前显式检查，不再依赖修改 PIL 全局状态。已补充 `tests/test_bounded_batch_concurrency.py`，并在 `.venv` 下通过 `tools/type_check.py` strict mypy 门禁。

## 2026-07-11 / v0.5.55 维护收口

本轮在 0.5.54 后审计升级的基础上，完成测试安全网和补偿路径的二次收口：

- 恢复完整测试与 strict mypy 门禁：修复生产可选依赖导出、模型回归 manifest 类型收窄和控制台模块化后的契约测试观察点。
- 加固图库 enroll、视频任务取消和 retention cleanup 的回滚补偿：route-level hook 重新贯穿对象存储、audit、特征持久化、任务持久化和图库/stream 恢复路径。
- 将批量异步图片读取统一接入 `read_limited_upload()`，避免超大图片先完整读入内存后才被拒绝。
- 同步 `tools/portrait_production_readiness.py` 的静态模板规则，识别新的补偿实现形态，默认生产就绪检查保持 `ok=true`。
- 清理本地缓存产物，减少约 553 MB 工作区缓存噪声。

固定验收结果：`pytest -q` 为 `443 passed, 4 skipped`，`tools/type_check.py`、`tools/deploy_check.py`、`npm run check` 和默认 `portrait_production_readiness.py` 均通过。完整 strict 生产就绪仍保留 5 个真实模型资产接入项：appearance、face detection、face embedding、gait、pose。

## 下一阶段扩展矩阵

| 方向 | 目标 | 验收方式 |
| --- | --- | --- |
| 真实数据栈 | PostgreSQL/pgvector、Qdrant、Redis、S3 或 MinIO 组合跑通端到端写入、检索、队列和对象清理 | 在 staging 启用 `PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS=1`，补跑 testcontainers 集成测试，并增加一轮真实服务 smoke test |
| 模型灰度 | 将 A/B、shadow、canary、rollback 从配置能力推进到可观测发布流程 | 每个候选模型提供 regression manifest、能力声明、哈希校验、灰度指标和回滚演练记录 |
| 控制台模块化 | 继续拆分 API client、状态管理、视图渲染和运维面板配置 | 保持 `/assets/console.config.js` 为环境配置入口，新增视图时必须有 API 契约测试和生产就绪断言 |
| 数据生命周期 | 覆盖增量备份、恢复演练、保留策略和对象存储清理一致性 | 使用 `/v1/admin/backup`、retention cleanup 和对象存储回滚用例生成演练记录，校验导出脱敏和 checksum |
| 运维安全 | 将漏洞门禁、镜像扫描、SBOM、密钥轮换和 GPU OOM 演练前移到发布流程 | 运行 `tools/security_audit.py`、镜像扫描、`portrait_production_readiness.py --strict` 和故障注入 runbook |
| 性能容量 | 明确 p95 延迟、ANN 召回、连接池尺寸、批处理上限和 GPU/CPU fallback 行为 | 对真实租户/模态基数执行 load test、`EXPLAIN (ANALYZE, BUFFERS)` 和检索召回评估 |

## 发布前固定门禁

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\type_check.py
.\.venv\Scripts\python.exe tools\deploy_check.py --skip-node
.\.venv\Scripts\python.exe tools\portrait_production_readiness.py --scope platform --strict
npm run check
```

生产切换前还需要在真实依赖环境中补跑：

```powershell
$env:PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_production_integration_containers.py -q
.\.venv\Scripts\python.exe tools\security_audit.py
.\.venv\Scripts\python.exe tools\portrait_production_readiness.py --strict
```