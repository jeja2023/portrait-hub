# PortraitHub 接入指南

本文档面向接入 PortraitHub 的业务项目。它把控制台“接入中心”、SDK、稳定 API、错误处理、SLO 和网关部署约束收在一个入口里，避免调用方只复制零散 curl 示例。

## 接入前提

- 服务端已完成平台门禁：`python tools\portrait_production_readiness.py --scope platform --strict`。
- 生产环境必须启用鉴权、租户头、限流、请求体大小限制和安全响应头。
- 阶段一 ReID-only 场景至少需要 `person_detector_default` 与 `person_reid_default` 指向已校验的 ONNX 模型。
- 完整多模态生产切换必须额外通过 `python tools\portrait_production_readiness.py --strict`，其中 face、pose、gait、appearance 不能仍是 fallback 或 placeholder。

## 推荐调用入口

生产环境建议只暴露 `/v1`、`/ready` 和受保护的 `/metrics`，由内网网关或服务网格转发到 `portrait-api` 或 GPU worker。

| 场景 | API |
| --- | --- |
| 人员入库 | `POST /v1/gallery/enroll` |
| 以图搜人 | `POST /v1/gallery/search` |
| 批量以图搜人 | `POST /v1/gallery/search/batch` |
| 1:1 人像比对 | `POST /v1/compare/persons` |
| 批量比对 | `POST /v1/compare/batch` |
| 图片解析 | `POST /v1/infer/persons` |
| 离线视频任务 | `POST /v1/jobs/video`、`GET /v1/jobs/{job_id}`、`GET /v1/jobs/{job_id}/result` |
| 实时流 | `POST /v1/streams`、`POST /v1/streams/{stream_id}/start`、`GET /v1/streams/{stream_id}/events` |
| 模型状态 | `GET /v1/models` |
| 阈值 | `GET /v1/thresholds` |
| 多模态融合 | `POST /v1/fusion/compare` |
| 租户目录 | `GET /v1/access/tenants`、`POST /v1/access/tenants` |
| 接入应用 | `GET /v1/access/applications`、`POST /v1/access/applications`、`PATCH /v1/access/applications/{app_id}`、`POST /v1/access/applications/{app_id}/rotate` |
| Webhook | `GET /v1/access/webhooks`、`POST /v1/access/webhooks`、`PATCH /v1/access/webhooks/{webhook_id}`、`POST /v1/access/webhooks/{webhook_id}/rotate`、`POST /v1/access/webhooks/{webhook_id}/sample` |

## 鉴权与租户

生产对接推荐使用“单凭证优先”模式。管理员在控制台“接入中心”或 `POST /v1/access/tenants` 中只填写租户名称，后台自动生成稳定 `tenant_id`、默认接入应用和一次性 API Key；业务系统调用时只需要携带：

```text
X-API-Key: <application-api-key>
```

服务端会自动完成：

```text
API Key -> 接入应用 -> 租户 -> scope 权限 -> 应用级限流/配额 -> 审计与调用日志归属
```

最小租户开通请求示例：

```json
{
  "name": "客户 A",
  "create_default_application": true
}
```

JWT 对接同样支持单凭证：当 JWT 中只有一个 `tenant_id`、`tenant` 或 `tenants` claim 时，可以只发送：

```text
Authorization: Bearer <jwt>
```

如果一个 JWT 或系统凭证允许访问多个租户，调用方仍需显式选择本次请求租户：

```text
X-Tenant-ID: <tenant-id>
Authorization: Bearer <jwt>
```

旧版 `X-Tenant-ID + X-API-Key` 调用继续兼容，适合迁移期、排障或多租户共享凭证场景：

```text
X-Tenant-ID: <tenant-id>
X-API-Key: <application-api-key>
```

