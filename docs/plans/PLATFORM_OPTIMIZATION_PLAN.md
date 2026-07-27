# 平台性能、配置健壮性与交付优化计划

版本：`0.18.0`

日期：2026-07-27

本文档归档本轮全项目静态检查的结论与修复状态。检查覆盖后端 Python（313 文件 / 69316 行）、前端
TypeScript 与 Vue（63 文件 / 40353 行）、部署清单（Docker / docker-compose / Kubernetes）、CI 工作流
与 94 个测试文件（739 个用例）。

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
| 阻塞 IO 与推理共用默认线程池 | 部分完成 | 全项目此前无任何 executor 配置，371 处 `run_blocking_io`（控制面持久化、状态落盘、审计写入）与 28 处 `asyncio.to_thread`（ONNX `run_session`、图像 letterbox、视频解码）共用 asyncio 默认线程池（`min(32, CPU+4)`），而服务以 `--workers 1 --limit-concurrency 100/200` 运行。现新增 `portrait-io` 专用线程池（`io_executor()`），`run_blocking_io` 改走该池，默认池留给推理与媒体解码；`shutdown_io_executor()` 已接入 lifespan 停机流程，在统计刷盘之后关闭。**待补**：`.env.example` 中 `BLOCKING_IO_THREAD_POOL_SIZE` 条目。 |
| 控制面快照按域单行存储 | 后续计划 | `portrait_control_state` 为 `state_key TEXT PRIMARY KEY`，整个 commercial 域的所有租户共享一行 JSONB 与一个全局 revision，因此不同租户的并发写也会互相 409 冲突。建议主键改为 `(state_key, tenant_id)`、revision 按租户独立；终态可以 `portrait_control_entities` 为写权威、快照降级为只读缓存。需要破坏性 schema 迁移与在线迁移路径，单独排期。 |
| embedding 三重存储写放大 | 后续计划 | `portrait_features` 仍同时保存 `embedding(BYTEA JSON)`、`embedding_json(JSONB)`、`embedding_vector`。1024 维粗估约 28KB/条，而检索实际只需 4KB，约 7 倍存储与写入放大。沿用 `SECURITY_RUNTIME_AUDIT_FIX_PLAN` 的判断：先确认兼容消费者，再设计单一主存储与在线迁移。特征库最容易长到千万级，建议在分片快照之后优先排期。 |

## 中优先级

| 项目 | 状态 | 本轮处理 |
| --- | --- | --- |
| 配置非法值静默回退 | 待实施 | `settings.py` 未导入 logging，`parse_int_env` / `parse_float_env` 解析失败时 `except ValueError: return default`，`parse_bool_env` 任何非白名单值直接变 `False`。在 232 个配置常量 / 238 个环境变量的规模下，`MAX_IMAGE_BYTES=10MB` 或 `true` 拼成 `ture` 都会静默按默认值启动。计划：非法值记 WARN 并汇总，生产 profile 下 fail-fast（可挂进 `production_gates.py`）。 |
| Dockerfile 硬编码国内镜像源 | 待实施 | `Dockerfile` 的 apt sources.list 与 pip `-i`、`Dockerfile.cpu` 的 pip `-i` 均写死 `mirrors.tuna.tsinghua.edu.cn`，交付到海外或隔离网络客户时构建会变慢甚至失败。计划：改 `ARG APT_MIRROR` / `ARG PIP_INDEX_URL`，默认官方源，国内构建按需传入。 |
| CI 单 job 串行 | 待实施 | `ci.yml` 单个 job 约 20 个步骤、`timeout-minutes: 25`，包含安装三个 playwright 浏览器、前端 build、e2e、pytest+coverage、mypy 与六个门禁脚本，无并行 job、无浏览器缓存。计划：拆为 lint+type / python tests / frontend+e2e / 交付门禁四个并行 job，缓存 `~/.cache/ms-playwright`，e2e 按需只装 chromium。 |

## 低优先级与清理

| 项目 | 状态 | 本轮处理 |
| --- | --- | --- |
| GPU 探测失败静默降级 | 待实施 | `runtime_state.py` 中 GPU 设备探测异常时回退到设备 `[0]` 且无日志，运维不易发现。建议补一条 warning。 |
| 巨型模块 | 后续计划 | `portrait_commercial.py` 2241 行、`portrait_access.py` 1368 行、`routes_portrait_commercial.py` 1266 行；前端 `IdentityView.vue` 1217 行、`ConfigurationView.vue` 774 行、`CommercialView.vue` 768 行。建议在控制面分片快照重构时按领域（profile / entitlement / SLA / compliance）顺势拆分。 |
| `generated.ts` 单文件 25413 行 | 后续计划 | 自动生成产物，当前可接受；若 IDE 响应明显变慢，可按 OpenAPI tag 拆分输出。 |

## 已核查、确认无需处理

以下为本轮重点排查但结论为"设计正确"的项，记录以避免重复审计：

- **事件循环阻塞**：371 处 `run_blocking_io` 覆盖 29 个文件、几乎全部路由，推理预处理统一走 `asyncio.to_thread`。
- **SQL 注入**：全库仅 2 处 f-string 拼接 SQL（`portrait_analysis_archive.py`、`postgres_analysis_archive.py`），拼接的都是硬编码条件片段，值全部参数绑定。
- **多副本状态分叉**：`production_gates.py` 强制生产 `PORTRAIT_STORAGE_BACKEND=postgres`，与 `ControlStateBackend.postgres_enabled()` 是同一开关，配合 revision 乐观锁与 409 语义，多副本一致性成立。
- **吞异常**：5 处 `except Exception: pass` 全部合理——JSON keyring 失败退回 `k=v` 解析（且 `HTTPException` 显式重抛）、`isoformat()` 失败退回类型占位、GPU 探测降级、两处 finally 清理临时文件。
- **硬编码密钥**：零命中。
- **容器健康检查**：`Dockerfile` 无 `HEALTHCHECK`，但 docker-compose 与 Kubernetes 三探针均已覆盖。
- **前端首屏**：路由已全量动态 `import` 懒加载。
- **Kubernetes 安全基线**：非 root、seccomp RuntimeDefault、drop ALL caps、readOnlyRootFilesystem、镜像 digest 固定、preStop + 60s 优雅期，均已到位。

## 待办清单

1. 补 `.env.example` 的 `BLOCKING_IO_THREAD_POOL_SIZE` 条目（A3 收尾）。
2. `settings.py` 增加非法值 WARN 与生产 fail-fast。
3. `Dockerfile` / `Dockerfile.cpu` 镜像源参数化。
4. `ci.yml` 拆分并行 job 与 playwright 缓存。
5. `runtime_state.py` GPU 探测降级补日志。
6. 排期：控制面快照按租户分片；embedding 单一主存储迁移。

## 验证记录

- 本轮改动尚未在本地执行 `ruff` / `mypy` / `pytest`（执行环境受限）。合入前需补跑：
  - `python -m ruff check app tools tests`
  - `python tools/type_check.py`
  - `python -m pytest tests/test_postgres_control_state.py tests/test_control_state_backend.py tests/test_commercial_control_plane.py tests/test_bounded_batch_concurrency.py -q`
- 控制面增量同步与锁外预取涉及并发语义，建议补充针对性用例：同一实体重复保存不产生行写入、快照删除实体后投影表同步清理、并发写触发 409 后重试成功。
- CI 已覆盖 ruff、pytest+coverage、mypy strict、OpenAPI 契约兼容、前端 lint/test/typecheck/build、e2e、SDK 冒烟、部署契约与生产就绪门禁。
