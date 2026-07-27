# 平台性能、配置健壮性与交付优化计划

版本：`0.18.1`

日期：2026-07-27

本文档归档本轮全项目静态检查的结论与修复状态。检查覆盖后端 Python（313 文件 / 69316 行）、前端
TypeScript 与 Vue（63 文件 / 40353 行）、部署清单（Docker / docker-compose / Kubernetes）、CI 工作流
与 95 个测试文件（765 个用例）。

状态说明：

- 已完成：代码已落地，等待随版本发布。
- 部分完成：主体已落地，仍有配套项（文档、样例、门禁）待补。
- 后续计划：本轮记录并排序，需要破坏性迁移或真实生产数据验证，不作为本版本阻断项。

## 结论摘要

项目工程成熟度高：mypy strict、ruff、OpenAPI 契约兼容门禁、生产就绪门禁、Kubernetes 安全基线均已到位，
零 TODO/FIXME。本轮真正值得处理的问题集中在**控制面持久化的写放大与串行化**——它们在当前数据量下不
明显，但随租户与实体数增长会非线性恶化。

## 高优先级

| 项目 | 状态 | 本轮处理 |
| --- | --- | --- |
| 控制面实体表每次写操作全量重建 | 已完成 | `_sync_control_entities` 原先 `DELETE` 整个 `state_key` 后 `executemany` 重插全部行，导致改一个租户的一个字段就要重建该域全部实体行及其三个索引（含 GIN `jsonb_path_ops`）。现改为 `ON CONFLICT ... DO UPDATE ... WHERE (...) IS DISTINCT FROM (...)` 增量 UPSERT，内容未变的行不产生写入；再用并行 `unnest` anti-join 删除快照中已消失的行。写成本从 O(全域实体数) 降到 O(变更数)。 |
| 全局锁内做 Postgres 往返 | 已完成 | `ControlStateBackend.operation()` 原先在持有全局 `RLock` 期间调用 `refresh()`，而 `with _LOCK` 在 8 个模块共 100 处，使所有控制面读写在一把锁上串行、每个请求持锁期间还要等一次网络 RTT。现拆出 `_load_snapshot()` / `_apply_snapshot()`，快照读取移到锁外预取，入锁后仅按 revision 应用。写入正确性仍由 `save()` 的 revision 乐观锁兜底，预取值过期只会触发既有的 409 重试语义。 |
| 阻塞 IO 与推理共用默认线程池 | 已完成 | 全项目此前无任何 executor 配置，371 处 `run_blocking_io`（控制面持久化、状态落盘、审计写入）与 28 处 `asyncio.to_thread`（ONNX `run_session`、图像 letterbox、视频解码）共用 asyncio 默认线程池（`min(32, CPU+4)`），而服务以 `--workers 1 --limit-concurrency 100/200` 运行。现新增 `portrait-io` 专用线程池（`io_executor()`），`run_blocking_io` 改走该池，默认池留给推理与媒体解码；`shutdown_io_executor()` 已接入 lifespan 停机流程，在统计刷盘之后关闭；`.env.example` 已补 `BLOCKING_IO_THREAD_POOL_SIZE=32` 及调优说明。 |
| 控制面快照按域单行存储 | 后续计划 | `portrait_control_state` 为 `state_key TEXT PRIMARY KEY`，整个 commercial 域的所有租户共享一行 JSONB 与一个全局 revision，因此不同租户的并发写也会互相 409 冲突。建议主键改为 `(state_key, tenant_id)`、revision 按租户独立；终态可以 `portrait_control_entities` 为写权威、快照降级为只读缓存。需要破坏性 schema 迁移与在线迁移路径，单独排期。 |
| embedding 三重存储写放大 | 后续计划 | `portrait_features` 仍同时保存 `embedding(BYTEA JSON)`、`embedding_json(JSONB)`、`embedding_vector`。1024 维粗估约 28KB/条，而检索实际只需 4KB，约 7 倍存储与写入放大。沿用 `SECURITY_RUNTIME_AUDIT_FIX_PLAN` 的判断：先确认兼容消费者，再设计单一主存储与在线迁移。特征库最容易长到千万级，建议在分片快照之后优先排期。 |

## 中优先级