生产建议：
- 管理员只维护租户名称、用户/角色和接入应用；`/v1/access/tenants` 会生成 `tenant_id`，并可一键创建默认接入应用、scope、限流、配额和审计归属。
- 接入应用使用单租户 API Key；不要让多个业务系统共享同一个高权限密钥。
- 不要把全局 `API_TOKEN` 当作普通多租户应用密钥。v1 使用该令牌时必须配置 `API_TOKEN_TENANT_ID`；只有受控平台运维才可显式开启 `API_TOKEN_ALLOW_TENANT_OVERRIDE=true`。
- JWT 模式需要校验 issuer、audience、exp 和租户 claim；多租户 JWT 必须显式选择租户。
- 控制台“接入中心”通过 `/v1/access/tenants` 开通租户，通过 `/v1/access/applications` 管理租户级应用密钥；密钥只在创建或轮换响应中显示一次，服务端仅保存哈希与短宽限期内的旧密钥哈希。
- 默认租户接入应用只包含业务调用所需 scope，不包含 `tenants:read` / `tenants:write`；租户目录读写应由管理员 JWT 或显式授权的高权限接入应用完成。
- 接入应用可配置 `rate_limit_per_minute`、`rate_limit_burst` 和 `daily_quota`；留空或 `0` 表示沿用平台默认/不限额，正整数会在入口限流中生效。
- 调用日志可通过 `/v1/access/call-logs` 按 `request_id`、接口、状态、接入应用、`error_code`、`created_since` 和 `created_until` 筛选；日志只保存脱敏元数据，不记录请求体或响应体，并会回写接入应用的 `call_count`、`error_count`、`error_rate`、`last_called_at` 和 `last_error_at`。高频计数先在内存聚合，再按 `ACCESS_STATS_FLUSH_INTERVAL_SECONDS` 批量落盘；服务关闭和接入配置变更会强制刷新。
- 错误码目录可通过 `/v1/access/error-codes` 读取，返回 `code`、`http_status`、`retryable`、`category`、`description` 和 `operator_action`。接入方应把 `retryable=true` 视为可预算内退避重试，而不是无限重放。
## 接口调试台 受控调试

控制台“接口调试台”用于开发或受控内网环境的最小联调，不应替代业务侧自动化测试。它覆盖单图检索、批量检索、单图比对、批量比对、融合比对、图片解析、离线视频、实时流创建、实时流事件查询、模型状态和阈值查询。

接口调试台会保留统一响应外层的 `request_id`、HTTP 状态和 `error.code`，并把接口模板、解析后的路径、耗时、文件数量、`async_mode` 和受控调试标记写入页面响应数据，方便和 `/v1/access/call-logs` 按 `request_id` 交叉定位。
## Python SDK 最小示例

```python
import os
from pathlib import Path

from sdk.python.portrait_hub_client import PortraitHubClient

client = PortraitHubClient(
    base_url="https://portrait.internal.example",
    api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),
    auth_scheme="api_key",
    timeout=30,
)

enroll = client.enroll("person-001", [Path("a.jpg"), Path("b.jpg")], modality="body")
search = client.search(Path("query.jpg"), modality="body", top_k=5, threshold_profile="normal")
compare = client.compare_persons(Path("a.jpg"), Path("query.jpg"), threshold_profile="normal")

print(enroll["request_id"], search["request_id"], compare.get("data", {}).get("passed"))
```

## Node SDK 最小示例

```javascript
const { PortraitHubClient } = require("./sdk/node/portraitHubClient");

const client = new PortraitHubClient({
  baseUrl: "https://portrait.internal.example",
  apiToken: process.env.PORTRAIT_HUB_API_TOKEN,
  authScheme: "api_key",
});

const enroll = await client.enroll("person-001", ["a.jpg", "b.jpg"], "body");
const search = await client.search("query.jpg", "body", 5, "normal");
const compare = await client.comparePersons("a.jpg", "query.jpg", "normal");

console.log(enroll.request_id, search.request_id, compare.data?.passed);
```

## 批量异步与视频轮询 SDK 示例

批量图片检索和批量比对均可通过 SDK 直接提交异步任务，响应中的 `data.batch_id` 可交给任务状态页、Webhook 或后台轮询流程继续跟踪。

```python
batch = client.search_batch(
    [Path("query-a.jpg"), Path("query-b.jpg")],
    modality="body",
    top_k=10,
    threshold_profile="normal",
    async_mode=True,
)
batch_id = batch.get("data", {}).get("batch_id")
print(batch["request_id"], batch_id)
```

```javascript
const batch = await client.compareBatch(["a1.jpg", "a2.jpg"], ["b1.jpg", "b2.jpg"], {
  modality: "body",
  thresholdProfile: "normal",
  asyncMode: true,
});
console.log(batch.request_id, batch.data?.batch_id);
```

离线视频任务建议优先使用 Webhook 接收 `job.completed`，需要主动查询时按状态轮询，终态后再读取结果。

