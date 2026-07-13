# AI 模型治理

生产模型发布必须携带模型卡和治理 sidecar。

sidecar 必要章节：

- `dataset_lineage`
- `bias`
- `threshold_calibration`
- `risk_management`
- `human_review`
- `drift_monitoring`
- `privacy`
- `release`

发布门禁：

1. 切换前使用治理检查校验模型包。
2. 在留出样本上验证回归门禁。
3. 为每个活跃别名要求明确的回滚目标。
4. 对落入人工复核区间的模糊分数进行复核。
5. 将漂移和阈值重新校准作为发布构件跟踪，而不是仅写入备注。