| 项目 | 状态 | 本轮处理 |
| --- | --- | --- |
| 配置非法值静默回退 | 已完成 | `parse_int_env`、`parse_float_env` 与 `parse_bool_env` 现对非法值记录去重 WARN 并汇总变量名与期望类型；浮点数同时拒绝 `NaN`/无穷值，布尔值显式区分真、假白名单。开发环境仍按默认值启动，生产 profile 在启动门禁汇总报错并 fail-fast，且不受 `PRODUCTION_EXTERNAL_SERVICES_REQUIRED` 开关绕过。日志不记录原始值，避免误泄露配置内容。 |
| Dockerfile 硬编码国内镜像源 | 已完成 | GPU 镜像的 apt 源改为可选 `ARG APT_MIRROR`，留空时沿用基础镜像官方源；GPU/CPU 镜像的 pip 源改为 `ARG PIP_INDEX_URL=https://pypi.org/simple`。国内或隔离网络构建可按需传入构建参数，仓库交付门禁同时禁止重新写死清华源。 |
| CI 单 job 串行 | 已完成 | `ci.yml` 已拆为 `lint-type`、`python-tests`、`frontend-e2e`、`delivery-gates` 四个无依赖并行 job。Playwright 缓存 `~/.cache/ms-playwright`，CI 只安装 Chromium 并通过专用脚本运行 desktop/tablet/mobile；单个 E2E job 因共享一个服务和运行时目录而使用单 worker，避免登录态和 JSON 状态互相覆盖。交付门禁同时检查并行 job、项目集合、Console 构建顺序和 E2E 隔离。 |

## 低优先级与清理

| 项目 | 状态 | 本轮处理 |
| --- | --- | --- |
| GPU 探测失败静默降级 | 已完成 | `runtime_state.py` 的动态 GPU 设备发现异常现在记录 warning（仅包含异常类型，不输出可能敏感的异常正文），随后保持原有配置设备或 `[0]` 降级语义。 |
| 巨型模块 | 后续计划 | `portrait_commercial.py` 2241 行、`portrait_access.py` 1368 行、`routes_portrait_commercial.py` 1266 行；前端 `IdentityView.vue` 1217 行、`ConfigurationView.vue` 774 行、`CommercialView.vue` 768 行。建议在控制面分片快照重构时按领域（profile / entitlement / SLA / compliance）顺势拆分。 |
| `generated.ts` 单文件 25413 行 | 后续计划 | 自动生成产物，当前可接受；若 IDE 响应明显变慢，可按 OpenAPI tag 拆分输出。 |

## 已核查、确认无需处理

以下为本轮重点排查但结论为"设计正确"的项，记录以避免重复审计：

- **事件循环阻塞**：371 处 `run_blocking_io` 覆盖 29 个文件、几乎全部路由，推理预处理统一走 `asyncio.to_thread`。
- **SQL 注入**：全库仅 2 处 f-string 拼接 SQL（`portrait_analysis_archive.py`、`postgres_analysis_archive.py`），拼接的都是硬编码条件片段，值全部参数绑定。
- **多副本状态分叉**：`production_gates.py` 强制生产 `PORTRAIT_STORAGE_BACKEND=postgres`，与 `ControlStateBackend.postgres_enabled()` 是同一开关，配合 revision 乐观锁与 409 语义，多副本一致性成立。
- **吞异常**：4 处 `except Exception: pass` 全部合理——JSON keyring 失败退回 `k=v` 解析（且 `HTTPException` 显式重抛）、`isoformat()` 失败退回类型占位、两处 finally 清理临时文件。GPU 探测降级已改为记录 warning。
- **硬编码密钥**：零命中。
- **容器健康检查**：`Dockerfile` 无 `HEALTHCHECK`，但 docker-compose 与 Kubernetes 三探针均已覆盖。
- **前端首屏**：路由已全量动态 `import` 懒加载。
- **Kubernetes 安全基线**：非 root、seccomp RuntimeDefault、drop ALL caps、readOnlyRootFilesystem、镜像 digest 固定、preStop + 60s 优雅期，均已到位。

## 完成清单与后续排期