上传请求会分块写入 `VIDEO_JOB_INPUT_DIR`，随后由持久化队列交给独立 worker。生产环境应设置 `TASK_QUEUE_BACKEND=redis`、`VIDEO_JOB_WORKER_IN_PROCESS=false`，运行 `python -m app.portrait_video_job_worker`，并让 API 与 worker 共享视频暂存目录；任务取消信号同样通过队列后端跨进程传播。

```javascript
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const terminal = new Set(["completed", "failed", "cancelled"]);

const job = await client.createVideoJob("clip.mp4", { frameInterval: 15, maxFrames: 64 });
const jobId = job.data?.job?.job_id ?? job.data?.job_id;
let status = job;
while (jobId && !terminal.has(status.data?.job?.status)) {
  await wait(2000);
  status = await client.getJob(jobId);
}
const result = jobId ? await client.jobResult(jobId) : {};
console.log(jobId, result.request_id);
```
## curl 示例

以图搜人：

```bash
curl -X POST "https://portrait.internal.example/v1/gallery/search" \
  -H "X-Tenant-ID: tenant-a" \
  -H "X-API-Key: ${PORTRAIT_HUB_API_TOKEN}" \
  -F "file=@query.jpg" \
  -F "modality=body" \
  -F "top_k=5" \
  -F "threshold_profile=normal"
```

离线视频任务轮询：

```bash
JOB_ID=$(curl -s -X POST "https://portrait.internal.example/v1/jobs/video" \
  -H "X-Tenant-ID: tenant-a" \
  -H "X-API-Key: ${PORTRAIT_HUB_API_TOKEN}" \
  -F "file=@clip.mp4" \
  -F "frame_interval=15" \
  -F "max_frames=64" | jq -r '.data.job.job_id')

curl -H "X-Tenant-ID: tenant-a" \
  -H "X-API-Key: ${PORTRAIT_HUB_API_TOKEN}" \
  "https://portrait.internal.example/v1/jobs/${JOB_ID}/result"
```

## OpenAPI 与 Webhook

OpenAPI 可用于生成接入侧客户端、网关放行清单和 smoke test 路径校验。生产环境若关闭 `/openapi.json`，应在发布物中保留同版本的契约快照，并由控制台“OpenAPI”页或 CI 记录核心路径是否完整。控制台接入中心的应用与 Webhook 配置分别落到 `/v1/access/applications` 和 `/v1/access/webhooks`，要求管理员或具备对应权限的 JWT/全局 token 操作。

Webhook 面向异步任务、视频流事件和模型发布通知。建议事件体保持以下外层结构：

```json
{
  "id": "evt_...",
  "event": "job.completed",
  "tenant_id": "tenant-a",
  "request_id": "req_...",
  "created_at": "2026-07-11T00:00:00Z",
  "data": {}
}
```

推荐请求头：

```text
Content-Type: application/json
X-PortraitHub-Event: job.completed
X-PortraitHub-Delivery: evt_...
X-PortraitHub-Signature: sha256=<hmac-sha256-body>
```

接入方处理要求：

- 以 `X-PortraitHub-Delivery` 做幂等去重。
- 校验 `X-PortraitHub-Signature` 后再解析业务载荷。
- 2xx 视为成功；非 2xx 按控制台 Webhook 配置的 `retry_limit` 和 `timeout_seconds` 重试。
- 回调 URL 不应包含 token；敏感凭证放在服务端配置或 mTLS 中。
- 订阅事件至少覆盖 `gallery.enrolled`、`search.completed`、`compare.completed`、`job.completed`、`stream.event` 和 `model.rollout` 中实际使用的类型。
- 排查模型发布、灰度和回滚时，可用 `GET /v1/admin/models/rollout/audit?limit=20` 查看最近非 dry-run 发布审计；响应会忽略损坏 JSONL 行并只返回白名单字段，避免把任意审计载荷透给接入侧。

## 响应与错误处理

成功响应统一包含：

```json
{
  "status": "success",
  "request_id": "req_...",
  "data": {}
}
```

`/v1` 错误响应使用固定外层，不再根据错误类型改变字段形状：

```json
{
  "status": "error",
  "request_id": "req_...",
  "error": {
    "code": "validation_error",
    "message": "请求参数验证失败",
    "details": {}
  }
}
```

调用方应按 HTTP 状态码做主判断，按 `error.code` 做细分处理；`error.details` 仅在存在结构化上下文时返回。

稳定目录也可通过 GET `/v1/access/error-codes` 查询；控制台“错误码”页与该接口同源，方便接入方把 HTTP 状态、`error.code` 和重试策略保持一致。

