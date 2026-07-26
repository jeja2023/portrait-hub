# 商业控制面数据与迁移

商业控制面使用 `tools/postgres_migrations/` 中按四位版本号排序的只增迁移。`tools/portrait_postgres_migrate.py` 记录文件名、SHA-256、执行人和耗时，使用 PostgreSQL 会话级 advisory lock 串行化迁移，并让每个迁移在独立事务中提交。已应用文件发生名称或摘要漂移、数据库存在当前发布未知的更高版本、PostgreSQL 低于 15 或存在待执行迁移时，发布预检失败。

## 物理边界

`0001_commercial_control_plane.sql` 是 expand-only 迁移，覆盖模型注册/评估/审批/发布、复核与标注交换、数据集 manifest、客户档案与权益、用量和成本、SLA/事故、COM-001~012、权利请求、证据包、行业模板、支持工单及 outbox。业务唯一约束均包含 `tenant_id/project_id`，制品和报告记录摘要，聚合结论记录 `definition_version`，并通过外键和检查约束限制状态与删除行为。

`0002_control_entity_projection.sql` 增加 `portrait_control_entities`。生产 API 以 `portrait_control_state` 的版本化聚合快照为权威写模型，并使用 revision CAS 阻止多副本静默覆盖；每次快照提交会在同一 PostgreSQL 事务内重建实体投影。投影保存 `state_key/collection_name/tenant_id/project_id/entity_id/entity_version`、状态、生命周期、请求/审计关联和原始 JSONB，供按租户/项目查询、到期扫描和一致性对账。快照或投影任一写入失败时整个事务回滚，不允许只成功一侧。

`portrait_model_*` 等领域表保留为详细物理契约和后续读模型，不是当前 API 的第二套权威写路径。发布一致性工具以聚合快照与 `portrait_control_entities` 的逐集合计数、档案/权益指针和到期状态为准；禁止通过直接修改投影修复业务状态，修复必须调用控制面 API 后由同一事务重新生成投影。

主要状态流转：

- 模型：`draft -> candidate -> shadow -> canary -> active -> deprecated`，任一发布阶段可进入 `blocked`，回滚产生独立发布事件而不改写历史。
- 权益：`pending -> active -> superseded|expired|revoked`，每个租户/项目最多一个 active 权益。
- 客户：`trial -> active -> grace -> suspended -> offboarding -> closed`，逆向恢复必须有新的批准与审计事件。
- outbox：`pending|failed -> delivering -> delivered`，超过重试策略进入 `dead_letter`，不得静默丢弃。

索引以租户/项目为首列，随后是状态、时间或主要检索键。正式交付前必须在目标数据量上保存 `EXPLAIN (ANALYZE, BUFFERS)`，重点覆盖模型版本/评估时间线、活跃权益、用量日期范围、SLA 窗口、事故状态、权利请求期限和 outbox 拉取；缺少目标环境 EXPLAIN 证据时支持矩阵保持 `limited`。

## 增长与归档

- `portrait_usage_daily_summary` 只保存日级聚合，在线保留 24 个月；原始调用日志按合同保留并进入既有归档链路。
- 模型发布、评估、SLA、事故、合规和证据索引在线保留当前合同期加审计窗口；大报告和媒体只保留对象 key 与摘要。
- outbox 已投递记录在线保留 30 天，之后导出带摘要的审计归档；`dead_letter` 在人工结案前不得清理。
- 当任一大表预计超过 5000 万行或单索引超过 100 GiB 时，新增分区表作为下一次 expand 迁移，按月分区时间序列表；禁止直接把现有表原地改造成分区表。
- 批量回填默认每批不超过 5000 行，`lock_timeout` 2 秒、`statement_timeout` 30 秒；复制延迟超过 5 秒、数据库 CPU 超过 70% 或错误预算告警时暂停。所有阈值可向下调整，向上调整需 SRE 批准。

## Expand、回填、切换与回滚

1. Expand：创建新表/列/索引和兼容读取路径，不删除或重命名旧结构；先备份并保存恢复点。
2. Backfill：按稳定主键游标分批复制，保存批次起止、行数、摘要和失败重试；源/目标总数、分租户计数和摘要全部一致后才可继续。
3. Dual write：通过同一事务或 outbox 写新旧结构，监控重复、延迟和失败；不得在应用层先写一边再无记录地写另一边。
4. Switch：灰度切换读取，按租户对账并保留旧读取开关；观察窗口覆盖至少一个完整业务峰值。
5. Contract：只在 N-1 回滚窗口结束、审计与客户批准完成后用后续迁移删除旧结构。`0001` 不包含 contract 操作。
6. Rollback：应用回滚只切回旧读取/写入路径并保留新结构；若新语义已产生旧版本无法理解的数据，恢复升级前快照并明确数据损失范围，禁止让旧版本继续写。

## 一致性与安全修复

```powershell
python tools/portrait_commercial_consistency.py --json
python tools/portrait_commercial_consistency.py --apply-safe-repairs `
  --confirmation APPLY-SAFE-REPAIRS --actor sre-owner --json
```

只读检查验证迁移历史、客户档案/active 权益指针、过期 active 数据、stale/dead-letter outbox，以及对象存储、向量后端和 Redis 队列健康。安全修复只允许将唯一 active 权益回填到客户档案，并把卡住 15 分钟以上的 delivering outbox 重新标为 failed 等待重试；它不删除数据、不自动接受 dead-letter，也不伪造跨存储对账结果。

权利删除可保留的最小审计信息仅限：不可逆请求标识摘要、租户/项目、请求类型、批准人、执行时间、覆盖后端、结果摘要和审计链哈希。不得保留原始媒体、特征向量、主体直接标识、长期 URL 或已删除对象内容。