1. 已完成：`.env.example` 补充 `BLOCKING_IO_THREAD_POOL_SIZE` 条目（A3 收尾）。
2. 已完成：`settings.py` 非法值 WARN、汇总与生产 fail-fast。
3. 已完成：`Dockerfile` / `Dockerfile.cpu` 镜像源参数化及回归门禁。
4. 已完成：`ci.yml` 拆分四个并行 job，增加 Playwright 缓存并收敛到 Chromium。
5. 已完成：`runtime_state.py` GPU 探测降级告警。
6. 已排期：破坏性数据迁移按以下顺序进入独立迁移窗口，本版本不改现有 schema。

| 阶段 | 工作项 | 启动条件 | 完成标准 |
| --- | --- | --- | --- |
| M1（下一数据库迁移窗口） | 控制面快照按 `(state_key, tenant_id)` 分片，revision 按租户独立 | 完成双写/回填/校验/回滚方案，并用生产规模副本压测 409 冲突率 | 在线迁移无停机；新旧快照逐租户校验一致；可观测冲突率、回填延迟与回滚结果；稳定窗口结束后停止旧单行写入 |
| M2（M1 稳定后） | embedding 收敛为单一主存储 | 完成所有兼容消费者盘点，确定 pgvector/序列化兼容边界与分批回填容量 | 双读校验达到约定一致率；检索召回与延迟基线不回退；旧列停止写入并在独立清理窗口删除 |
| M3（随 M1/M2 实施） | 拆分巨型控制面模块与生成代码评估 | M1 领域边界稳定；IDE/构建数据证明 `generated.ts` 已构成瓶颈 | profile/entitlement/SLA/compliance 所有权边界清晰；仅在有量化收益时按 OpenAPI tag 拆分生成物 |

## 0.18.3 交付回归修复

- 修复 GPU/CPU Dockerfile 复制 `.dockerignore` 已排除 `tools/` 的构建上下文冲突，并将该契约加入部署门禁。
- Trivy 与 Scorecard 的 SARIF 上传增加文件存在保护，保留构建或扫描步骤的原始失败状态。
- GitHub 官方 Checkout、Python/Node 设置、缓存、构件上传下载和 CodeQL SARIF Action 升级到当前 Node.js 24 运行时主版本。
- Python 测试与交付门禁在检查静态产物前执行 Console production build，消除本地遗留 `dist` 掩盖干净检出失败的问题。
- Playwright 共享状态矩阵改为单 worker，CI 项目写入专用脚本；移动端配置验收同步到 0.18.2 已统一的标准表格 DOM。
- 模型注册测试改用隔离小构件；对象存储原子临时名缩短，深层 Windows 工作区不再因临时路径超长失败。
- Trivy 只忽略上游暂无修复版本的漏洞，已有修复方案的 `HIGH/CRITICAL` 条目继续阻断发布。

## 验证记录

- `python -m ruff check app tools tests`：通过。
- `python tools/type_check.py`：通过，215 个源文件无问题。
- 计划指定的控制面回归：39 passed；新增配置、GPU、增量同步与交付检查定向回归合计 47 passed。
- 完整 Python 测试：760 passed、6 skipped，覆盖率 77%；跳过项为需要外部环境的既有用例。
- `npm run check`：Node SDK 通过；前端 lint/typecheck/build 通过，Vitest 45 passed。
- `python tools/deploy_check.py --json --skip-node`：通过，包含 Docker 镜像源参数化与 CI 四 job/Chromium 缓存新门禁。
- `npm run console:e2e:ci`：通过，Chromium 桌面/平板/移动端 `24 passed`，单 worker 无登录态或共享状态竞态。
- 控制面新增用例覆盖条件 UPSERT SQL、快照实体消失后的投影清理，以及 409 冲突刷新后重试成功。
- Docker BuildKit `--check` 已尝试，但本机 Docker Desktop 的失效代理 `127.0.0.1:10808` 阻断基础镜像元数据读取，未进入 Dockerfile 构建阶段；需在可访问镜像仓库的 CI/构建机补跑。
- CI 持续覆盖 ruff、pytest+coverage、mypy strict、OpenAPI 契约兼容、前端 lint/test/typecheck/build、Chromium e2e、SDK 冒烟、部署契约与生产就绪门禁。
