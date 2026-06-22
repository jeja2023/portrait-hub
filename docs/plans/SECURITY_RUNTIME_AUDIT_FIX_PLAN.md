# 安全、运行时与数据路径审计收口计划

版本：`0.5.36`

日期：2026-06-21

本文档归档本轮静态检查和推理运行时、数据存储、路由安全三条审计线的修复状态。状态说明：

- 已完成：代码已落地，并已纳入本次版本说明。
- 部分完成：已完成风险缓解或短期修复，但仍需要生产数据、真实依赖或更大重构验证。
- 后续计划：本轮记录并排序，暂不作为 `0.5.36` 发布阻断项。

## 高优先级

| 项目 | 状态 | `0.5.36` 处理 |
| --- | --- | --- |
| 限流可被 `x-tenant-id` 绕过 | 已完成 | 限流 key 改为匿名按客户端 IP / 可信 `X-Forwarded-For`，认证请求按 API Token 或 JWT 身份；新增伪造租户头测试。 |
| `GET /ready` 未鉴权泄露依赖明细 | 已完成 | `/ready` 保留公开探活，但依赖、对象存储、任务队列和磁盘明细只对已鉴权调用返回；匿名失败只返回 `not_ready`。 |
| pgvector 检索维度和多租户性能悬崖 | 部分完成 | schema 补齐 128/1024 维 HNSW，并增加 `(tenant_id, modality, embedding_dim)` 检索索引；2048 维因 pgvector HNSW 维度上限保留精确排序。后续需要在真实租户/模态基数上评估 partition、partial ANN 或独立集合策略。 |
| 内存态 gallery 无锁迭代 | 已完成 | 检索和重建索引路径在锁内获取快照，避免并发 upsert 时触发字典迭代异常；热路径不再在无锁状态下直接遍历共享字典。 |
| LRU 可能释放正在推理的模型 bundle | 已完成 | bundle 增加 `in_use` 计数；推理执行期间标记在途，LRU 驱逐跳过在途 bundle，避免 session 被运行中的请求释放。 |
| runtime body 不可用标志永久锁死 | 已完成 | 不可用标志改为冷却重试时间戳，冷启动抖动后可自动恢复尝试。 |

## 中优先级

| 项目 | 状态 | `0.5.36` 处理 |
| --- | --- | --- |
| facade 全局函数重绑定非线程安全 | 部分完成 | `portrait_gallery` 改为显式 persist hook；`portrait_model_runtime` 用事件循环内锁串行化依赖同步；`portrait_postgres` 仍保留测试兼容用同步入口，但已用 `RLock` 串行保护。后续建议改为显式依赖注入或配置对象，彻底移除生产路径的全局同步。 |
| 全表快照 `fetchall()` 和逐行写入 | 已完成 | gallery/jobs/streams/thresholds 快照读取改为游标迭代；gallery snapshot 替换改为准备参数后批量执行，降低中间列表和 N+1 SQL 开销。 |
| 热路径每次新建 Redis/S3 客户端 | 已完成 | Redis task queue 与 S3 object store 缓存客户端实例，复用连接和 signer 初始化结果。 |
| 异步 health 处理器内阻塞 I/O | 已完成 | ONNX provider 查询、依赖 health 和磁盘检查通过 `run_blocking_io` 从事件循环隔离。 |
| `v1_compare_batch` 配对数无上限 | 已完成 | 增加 `MAX_COMPARE_BATCH_PAIRS` 配置上限，超限返回 413。 |
| rollout 端点返回原始异常文本 | 已完成 | 统一使用 `exception_log_summary()` 记录日志，客户端只收到脱敏错误。 |
| `v1_model_detail` 吞掉校验异常 | 已完成 | 校验产生的 `HTTPException` 正常向外传播，不再继续使用未校验输入。 |

## 低优先级与清理

| 项目 | 状态 | `0.5.36` 处理 |
| --- | --- | --- |
| `import *` 门面无 `__all__` | 已完成 | `vision.py`、`runtime.py`、`inference.py`、`model_config.py` 等兼容层改为显式导入和 `__all__`，ruff `F403/F405` 已清理。 |
| ruff 未用 import、E402、F841 | 已完成 | 工具脚本和应用模块已清理，`python -m ruff check app tools tests` 通过。 |
| 大量路由重复代码 | 后续计划 | 记录为低优先级重构项。建议在下一轮 API 契约稳定后抽取上传序列化、文件数/阈值校验、模型 registry snapshot 和 unauthorized helper。 |
| embedding 三重存储写放大 | 后续计划 | 本轮不做破坏性 schema 迁移。建议先确认 `embedding(BYTEA JSON)`、`embedding_json(JSONB)`、`embedding_vector` 的兼容消费者，再设计单一主存储和在线迁移路径。 |
| 预处理多余 `astype(np.float32)` 拷贝 | 部分完成 | 已清理热路径中可确认冗余的转换；后续可结合 profiler 继续压缩跨模块数组拷贝。 |

## 验证记录

- `python -m pytest tests\test_rate_limit.py tests\test_portrait_model_runtime.py tests\test_portrait_data_backends.py tests\test_portrait_pipeline_algorithms.py tests\test_portrait_tools.py -q`：`117 passed`。
- `python -m ruff check app tools tests`：通过。

## 后续执行顺序

1. 在真实 PostgreSQL/pgvector 数据量上跑 `EXPLAIN (ANALYZE, BUFFERS)`，决定 2048 维向量、热租户和热模态是否需要 partition、partial ANN 或独立集合。
2. 将 `portrait_postgres` 的测试兼容 seam 从生产调用路径中移出，改为依赖注入或测试 fixture 级 monkeypatch。
3. 抽取重复路由 helper，优先处理上传文件序列化、阈值/数量校验和模型 snapshot。
4. 设计 embedding 存储瘦身迁移，明确兼容窗口、回滚路径和索引重建步骤。
5. 在拥有可写 pycache 的环境中补跑完整 `pytest -q`、部署检查和生产就绪检查，作为真实发布门禁。
