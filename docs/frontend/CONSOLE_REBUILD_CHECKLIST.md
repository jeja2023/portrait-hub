# 控制台重建行为对照清单

- 冻结日期：2026-07-17
- 交付版本：0.11.1
- 范围：legacy 模板中全部 27 个 data-view 和其写操作
- 正式入口：/ 登录，认证成功后进入 /console；/console/next 仅保留直达验收
- 状态定义：实现完成表示代码与自动化链路已落库；观察中表示必须在生产灰度后收集指标，不能以本地测试代替。
- 通用状态：所有新路由均有加载骨架、空状态、错误横幅、权限路由和直接深链；列表使用服务端数据，不静默回退本地假数据。


> 2026-07-18 / 0.11.1：迁移后补强 deploy_check 与 readiness，确认旧目录、旧灰度变量、旧静态路径、data-view= 属性和 PortraitConsoleModules 不会回归。
## 对照矩阵

| # | 旧版视图 | 新版路由与交互 | API / 关键字段 | 权限 | 决定与状态 | 测试 ID |
|---:|---|---|---|---|---|---|
| 1 | overview | /；状态卡、指标、快捷入口 | GET /ready/deep；GET /metrics；gpu_worker_requests_total、inference_seconds_bucket、gpu_queue_depth | admin:status、metrics:read | 保留并重做；完成 | E2E-ROUTES-02 |
| 2 | vision | /analysis/image；能力选择、上传、摘要、脱敏原始数据 | POST /v1/infer/persons、faces、pose、appearance、gait | infer | 合并为图片分析；完成 | E2E-ROUTES-02 |
| 3 | compare | /compare；人体/人脸/步态/融合/批量，结论与阈值条 | POST /v1/compare/persons、faces、gait、batch | compare | 保留并重做；完成 | E2E-ROUTES-02 |
| 4 | video | /analysis/video；服务端列表、新建、详情、取消 | GET /v1/jobs；POST /v1/jobs/video；GET /v1/jobs/{job_id} | jobs:read、jobs | 合并为视频任务；完成 | test_jobs_collection_is_tenant_scoped_filterable_and_cursor_paginated；E2E-ROUTE-01 |
| 5 | video-results | /analysis/results 结果库；图片/视频/视频流来源筛选、预览卡片与详情抽屉 | GET /v1/analysis/results；results、previews、source_type、mode、next_cursor | infer | 扩展为统一解析结果库；完成 | E2E-ROUTES-02 |
| 6 | streams | /analysis/stream；流列表、注册、启动/停止 | GET/POST /v1/streams；POST /v1/streams/{id}/start、stop | streams:read、streams | 保留并重做；完成 | E2E-ROUTES-02 |
| 7 | access-credentials | /dev/access 应用页签；创建、启停、轮换、一次性密钥 | GET/POST /v1/access/applications；PATCH；POST rotate | access:read、access:write | 合并为接入配置；完成 | E2E-ROUTES-02 |
| 8 | sdk-examples | /dev/playground SDK 示例页签；异步批量/视频示例与复制 | Python/Node search_batch、compareBatch、createVideoJob、jobResult | infer | 并入调试台；完成 | E2E-ROUTES-02；console unit |
| 9 | openapi-docs | /dev/playground OpenAPI 页签；操作表与完整定义链接 | GET /openapi.json；paths | access:read | 并入调试台；完成 | E2E-ROUTES-02 |
| 10 | api-playground | /dev/playground；GET、multipart、JSON 请求构造与脱敏诊断 | models、thresholds、批量检索/比对、streams、stream events | 按目标接口 | 保留并重做；完成 | E2E-ROUTES-02；readiness strict |
| 11 | call-logs | /dev/logs；时间、应用、接口、状态、错误码筛选 | GET /v1/access/call-logs；request_id、created_since、created_until | access:read | 保留并重做；完成 | E2E-ROUTES-02 |
| 12 | error-codes | /dev/playground 错误码页签；说明与处置建议表 | GET /v1/access/error-codes；code、retryable、operator_action | access:read | 并入调试台；完成 | E2E-ROUTES-02 |
| 13 | webhooks | /dev/access Webhook 页签；创建、轮换、样例事件 | GET/POST /v1/access/webhooks；POST rotate、sample | access:read、access:write | 并入接入配置；完成 | E2E-ROUTES-02 |
| 14 | slo-panel | / 总览；30 天成功率、错误预算、推理/排队 p95/p99、GPU 队列 | GET /v1/access/call-logs；GET /metrics | admin:status、metrics:read、access:read（可回退） | 并入总览；完成 | slo.test.ts；readiness strict |
| 15 | multimodal-compare | /compare 多模态融合模式；模态参与/分数/质量/权重/原因表 | POST /v1/fusion/compare；final_score、decision、modalities | compare | 独立页裁撤，能力并入比对；完成 | E2E-ROUTES-02 |
| 16 | track-review | /admin/calibration；合法标签、证据、摘要、评估池 | GET/POST track-reviews；summary；datasets；threshold-recommendations | jobs:read、jobs、thresholds:write | 并入阈值与标注；完成 | test_portrait_review；readiness strict |
| 17 | evaluation-center | /admin/models 评估页签；数据集与阈值建议 | GET /v1/evaluation/datasets、threshold-recommendations | models:read | 并入模型中心；完成 | E2E-ROUTES-02 |
| 18 | release-center | /admin/models 发布页签；默认预演，正式发布二次确认 | GET aliases；POST /v1/admin/models/rollout/aliases/switch | models:read、models:write | 并入模型中心；完成 | E2E-ROUTES-02 |
| 19 | audit-compliance | /admin/ops；事件/结果/类别/时间筛选、摘要、链指纹与错误数 | GET /v1/admin/audit/events；GET /v1/admin/audit/verify | admin:status | 并入运维与合规；完成 | test_api_contract_03；readiness strict |
| 20 | models | /admin/models 模型页签；加载、卸载、别名 | GET /v1/models；POST /v1/models/{id}/load、unload | models:read、models:write | 保留并重做；完成 | E2E-ROUTES-02 |
| 21 | admin-threshold | /admin/calibration 阈值页签；按方案编辑并确认保存 | GET /v1/thresholds；PUT /v1/thresholds/{profile} | models:read、thresholds:write | 并入阈值与标注；完成 | E2E-ROUTES-02 |
| 22 | admin-data | /admin/ops；快照后端/大小/增量起点与高风险清理 | GET /v1/admin/backups；POST backup、retention/cleanup | admin:export、admin:retention | 并入运维与合规；完成 | test_api_contract_03；E2E-ROUTES-02 |
| 23 | alerts | /admin/ops 告警说明与 / 总览实时 SLO | GET /metrics；请求成功率、错误预算、队列与运行状态 | admin:status、metrics:read | 能力并入总览；完成 | slo.test.ts |
| 24 | gallery-enroll | /gallery 注册弹窗；多图与完成后详情 | POST /v1/gallery/enroll；person、feature_count | gallery:write | 并入人员库；完成 | E2E-ROUTES-02 |
| 25 | gallery-search | /search；候选卡、质量警告、人员深链 | POST /v1/gallery/search；template_similarity、decision、feature | gallery:read | 保留为以图搜人；完成 | test_gallery_collection_returns_redacted_summaries_and_supports_search；E2E-ROUTES-02 |
| 26 | gallery-manage | /gallery 与 /gallery/{personId}；列表、详情、名称/元数据编辑、删除 | GET/PATCH/DELETE /v1/gallery/{person_id}；GET /v1/gallery | gallery:read、gallery:write | 保留并重做；完成 | test_gallery_collection_returns_redacted_summaries_and_supports_search；E2E-ROUTES-02 |
| 27 | gallery-rebuild | /gallery 高级操作；先 dry_run 预演，再文本二次确认 | POST /v1/gallery/reindex?dry_run=true/false；matched_feature_count、failed_feature_count | gallery:write | 独立页裁撤，能力并入人员库；完成 | E2E-ROUTES-02 |

