# PortraitHub SLO

生产目标 SLO：

- 可用性：30 天内经认证的 `/v1` 请求成功率达到 99.5%。
- 延迟：95% 的推理和比对请求在 2 秒内完成，不计模型冷启动。
- 排队：10 分钟窗口内 GPU 队列等待时间的 p95 低于 500 ms。
- 新鲜度：运行中流的 worker 心跳年龄保持在 30 秒以内。
- 安全性：对每个保留的审计片段执行审计链校验，结果都应为 `ok=true`。

主要信号：

- `gpu_worker_http_request_duration_seconds_bucket`
- `gpu_worker_inference_duration_seconds_bucket`
- `gpu_worker_gpu_memory_used_bytes`
- `gpu_worker_gpu_queue_depth`
- `gpu_worker_gpu_device_queue_depth`
- `gpu_worker_loaded_models`
- `gpu_worker_stream_active_sessions`

运维响应：

- 当可用性、GPU 显存或审计链告警触发时，立即通知值班。
- 当延迟或队列深度告警持续超过 15 分钟时，创建工单。
