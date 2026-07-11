# 后审计优化升级记录

版本：`0.5.55`

日期：2026-07-11

本文记录本轮全面检查后的已落地优化，以及仍需要真实生产依赖或业务数据配合的扩展验收项。它补充 `影鉴优化方案.md` 和 `PLATFORM_ACCEPTANCE.md`，作为后续升级的执行清单。

## 本轮已落地

- 修复类型与部署门禁阻断：`tools/type_check.py`、`tools/deploy_check.py --skip-node` 已恢复通过，模型治理 YAML 读取、流 worker 锁时间解析和 UTF-8 BOM 解析边界均已加固。
- 增加源码编码卫生门禁：部署检查现在扫描 app、tools、sdk、tests、frontend、docs、ops、deploy、requirements 和 GitHub workflow，阻止 UTF-8 BOM 再次进入源码与配置。
- 拆分控制台配置边界：`frontend/console/console.config.js` 承载端点映射和告警默认值，主控制台脚本只消费 `window.PortraitConsoleConfig`，并由 API 契约、部署检查和生产就绪检查共同守住。
- 收口图库变更回滚边界：新增 `app/portrait_gallery_mutations.py`，将对象清理、图库快照恢复和回滚失败脱敏从路由中抽出，路由只保留 HTTP 编排和依赖注入。
- 扩充生产就绪门禁：`tools/portrait_production_readiness.py` 已将控制台配置资产和外置配置契约纳入平台级检查。
- 标准化 Node 检查入口：新增根目录 `package.json`，`npm run check` 统一执行 Node SDK 契约测试，并同步 CI、部署检查和生产就绪模板。

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