| HTTP | 典型原因 | 调用方处理 |
| --- | --- | --- |
| 400 | 参数不支持、metadata 非 JSON 对象、流地址不合规 | 修正请求，不自动重试 |
| 401 | token 缺失或无效 | 刷新凭证或停止请求 |
| 403 | JWT 与租户不匹配、RBAC scope 不足 | 检查租户和权限 |
| 404 | person、job、stream、model 或 alias 不存在 | 停止轮询或提示资源不存在 |
| 409 | 模型别名切换期望目标不匹配 | 重新读取当前配置后再提交 |
| 413 | 文件或请求体超过上限 | 压缩输入或拆分请求 |
| 422 | 查询参数边界或 schema 校验失败 | 修正请求结构 |
| 429 | 限流或应用日配额耗尽 | 遵守 `Retry-After`，指数退避并保留 request_id |
| 503 | 状态写入、队列、存储、模型队列或外部依赖不可用 | 可按幂等性和业务成本有限重试 |

## SLO 与观测

默认生产目标见 [SLO.md](SLO.md)。调用方至少记录：

- `request_id` 与业务 trace id。
- HTTP 状态码、`error.code`、接口路径和租户。
- 客户端总耗时、连接超时、读取超时。
- 对视频/流任务记录 `job_id` 或 `stream_id`。

平台侧主要 Prometheus 信号：

- `gpu_worker_requests_total` 与 `gpu_worker_requests_total{status=...}`。
- `gpu_worker_inference_seconds_bucket`。
- `gpu_worker_queue_seconds_bucket`。
- `gpu_worker_gpu_queue_depth` 与 `gpu_worker_gpu_device_queue_depth`。
- `gpu_worker_gpu_memory_used_bytes`、`gpu_worker_gpu_memory_free_bytes`。
- `gpu_worker_stream_active_sessions`。
- 控制台 SLO 面板会优先用近 30 天 `/v1/access/call-logs` 计算成功率和错误预算燃烧率，再用 Prometheus histogram 展示推理 p95/p99、队列 p95/p99、GPU 队列深度和 worker 热状态。

## 网关部署

对外验收工具建议与接入中心应用密钥保持同一认证方式：

```bash
python tools/service_smoke_test.py --base-url https://portrait.internal.example --tenant-id tenant-a --token "$PORTRAIT_HUB_API_TOKEN" --auth-scheme api-key --require-ready
python tools/regression_check.py --manifest regression.yml --base-url https://portrait.internal.example --tenant-id tenant-a --token "$PORTRAIT_HUB_API_TOKEN" --auth-scheme api-key
python tools/load_test.py --url https://portrait.internal.example/health --tenant-id tenant-a --token "$PORTRAIT_HUB_API_TOKEN" --auth-scheme api-key
```

Postman 集合位于 `tools/portrait_hub_postman_collection.json`，默认变量为 `base_url`、`tenant_id` 和 `api_key`；新接入优先只发送 `X-API-Key`，需要多租户显式选择时再发送 `X-Tenant-ID`。

两套 demo client 位于 `examples/demo-clients/`，分别代表两个业务项目接入同一服务：Python 默认 `tenant-a`，Node 默认 `tenant-b`。先执行 `--dry-run` 核对 `health/models/thresholds/enroll/search/compare/video job` 调用计划，再替换真实媒体样本进行联调。
可从 [nginx-gateway.example.conf](../../ops/nginx-gateway.example.conf) 起步。生产网关至少应提供：

- TLS 终止或 mTLS。
- 请求体大小限制，与服务端 `MAX_REQUEST_BODY_BYTES` 对齐。
- 对 `/v1`、`/metrics`、`/ready/deep` 的鉴权要求。
- 连接、读取和发送超时，读取超时需要覆盖排队、推理和模型冷启动。
- `X-Request-ID` 透传，缺失时由网关生成。

## 接入验收清单

- 完成 enroll、search、compare、video job 四类 SDK 或 HTTP 调用。
- A/B 两个租户互相不可读取对方人员、任务和流数据。
- token 轮换时旧 token 在约定宽限期内仍可用，过期后被拒绝。
- 上传超大文件、非法图片、非法流地址和缺失租户头都能得到明确 4xx。
- 压测达到阶段一容量基线，且 SLO 面板能看到 p95、错误率、队列和 worker 状态。
- 日志和控制台调用日志可以通过 `request_id` 定位同一次请求。
