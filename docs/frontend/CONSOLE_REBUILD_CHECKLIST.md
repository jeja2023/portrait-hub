# 控制台重建行为对照清单

- 冻结日期：2026-07-17
- 交付版本：0.10.0
- 范围：legacy 模板中全部 27 个 data-view 和其写操作
- 正式入口：/ 登录，认证成功后进入 /console；/console/next 仅保留直达验收
- 状态定义：实现完成表示代码与自动化链路已落库；观察中表示必须在生产灰度后收集指标，不能以本地测试代替。
- 通用状态：所有新路由均有加载骨架、空状态、错误横幅、权限路由和直接深链；列表使用服务端数据，不静默回退本地假数据。

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
| 8 | sdk-examples | /dev/playground SDK 示例页签 | 仓库 SDK 公共调用约定 | infer | 并入调试台；完成 | E2E-ROUTES-02 |
| 9 | openapi-docs | /dev/playground OpenAPI 页签；操作表与完整定义链接 | GET /openapi.json；paths | access:read | 并入调试台；完成 | E2E-ROUTES-02 |
| 10 | api-playground | /dev/playground 调试页签；端点选择与脱敏响应 | GET /v1/models、thresholds、admin/status、access/error-codes | 按目标接口 | 保留并重做；完成 | E2E-ROUTES-02 |
| 11 | call-logs | /dev/logs；时间、应用、接口、状态、错误码筛选 | GET /v1/access/call-logs；request_id、created_since、created_until | access:read | 保留并重做；完成 | E2E-ROUTES-02 |
| 12 | error-codes | /dev/playground 错误码页签；说明与处置建议表 | GET /v1/access/error-codes；code、retryable、operator_action | access:read | 并入调试台；完成 | E2E-ROUTES-02 |
| 13 | webhooks | /dev/access Webhook 页签；创建、轮换、样例事件 | GET/POST /v1/access/webhooks；POST rotate、sample | access:read、access:write | 并入接入配置；完成 | E2E-ROUTES-02 |
| 14 | slo-panel | /admin/ops 平台状态/告警页签 | GET /v1/admin/status；GET /metrics | admin:status、metrics:read | 并入运维与合规；完成 | E2E-ROUTES-02 |
| 15 | multimodal-compare | /compare 多模态融合模式；模态参与/分数/质量/权重/原因表 | POST /v1/fusion/compare；final_score、decision、modalities | compare | 独立页裁撤，能力并入比对；完成 | E2E-ROUTES-02 |
| 16 | track-review | /admin/calibration 轨迹审阅页签；标注表单与列表 | GET/POST /v1/evaluation/track-reviews | models:read、thresholds:write | 并入阈值与标注；完成 | E2E-ROUTES-02 |
| 17 | evaluation-center | /admin/models 评估页签；数据集与阈值建议 | GET /v1/evaluation/datasets、threshold-recommendations | models:read | 并入模型中心；完成 | E2E-ROUTES-02 |
| 18 | release-center | /admin/models 发布页签；默认预演，正式发布二次确认 | GET aliases；POST /v1/admin/models/rollout/aliases/switch | models:read、models:write | 并入模型中心；完成 | E2E-ROUTES-02 |
| 19 | audit-compliance | /admin/ops 审计页签；事件表与审计链校验 | GET /v1/admin/audit/events；GET /v1/admin/audit/verify | admin:status、admin:export | 并入运维与合规；完成 | E2E-ROUTES-02 |
| 20 | models | /admin/models 模型页签；加载、卸载、别名 | GET /v1/models；POST /v1/models/{id}/load、unload | models:read、models:write | 保留并重做；完成 | E2E-ROUTES-02 |
| 21 | admin-threshold | /admin/calibration 阈值页签；按方案编辑并确认保存 | GET /v1/thresholds；PUT /v1/thresholds/{profile} | models:read、thresholds:write | 并入阈值与标注；完成 | E2E-ROUTES-02 |
| 22 | admin-data | /admin/ops 备份保留页签；备份与高风险清理 | GET /v1/admin/backups；POST /v1/admin/backup、retention/cleanup | admin:export、admin:retention | 并入运维与合规；完成 | E2E-ROUTES-02 |
| 23 | alerts | /admin/ops 告警页签 | GET /metrics；请求错误率、队列与运行状态 | admin:status、metrics:read | 并入运维与合规；完成 | E2E-ROUTES-02 |
| 24 | gallery-enroll | /gallery 注册弹窗；多图与完成后详情 | POST /v1/gallery/enroll；person、feature_count | gallery:write | 并入人员库；完成 | E2E-ROUTES-02 |
| 25 | gallery-search | /search；候选卡、质量警告、人员深链 | POST /v1/gallery/search；template_similarity、decision、feature | gallery:read | 保留为以图搜人；完成 | test_gallery_collection_returns_redacted_summaries_and_supports_search；E2E-ROUTES-02 |
| 26 | gallery-manage | /gallery 与 /gallery/{personId}；列表、详情、名称/元数据编辑、删除 | GET/PATCH/DELETE /v1/gallery/{person_id}；GET /v1/gallery | gallery:read、gallery:write | 保留并重做；完成 | test_gallery_collection_returns_redacted_summaries_and_supports_search；E2E-ROUTES-02 |
| 27 | gallery-rebuild | /gallery 高级操作；先 dry_run 预演，再文本二次确认 | POST /v1/gallery/reindex?dry_run=true/false；matched_feature_count、failed_feature_count | gallery:write | 独立页裁撤，能力并入人员库；完成 | E2E-ROUTES-02 |

## 横切契约

| 契约 | 验收结果 | 自动化 |
|---|---|---|
| 静态壳匿名可取，租户数据必须鉴权 | 完成 | test_public_console_shell_does_not_expose_tenant_data；E2E-SHELL-01 |
| /v1/console/me 服务端驱动权限与三个 feature flag | 完成 | test_console_me_returns_server_driven_capabilities |
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

## 灰度后待完成项

以下事项依赖真实生产流量，不得在代码评审中勾为完成：

- [ ] 内部租户、5%、25%、50%、100% 各级指标记录与负责人签字。
- [ ] 新旧版本同租户登录成功率、前端异常率、API 4xx/5xx、主流程成功率和 P95/P99 对比。
- [ ] 回退演练记录：关闭三个能力 flag 后，/console 恢复 legacy 且业务数据无变更。
- [ ] 至少两个发布周期、全量连续 14 天稳定、零 P0/P1。
- [ ] 多副本部署前将 WS ticket 迁移到共享原子消费存储并补并发消费测试。
- [ ] 产品、安全、运维共同批准删除 frontend/console 与 legacy 门禁项。
