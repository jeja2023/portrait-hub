# 商业容量基线

## 当前结论

截至 2026-07-24，本仓库没有可代表客户目标环境的签署容量实测，因此本基线状态为 **Unqualified**，不得用于生产 SLA、硬件报价或支持范围承诺。开发机单元测试、合成 smoke 和平台就绪检查不替代真实 GPU、真实模型与生产数据后端上的容量验收。

商业生产发布必须提交通过 `tools/portrait_acceptance_evidence.py` 校验的 `capacity_report`，并由 `platform_sre` 与 `test_owner` 批准。原始压测结果、Prometheus 导出、资源采样和检索质量结果必须作为 `raw_evidence` 附件并校验 SHA-256；禁止手工填写或复用其他环境的数值。

## 固定输入

每次基线必须冻结并记录以下输入，任一项变化均视为新基线：

| 输入 | 报告字段 | 要求 |
| --- | --- | --- |
| 源码 | `git_commit` | 完整提交标识 |
| 镜像 | `image_digest` | OCI `sha256:` 摘要 |
| 模型 | `test.model_versions` | 能力、版本与制品摘要 |
| 配置 | `test.configuration_fingerprint` | 规范化有效配置 SHA-256 |
| 数据 | `test.dataset_manifest` | 固定数据集版本与 manifest SHA-256 |
| 硬件 | `test.hardware` | CPU、内存、GPU、显存、磁盘和网络 |
| 统计 | `test.statistics` | 预热、样本数和百分位算法 |
| 环境 | `environment_id`、`profile` | 唯一环境和交付形态 |

测试数据 manifest 必须列出图片、视频、流源、人员库规模、向量维数、真值集、租户/项目分布和敏感数据处置方式。仓库不附带客户数据或虚构 manifest。

## 必测场景

容量报告的 `scenarios` 必须逐项覆盖：

1. `single_image_sync`：单图同步解析。
2. `image_batch`：图片批量解析。
3. `vector_extract_compare_search`：向量提取、1:1 比对和 1:N 检索。
4. `gallery_ingest_rebuild_query`：人员库录入、索引重建和并发查询。
5. `video_upload_async`：视频、分片上传和异步任务。
6. `stream_processing`：实时流、抽帧、跟踪和事件输出。
7. `multi_tenant_burst`：多租户、多项目混合负载和突发流量。

每项必须记录状态、吞吐、成功率、错误率、p50/p95/p99，以及排队、预处理、执行和后处理耗时。额定负载持续至少 30 分钟；Private HA 和 Platform API 还须执行至少 10 分钟的突发阶段。

## 观测与容量表

报告必须包含 GPU 利用率、显存峰值、显存碎片、OOM、CPU、内存、网络、磁盘、对象存储吞吐、Worker 并发、批量大小、队列深度和积压时间。向量检索必须记录 p50/p95/p99、召回率、索引大小和更新 p95；吞吐与失败率必须按接口、模型、租户和项目拆分。

`capacity_table` 必须包含以下三行实测结果，未执行的拓扑不能写估算值冒充实测：

| 拓扑 | 必填结果 | 当前状态 |
| --- | --- | --- |
| `single_node` | 硬件、额定吞吐、安全并发、最大测试负载、余量、过载行为、恢复时间 | Unqualified |
| `dual_node` | 同上，并包含单节点失效后的行为 | Unqualified |
| `target_cluster` | 同上，并匹配交付目标副本和 GPU 节点规格 | Unqualified |

安全余量不得低于 20%。超过容量边界时，行为必须是可观测的排队后限流或直接限流，不能出现静默丢请求、无界积压或进程 OOM。报告还必须给出扩容、限流、超时、重试、熔断和推荐硬件余量建议。

## 默认候选门槛

以下是进入客户特定验收前的默认候选门槛，合同覆盖项必须版本化并重新实测：

| 指标 | 门槛 |
| --- | --- |
| 同步推理 p95 | `< 2 s`，不计冷启动 |
| GPU 排队 p95 | `< 500 ms` |
| 系统错误率 | `< 1%` |
| 结束时队列增长 | `<= 0` |
| 模型质量回退 | `<= 1` 个百分点 |
| GPU OOM | `0` |
| Critical / High 漏洞 | `0 / 0` |
| 动态批处理吞吐收益 | 启用时 `>= 20%` |
| 动态批处理 p95 回退 | 启用时 `<= 10%` |

## 执行与发布

负载工具可以按客户环境选择 k6、Locust、Vegeta 或现有 SDK 驱动，但命令、版本、负载模型和随机种子必须随原始证据归档。完成后执行：

```powershell
python tools/portrait_acceptance_evidence.py artifacts/capacity-report.json --json
python tools/portrait_evidence_package.py --help
```

校验通过只说明报告结构、阈值、来源和审批完整，不替代负责人对测试设计和环境代表性的评审。在三个拓扑的真实报告完成、原始证据可复核且支持矩阵解除阻断前，本文件始终保持 Unqualified。