## 横切契约

| 契约 | 验收结果 | 自动化 |
|---|---|---|
| 静态壳匿名可取，租户数据必须鉴权 | 完成 | test_public_console_shell_does_not_expose_tenant_data；E2E-SHELL-01 |
| /v1/console/me 服务端驱动主体与权限，不要求 admin:status、不返回 legacy features | 完成 | test_console_me_returns_principal_capabilities |
| 任务与人员集合租户隔离、脱敏和游标分页 | 完成 | test_jobs_collection_is_tenant_scoped_filterable_and_cursor_paginated；test_gallery_collection_returns_redacted_summaries_and_supports_search |
| 非法游标稳定返回 422 | 完成 | test_console_collections_reject_invalid_cursors |
| WS ticket 单次、过期、租户与资源绑定 | 完成 | test_console_ws_ticket_is_resource_bound_and_single_use；test_console_ws_tickets_reject_wrong_binding_and_expiration |
| API Key/JWT 不进入 localStorage 或 URL | 完成 | E2E-SHELL-01；session.test.ts |
| 原始数据递归脱敏 | 完成 | redact.test.ts |
| CSP 无脚本放宽且运行期零违规 | 完成 | E2E-SHELL-01；E2E-ROUTES-02 |
| 直接深链登录后回到原路由 | 完成 | E2E-ROUTE-01 |
| 桌面/平板/移动及 Firefox/WebKit | 完成 | Playwright 五项目矩阵 |
| axe serious/critical 为 0 | 完成 | E2E-SHELL-01 |
| lint、unit、type、build、OpenAPI 差异门禁 | 完成 | .github/workflows/ci.yml |

