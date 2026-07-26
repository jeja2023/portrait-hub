# 容量与恢复证据

`tools/portrait_acceptance_evidence.py` 校验目标环境的实测容量报告和恢复演练报告。工具不运行虚构负载、不自动填充结果，也不允许用开发机结果替代生产目标环境。

容量报告必须使用 `kind: capacity_report` 和 `schema_version: "1.0"`，至少记录目标交付形态、环境标识、Git 提交、OCI 镜像摘要、硬件、模型版本、配置指纹、数据集 manifest、统计方法、额定负载时长、原始统计文件及 SHA-256，以及平台/SRE 和测试负责人的批准。七类场景必须逐项记录吞吐、成功/错误率、p50/p95/p99 和排队/预处理/执行/后处理耗时；资源观测、向量检索质量、接口/模型/租户/项目拆分、单机/双机/目标集群容量表和扩缩容建议均为强制字段。完整口径见 `docs/operations/CAPACITY_BASELINE.md`。指标还必须覆盖推理 p95、GPU 排队 p95、系统错误率、结束时队列增长、模型质量回退、Critical/High 漏洞；启用批处理时记录吞吐收益和 p95 回退。

恢复报告必须使用 `kind: recovery_drill`，记录实际故障场景、RTO/RPO、数据库/向量/对象存储对账、确认写入丢失数、重复投递收敛结果、原始时间线和日志摘要，以及平台/SRE 和数据负责人的批准。

```powershell
python tools/portrait_acceptance_evidence.py artifacts/capacity-report.json --json
python tools/portrait_acceptance_evidence.py artifacts/recovery-drill.json --json
```

Private Standard 的默认候选门槛为 RTO 不超过 240 分钟、RPO 不超过 1440 分钟；Private HA 和 Platform API 为 RTO 不超过 60 分钟、RPO 不超过 15 分钟。所有形态的确认写入丢失数必须为 0。容量报告至少连续运行额定负载 30 分钟；HA/Platform 还必须包含至少 10 分钟的 1.5 倍突发阶段。

报告中的 `raw_evidence[].path` 相对报告文件解析，摘要不匹配、文件缺失、时间戳在未来或必需审批缺失时一律失败。发布证据包必须收录通过校验的报告，商业发布门禁不接受 `--no-verify-sources` 结果。
