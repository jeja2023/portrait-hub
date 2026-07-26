# 写请求幂等契约

PortraitHub 的 `/v1` 写接口接受可选的 `Idempotency-Key` 请求头。视频分片上传保留自身的内容摘要与分片语义，其余 POST、PUT、PATCH、DELETE 由统一幂等层保护。

## 行为

- 首次请求在认证、权限和高风险二次认证通过后预占键，再执行写操作。
- 在保存窗口内使用相同键、方法、路径、查询参数和请求体时，服务返回首次响应，并设置 `Idempotency-Replayed: true`。
- 同一作用域内使用相同键但请求指纹不同，返回 `409 idempotency_key_conflict`，不会执行写操作。
- 首次请求仍在处理中时返回 `409 idempotency_request_in_progress` 和 `Retry-After: 1`。
- 5xx 或超过响应保存上限的结果不会保存；响应头 `Idempotency-Status: not-stored` 表示调用方不得自动重放非幂等业务操作。
- 首次成功保存的响应设置 `Idempotency-Replayed: false` 和 `Idempotency-Key-Expires-At`。

默认保存窗口为 24 小时，由 `IDEMPOTENCY_WINDOW_SECONDS` 控制，允许范围为 60 秒至 7 天。`IDEMPOTENCY_MAX_RESPONSE_BYTES` 默认 2 MiB。本地开发使用有界内存存储；配置 `REDIS_URL` 后使用带 TTL 的 Redis 原子记录。生产配置已强制要求 Redis，确保多副本共享键空间。

调用方应为一次业务意图生成不可预测的稳定键，并在网络超时重试时复用该键；不得为每次重试生成新键。日志和响应只暴露键的 SHA-256 短指纹，不记录原始键。