## 写操作与确认矩阵

| 操作 | 风险级别 | 新版保护 |
|---|---|---|
| 视频任务取消、人员删除、阈值保存、备份创建 | 普通危险 | DangerConfirm，显示资源与影响 |
| 应用/Webhook 密钥轮换 | 敏感 | 确认后执行；新密钥只展示一次并可复制 |
| 正式模型发布 | 高风险 | 必须先预演，再输入“正式发布” |
| 人员特征重建 | 高风险 | 必须先 dry_run，再输入“重建特征” |
| 保留策略数据清理 | 高风险、不可逆 | 显示租户/天数/影响，输入“清理数据” |
| 模型加载/卸载、应用启停、流启动/停止 | 可逆运维 | 行内明确命令、服务端权限与审计 |

## 本轮自动化验证

- `npm run console:typecheck`：通过。
- `npm run console:check`：通过，覆盖 ESLint、Vitest 4 files / 10 tests、Vue TypeScript 和 Vite production build。
- `npm run console:e2e`：通过，Playwright 11 passed / 4 skipped。
- `npm test`：通过，Node SDK 契约 + Console Next 静态链路。
- `python -m pytest -q tests/test_api_contract_01_config_console.py tests/test_api_contract_04_runtime_models.py tests/test_deploy_check.py tests/test_portrait_production_readiness.py tests/test_console_next_contracts.py`：48 passed，2 warnings。
- `python tools/deploy_check.py --json --import-app`：ok=true，确认旧源码目录缺失和 Console Next 源码无重复 `/v1` 前缀。
- `python tools/portrait_production_readiness.py --scope platform --strict`：ok=true，strict_failure_count=0。

## 生产上线待办

以下事项依赖真实发布环境，不以代码门禁结果代替：

- [ ] 产品、安全、运维负责人完成 0.11.1 上线签字。
- [ ] 使用上一版镜像或受控静态构件完成回退演练，验证业务数据和解析档案不变。
- [ ] 记录真实登录、前端异常、API 4xx/5xx、关键流程成功率和 p95/p99 观察结果。
- [ ] 多副本部署前将 WS ticket 迁移到共享、带 TTL、原子单次消费存储并补并发测试。
- [ ] 在可访问镜像仓库的环境完成 GPU/CPU Docker 镜像构建。
- [x] frontend/console、/console/legacy、旧静态资产、CONSOLE_DEFAULT_VERSION 和角色域灰度开关已删除。
- [x] /、/console、/console/next 已统一指向 Console Next。
- [x] 平台严格 readiness 的 11 个遗留项已全部关闭。