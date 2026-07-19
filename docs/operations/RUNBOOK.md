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

## 控制台登录与身份排障

1. 登录页未显示用户名/密码时，请求 GET /v1/auth/config，确认 local_enabled；默认 admin / 123456 只对 loopback 可见，生产模式或远程开放但仍使用默认密码/默认会话密钥时会自动禁用。
2. 生产本地账号必须设置 LOCAL_AUTH_PASSWORD、LOCAL_AUTH_SESSION_SECRET、LOCAL_AUTH_COOKIE_SECURE=true；若通过反向代理访问，还需明确评估 LOCAL_AUTH_ALLOW_REMOTE，不能直接暴露默认弱凭据。
3. OIDC 登录失败时检查 discovery、issuer、client_id、回调地址、JWKS、系统时间和 OIDC_ROLE_MAPPING。未映射角色/用户组以及非法租户声明会按设计拒绝登录。
4. 登录成功但写操作返回 403 CSRF validation failed 时，检查 portrait_csrf Cookie 与 X-CSRF-Token 是否同时经过代理，并确认请求同源。
5. 登录后角色或租户异常时访问系统管理的“身份与权限”，核对认证来源、tenant_id、roles 和权限矩阵；API Key/JWT 显式错误不会回退到浏览器会话。
6. 应急禁用人员登录可设置 LOCAL_AUTH_ENABLED=false 和 OIDC_ENABLED=false；系统到系统 API Key/JWT 接入仍按原鉴权配置运行。

## 视频流无解析图片

当控制台显示“分析中”但没有实时图片时，按以下顺序检查：

1. 请求 `GET /v1/streams/{stream_id}/events?limit=200`，确认最新事件中是否存在 `stream_analysis_completed`。不要只看最早的注册和启动事件。
2. 若事件停在 `stream_worker_session_started` 且 `processed_frames=0`，检查 `portrait-stream-worker` 进程或容器、拉流日志和 RTSP 连通性。
3. 检查 stream 状态中的 `worker_lease_active`。未激活通常表示 daemon 没有接管；反复过期则检查 worker 健康、`STREAM_WORKER_LOCK_DIR` 权限和残留 lock。
4. 确认 API 与 worker 使用相同的 `.env`、租户、存储后端和 `PORTRAIT_STREAMS_STATE_PATH`/共享数据卷，并核对 `ALLOW_STREAM_URLS`、`ALLOW_PRIVATE_STREAM_HOSTS`、`STREAM_ALLOWED_HOSTS`。
5. 若事件已包含 `stream_analysis_completed`，使用 `GET /v1/analysis/results?source_type=stream&limit=20` 查找 `source_ref` 等于该 `stream_id` 的档案。流事件的持久化载荷不再保存 Base64 图片，结果图应从统一档案读取。
6. CPU 兜底推理首批通常需要 20～60 秒。等待至少一个完整 batch 后再判断；本地 `python dev_start.py` 会同时启动 API 和流 worker。

## 解析档案健康与容量

统一档案没有每租户数量上限，也不会因为流事件环形历史淘汰而删除。生产环境必须把数据库增长、对象数量和对象存储字节数纳入容量告警。

日常检查：

1. 将 `source_type` 分别设为 `image`、`video` 和 `stream` 请求 `GET /v1/analysis/results?source_type=<type>&limit=1`，确认三种来源可查询且 `content_url` 能在相同租户凭证下读取。
2. PostgreSQL 后端检查 `portrait_analysis_archives` 的租户记录数和最新 `created_at`；本地后端检查 `PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH` 及其 `-wal`、`-shm` 文件所在磁盘空间。
3. 监控 S3 bucket 或 `OBJECT_STORAGE_DIR` 的对象数、总字节数、写入失败和读取失败。数据库有记录但对象接口返回 404/5xx，通常表示对象被外部删除、存储凭证变化或数据库与对象存储恢复点不一致。
4. 确认 API、视频 worker 和流 worker 的 `PORTRAIT_STORAGE_BACKEND`、`POSTGRES_DSN`、`PORTRAIT_OBJECT_STORAGE_BACKEND`、S3 配置、`ENCRYPTION_KEY_ID` 和 `ENCRYPTION_KEYRING` 一致。

备份与恢复：

1. 同一备份窗口内保存 PostgreSQL/SQLite 索引和对象存储；仅使用 `/v1/admin/backup` 的元数据导出不能替代解析对象备份。
2. 恢复时先恢复对象存储及历史解密密钥，再恢复数据库索引，最后启动 API 和 worker。
3. 恢复后从三个 `source_type` 各抽样档案，读取预览和完整对象并核对租户隔离。
4. 任何主动保留或删除策略都必须同时删除索引行、完整对象和预览对象；当前版本默认长期保留，不自动清理解析档案。
