# PortraitHub 运维手册

告警触发时：

1. 检查 `/ready` 和 `/ready/deep`。
2. 复核快速燃烧和慢速燃烧的错误预算告警。
3. 查看最新模型发布审计记录。
4. 如果回归与模型变更相关，回滚活跃模型别名。
5. 如果不存在异常模型发布但延迟和队列深度持续增长，降低流量或扩容。

合成探测：

- 通过 API 运行一次小型认证请求。
- 确认请求 ID 和探测延迟已记录到日志。

容量测试：

- 流量跃升前运行压测。
- 跟踪 p95 延迟、队列深度和 GPU 显存。

## 视频流无解析图片

当控制台显示“分析中”但没有实时图片时，按以下顺序检查：

1. 请求 `GET /v1/streams/{stream_id}/events?limit=200`，确认最新事件中是否存在 `stream_analysis_completed`。不要只看最早的注册和启动事件。
2. 若事件停在 `stream_worker_session_started` 且 `processed_frames=0`，检查 `portrait-stream-worker` 进程或容器、拉流日志和 RTSP 连通性。
3. 检查 stream 状态中的 `worker_lease_active`。未激活通常表示 daemon 没有接管；反复过期则检查 worker 健康、`STREAM_WORKER_LOCK_DIR` 权限和残留 lock。
4. 确认 API 与 worker 使用相同的 `.env`、租户、存储后端和 `PORTRAIT_STREAMS_STATE_PATH`/共享数据卷，并核对 `ALLOW_STREAM_URLS`、`ALLOW_PRIVATE_STREAM_HOSTS`、`STREAM_ALLOWED_HOSTS`。
5. 若事件已包含 `stream_analysis_completed`，检查 payload 的 `thumbnails` 是否非空；服务端有图而页面未更新时，刷新 WebSocket 连接并查看浏览器网络/控制台错误。
6. CPU 兜底推理首批通常需要 20～60 秒。等待至少一个完整 batch 后再判断；本地 `python dev_start.py` 会同时启动 API 和流 worker。
