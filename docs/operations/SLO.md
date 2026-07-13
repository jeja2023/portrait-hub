# PortraitHub SLO

生产目标 SLO：

- 可用性：30 天内经认证的 `/v1` 请求成功率达到 99.5%。
- 延迟：95% 的推理和比对请求在 2 秒内完成，不计模型冷启动。
- 排队：10 分钟窗口内 GPU 队列等待时间的 p95 低于 500 ms。
- 新鲜度：运行中流的 worker 心跳年龄保持在 30 秒以内。
- 安全性：对每个保留的审计片段执行审计链校验，结果都应为 `ok=true`。

主要信号：

- `gpu_worker_requests_total` 与 `gpu_worker_requests_total{status=...}`
- `gpu_worker_inference_seconds_bucket`
- `gpu_worker_queue_seconds_bucket`
- `gpu_worker_gpu_memory_used_bytes`
- `gpu_worker_gpu_queue_depth`
- `gpu_worker_gpu_device_queue_depth`
- `gpu_worker_loaded_models`
- `gpu_worker_stream_active_sessions`

运维响应：

- 当可用性、GPU 显存或审计链告警触发时，立即通知值班。
- 当延迟或队列深度告警持续超过 15 分钟时，创建工单。

接入侧解释口径：

- 稳定错误码目录由 `GET /v1/access/error-codes` 暴露；排障时同时记录 HTTP 状态、`detail.code`、`request_id` 和 `retryable` 建议。

## 控制台 SLO 面板口径

控制台“SLO 面板”优先读取 `/v1/access/call-logs?limit=500&created_since=<30d>` 计算近 30 天成功率、错误数和错误预算燃烧率；如果当前凭证没有 `access:read` 或日志窗口为空，则回退到 Prometheus 累计计数计算成功率。

延迟和排队口径来自 Prometheus histogram：`gpu_worker_inference_seconds_bucket` 计算 p95/p99，`gpu_worker_queue_seconds_bucket` 计算 GPU 队列等待 p95/p99。GPU 队列深度来自 `gpu_worker_gpu_queue_depth` 和 `gpu_worker_gpu_device_queue_depth{device=...}`，流会话和模型热状态分别来自 `gpu_worker_stream_active_sessions`、`gpu_worker_loaded_models` 与 `/v1/admin/status`。

面板导出的 JSON 会保留 `success_rate_source`、`call_log_window_seconds`、`queue_p95_seconds`、`queue_p99_seconds`、`gpu_device_queue_depths`、`error_budget_remaining` 和 `error_budget_burn_rate`，用于和调用日志、告警评估、压测报告交叉核对。