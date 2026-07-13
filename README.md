# 影鉴

面向 Ubuntu + Docker + NVIDIA GPU 的 ONNX 推理服务。服务通过 FastAPI 暴露接口，按 GPU 拆成多个 worker，适合给人像识别、人像检索、ReID 等业务项目提供共享推理能力。

当前版本：`0.6.1`。本版本在 `0.6.0` 平台收口基础上完成全项目中文化清理，统一前端、API、CLI、SDK、日志、示例和文档的中文提示；必要协议名、字段名和技术专名保持英文以维持兼容。

## PortraitHub v1 平台接口

服务现在还提供 PortraitHub v1 接口，用于人像中台方案：

- `/v1/infer/faces`、`/v1/infer/persons`、`/v1/infer/pose`、`/v1/infer/appearance`、`/v1/infer/gait`
- `/v1/compare/faces`、`/v1/compare/persons`、`/v1/compare/gait`、`/v1/fusion/compare`
- `/v1/gallery/enroll`、`/v1/gallery/search`、`/v1/gallery/{person_id}`、`/v1/gallery/reindex`
- `/v1/jobs/video`、`/v1/jobs/{job_id}`、`/v1/jobs/{job_id}/result`、`/v1/jobs/{job_id}/cancel`
- `/v1/streams`、`/v1/streams/{stream_id}/start`、`/v1/streams/{stream_id}/stop`、`/v1/streams/{stream_id}/status`、`/v1/streams/{stream_id}/events`
- `/v1/models`、`/v1/models/{model_id}`、`/v1/models/{model_id}/load`、`/v1/models/{model_id}/unload`
- `/v1/thresholds`、`/v1/thresholds/{profile}`、`/console`

当人脸、姿态、步态、衣着、数据库或向量库这些专用服务尚未配置时，v1 接口会使用明确的本地兜底能力，保证平台契约在开发环境里仍可运行。生产部署应使用校准后的模型和向量库集成替换这些兜底实现。

推荐的生产数据栈：

- PostgreSQL 作为租户、人员、底库元数据、特征元数据、阈值、视频任务、流、审计事件和对象元数据的主数据库。
- 当你想要最简单的生产拓扑时，`pgvector` 是首选向量后端。
- 当特征规模、QPS、过滤需求或运维隔离要求超过 PostgreSQL 时，推荐改用 Qdrant。
- MinIO 或其他 S3 兼容服务用于存放上传的底库图片、快照以及视频/对象载荷，数据库只保存对象 key 和元数据。
- Redis 只作为分布式任务队列的可选转交通道，本地默认队列仍适合单进程开发。

算法流水线升级：

- 图像质量评分现在包含模糊、曝光、过曝/欠曝裁剪、对比度、尺寸、宽高比、色彩丰富度和噪声信号。
- 图像解码增加了内容 SHA-256 以及感知平均哈希/差异哈希；批量图像解码会把完全重复和近重复输入标记为 `duplicate_of`。
- 图像上传现在通过同一条共享路径校验扩展名、文件签名、解码格式和像素上限，供旧接口和 v1 接口共用。`decode_upload_images()`、图库批量检索和批量人像/衣着比对均采用有界并发，分别由 `MAX_IMAGE_DECODE_CONCURRENCY`、`MAX_GALLERY_SEARCH_BATCH_CONCURRENCY` 和 `MAX_COMPARE_BATCH_CONCURRENCY` 控制，失败时会取消进行中任务并停止启动后续项。
- 视频上传会先用容器签名校验支持的扩展名，再进行 OpenCV 解码，从而降低伪装媒体的注入风险。
- 离线视频抽帧采用“区间 + 均匀”混合过采样，并结合质量、多样性、时间覆盖和场景片段选择，让受限帧数仍能覆盖整段视频、覆盖不同镜头，并避免低价值近重复帧。
- 视频和视频流抽帧现在会增加感知帧指纹和 MMR 风格的帧选择，抑制近重复候选，同时保留高质量且多样的证据。
- 视频流和不可 seek 视频抽帧在质量/多样性选择之前也会使用有界的顺序候选池，同时继续遵守读取超时。
- 抽取到的视频帧和流帧会在元数据里携带每帧质量、场景变化分数、帧指纹、被选帧的哈希距离以及近重复候选数量。
- 人体轨迹推理现在使用借鉴 ByteTrack/BoT-SORT 的两阶段关联策略，结合 IoU、运动连续性、置信区间、ReID 外观 embedding 和小批量全局最优分配，生成稳定的 `track_id`。
- 人体轨迹推理现在会在 embedding 聚合前，对每个 ReID 裁剪结果计算图像质量、面积占比、截断情况、宽高比适配度和可用性。
- 轨迹关联在最后一次观测 IoU 之外增加了恒速预测框，改善高速运动和间隔抽帧视频的连续性；每次匹配都会报告决策余量、置信度、风险和支持信号，便于排查 ID 切换。
- 轨迹后处理增加了时间维度的 `smoothed_box`、短间隔插值摘要、片段合并、稳定性分数、间隔计数和插值计数，供视频/流分析使用。
- 轨迹现在暴露面向近期的、按质量/置信度加权的 tracklet 模板，并做成对一致性离群抑制；只有在 `include_embeddings=true` 时才返回向量载荷。
- Tracklet 模板可以直接使用与人脸/人体/步态比对相同的质量感知阈值契约进行比较。
- 人脸兜底检测使用带缓存的、受限的 OpenCV Haar 路径，并在低纹理场景下快速回退，避免无人脸或低信息图像阻塞比对请求。
- 人脸/人体/步态比对使用质量感知校准，在保持原始相似度兼容性的同时，增加调整后相似度、调整后阈值、质量惩罚、质量门槛、决策余量、置信度和风险字段。
- 比对响应会包含输入证据，例如解码后的帧质量、指纹以及完全/近重复标记；人脸/人体/融合决策还会暴露 `input_independence`，对重复证据降低置信度，并附加重复输入风险因素，而不会掩盖已有的质量或模态风险。
- 步态比对会在 tracklet embedding 之前排除近重复序列帧。
- 多模态融合会报告一致性、分数一致程度、冲突惩罚、决策余量、置信度和风险标签，以便保守处理相互矛盾的模态证据。
- 底库检索会先扩大特征级检索池，再做人员级聚合；当查询质量和种子候选足够稳定时，还可以运行保守的查询扩展，执行第二阶段检索。
- 底库候选会包含查询质量、模板质量、决策余量、置信度、支撑因子、排序上下文、最近竞争者差距和风险标签，方便人工复核排序。
- 底库注册会跳过同一次请求中的完全/近重复图片，并报告被跳过的重复来源。
- `tools/portrait_algorithm_eval.py` 会评估比对 ROC/TAR@FAR、底库/ReID 检索 mAP/CMC/MRR/nDCG/precision@K/recall@K、分数分离度、工作阈值校准、复核带、跟踪 MOTA/IDF1/ID 切换/HOTA 代理/覆盖率，以及来自离线 JSON/YAML manifest 的嵌套帧/人员质量分布。

v1 已加入的生产加固：

- 底库记录按 `X-Tenant-ID` 隔离，默认持久化到 `PORTRAIT_GALLERY_STATE_PATH`，当 `PORTRAIT_STORAGE_BACKEND=postgres` 时则写入 PostgreSQL。
- 本地 JSON 底库默认启用 WAL 增量写入，变更先追加到 `.wal.jsonl`，再按 `PORTRAIT_GALLERY_WAL_COMPACT_EVERY` compact 成主快照，降低高频入库时的全量序列化压力。
- 底库特征写入会以 `upsert_feature` 增量 WAL 记录落盘，避免每次新增特征都重写整个人员特征列表。
- 底库特征状态保留私有对象引用，因此删除人员时可以同时清理关联的本地/S3 载荷，而公开响应仍会脱敏对象 key、bucket 和 hash。
- 人员删除会在不可逆清理开始前先写入失败关闭的 `gallery_delete_person_requested` 审计记录；对象清理失败会回滚并上报，不暴露存储位置。
- 保留清理按租户范围执行，会删除过期的视频任务、流事件、底库人员及相关对象载荷，并在清理失败时失败关闭，同时回滚元数据。
- 阈值更新默认持久化到 `PORTRAIT_THRESHOLDS_STATE_PATH`，或者在 `PORTRAIT_STORAGE_BACKEND=postgres` 时写入 PostgreSQL。
- 底库、阈值、视频任务和流操作会把脱敏、哈希链式 JSONL 审计事件写入 `PORTRAIT_AUDIT_PATH`；PostgreSQL 部署还会把审计行写入 `portrait_audit_events`，并保留可查询的审计 hash 列。
- `AUDIT_WRITE_FAIL_CLOSED` 会在审计持久化失败时让管理/审计写入失败关闭；Docker Compose 默认设为 `true`。
- 流注册会阻止私网、回环、链路本地、多播、保留和未指定的 IP 字面量，以及解析到这些地址段的主机名，除非显式设置 `ALLOW_PRIVATE_STREAM_HOSTS=true`。
- 旧版流推理现在复用同样的 SSRF 防护，并在响应元数据中隐藏凭据以及查询串/fragment 中的秘密。
- 流状态会保护流 URL 以及敏感 `settings`/`metadata` 字段在静态存储中的安全，而公开流响应会保持这些字段脱敏。
- `STREAM_ALLOWED_HOSTS` 可以把流 URL 限制到显式的主机/域名白名单。
- `RBAC_ENABLED`、`JWT_ALGORITHM`、`JWT_SECRET`、`JWT_SECRET_ID`、`JWT_SECRET_KEYRING`、`JWT_PUBLIC_KEY`、`JWT_PUBLIC_KEY_PATH`、`JWT_PUBLIC_KEYRING`、`JWT_ISSUER`、`JWT_AUDIENCE`、`JWT_REQUIRE_EXP`、`JWT_REQUIRE_ISS` 和 `JWT_REQUIRE_AUD` 用于开启可选 JWT 角色校验；默认兼容 HS256，生产可切换 RS256/ES256 并使用公钥验签。
- RBAC 角色采用最小权限：`viewer` 只能读底库/任务/流/模型元数据；`operator` 可以推理/比对、读取管理状态和指标；`auditor` 可以读取管理状态、导出和指标，但没有修改权限；生物特征推理/比对要求 `operator`、`algorithm` 或 `admin`；调试模型输出需要模型写权限；管理导出/保留使用独立的管理权限。
- `AUTH_REQUIRED` 会在既没有 `API_TOKEN` 也没有 RBAC 凭证时让受保护接口失败关闭；Docker Compose 默认设为 `true`。
- `DEBUG_ENDPOINTS_ENABLED` 控制 `/debug/model-output`，默认 `false`。
- `ENABLE_API_DOCS` 控制 `/docs`、`/redoc` 和 `/openapi.json`；Docker Compose 在生产环境默认设为 `false`。
- `TRUSTED_HOSTS` 通过 `TrustedHostMiddleware` 执行 Host 头白名单；Docker Compose 默认允许回环地址和 worker 服务名。
- `TENANT_HEADER_REQUIRED` 会让 v1 租户接口在 Compose 部署中默认拒绝不携带 `X-Tenant-ID` 的请求。
- `JWT_REQUIRE_TENANT` 会让 RBAC JWT 默认通过 `tenant_id`、`tenant` 或 `tenants` claim 绑定请求租户。
- `PORTRAIT_STORAGE_BACKEND`、`PORTRAIT_VECTOR_BACKEND` 和 `PORTRAIT_OBJECT_STORAGE_BACKEND` 提供 PostgreSQL、pgvector/Qdrant 以及 S3 兼容存储的生产后端适配器。
- `REQUIRE_ENCRYPTION` 会在缺少 `ENCRYPTION_KEY` 时让敏感载荷保护失败关闭；Docker Compose 默认设为 `true`。
- `ENCRYPTION_KEY_ID` 用于标记新加密载荷，而 `ENCRYPTION_KEYRING` 会在密钥轮换期间保留已退役密钥，供只读解密使用。
- `TASK_QUEUE_BACKEND`、`REDIS_URL` 和 `STREAM_EVENT_STATE_PATH` 定义了外部 worker 的任务队列和流事件契约。
- `model-capabilities.yml` 记录哪些模态是真实模型驱动，哪些仍是兜底或占位。
- `MODEL_CONFIG_READ_FAIL_CLOSED` 会让缺失、不可读或格式错误的 `models.yml` 在启动/重载时默认失败关闭，而不是静默以空配置运行。
- `RATE_LIMIT_PER_MINUTE` 和 `RATE_LIMIT_BURST` 开启按租户/路径的令牌桶限流；Docker Compose 默认每分钟 `120`，突发 `240`。
- `RATE_LIMIT_MAX_BUCKETS` 和 `RATE_LIMIT_BUCKET_TTL_SECONDS` 限定本地限流桶内存。
- `MAX_REQUEST_BODY_BYTES` 在路由处理器解析 JSON 或 multipart 之前就应用全局 HTTP 请求体上限；Docker Compose 默认 `805306368` 字节。
- `STATE_READ_FAIL_CLOSED` 会让现有本地 JSON 状态读取或结构失败默认失败关闭，而不是静默丢弃已持久化状态。
- `STATE_WRITE_FAIL_CLOSED` 会让本地 JSON 状态写入默认失败关闭。
- `SECURITY_HEADERS_ENABLED`、`CONTENT_SECURITY_POLICY` 和 `HSTS_*` 会加入加固后的默认 HTTP 响应头、CSP、跨域隔离头以及生产 HSTS。
- `MAX_PUBLIC_METADATA_BYTES`、`MAX_PUBLIC_METADATA_DEPTH`、`MAX_PUBLIC_METADATA_KEYS` 和 `MAX_PUBLIC_METADATA_STRING_LENGTH` 限制用户可写的元数据/设置字段。
- `MAX_AUDIT_PAYLOAD_BYTES`、`MAX_AUDIT_DEPTH`、`MAX_AUDIT_KEYS`、`MAX_AUDIT_LIST_ITEMS` 和 `MAX_AUDIT_STRING_LENGTH` 限制脱敏审计载荷的大小和复杂度。
- `API_LIST_DEFAULT_LIMIT`、`MAX_API_LIST_LIMIT`、`STREAM_EVENT_LIST_DEFAULT_LIMIT` 和 `MAX_STREAM_EVENT_LIST_LIMIT` 限制列表/导出响应大小。
- `/v1/admin/export`、`/v1/admin/audit/verify`、`/v1/admin/audit/events`、`/v1/admin/backups` 和 `/v1/admin/retention/cleanup` 提供按租户范围的运维导出、审计链校验、最近审计事件读回、备份快照读回和保留清理；审计链校验只返回脱敏 `path_hash`、记录数、错误数和链头哈希，审计事件接口只返回当前租户的白名单字段，支持按事件、结果、分类、request_id 和时间窗口过滤，并返回删除、导出、模型版本、保留清理分类汇总；导出支持 `updated_since` 增量过滤，`/v1/admin/backup` 可把导出快照写入本地或 S3 对象存储，`/v1/admin/backups` 只按白名单返回当前租户最近快照的时间、request_id、对象后端、字节数、增量起点和审计哈希。

生产集成产物：

- `requirements/prod-optional.txt` 列出 PostgreSQL 连接池、pgvector、Qdrant、S3、JWT、Redis 风格队列和 OpenTelemetry 所需的可选驱动。
- 通过设置 `INSTALL_PROD_OPTIONAL=true` 可以把这些可选驱动安装进 Docker 镜像。
- `tools/portrait_postgres_schema.sql` 提供 PostgreSQL/pgvector 的 schema，覆盖租户、人员、特征、阈值、任务、流、对象和审计事件。
- `tools/qdrant_collections.json` 记录人脸、人体、步态和衣着向量的 Qdrant collection 定义。
- `tools/portrait_production_readiness.py` 会报告模型文件、核心 SDK、模板和能力状态；在生产切换前请使用 `--strict`。
- `app/portrait_model_runtime.py` 通过现有 ONNXRuntime 注册表、GPU 队列、产物 hash 校验和 LRU 卸载路径接入 SCRFD、ArcFace、RTMPose、OpenGait、YOLO 人体检测、OSNet ReID 和 attribute ReID/衣着属性的生产适配器。
- 要启用真实模型，先添加对应的 ONNX 产物和 `models.yml` 条目，再把 `model-capabilities.yml` 里的对应项切到 `status: ready` 或 `production`，并把 `model_id` 设为已配置的模型 id，把 `adapter` 设为 `scrfd`、`arcface`、`rtmpose`、`opengait`、`yolo`、`reid` 或 `attribute_reid`。
- `examples/production-models.example.yml` 和 `examples/production-model-capabilities.example.yml` 展示了 SCRFD、ArcFace、RTMPose、OpenGait、YOLO person detection、OSNet body embedding 和 attribute ReID appearance 的生产契约示例。
- `tools/portrait_cutover_check.py --regression-manifest <held-out.yml> --validate-onnx --json` 是最终的真实模型门禁。它要求生产能力状态、非兜底模型 ID、匹配的产物 SHA-256、可选的 ONNXRuntime 加载检查，以及通过回归门禁。
- 精度回归门禁可直接参考 `python tools/portrait_model_regression.py --manifest examples/portrait-model-regression.example.yml --json`；上线前把示例分数替换成留出集评估结果。
- 长期运行的流拉取应在 API 进程外执行：`python -m app.portrait_stream_worker_daemon`；Docker Compose 已包含对应的 `portrait-stream-worker` 服务。
- 流 daemon 会先通过每个 stream 的原子 lock 文件做进程级兜底，再写入可过期的 stream worker lease，避免多个 daemon 进程重复拉取同一条流。
- `deploy/portrait-stream-worker.service` 和 `deploy/k8s-stream-worker.yaml` 提供流 worker 进程的 systemd 和 Kubernetes 部署模板，而 `tools/portrait_stream_worker_health.py --json` 会报告进程内 worker 心跳是否过期。
- `.github/workflows/ci.yml` 运行 Python 测试、Node SDK 契约测试、部署检查和示例回归门禁；`.github/workflows/security-audit.yml` 会在依赖变更时以及每周运行 `pip-audit`。

受限范围的工业级验收：

- 当真实模型替换、真实生产数据栈演练和真实运维演练被明确排除在本阶段之外时，请使用 `python tools/portrait_production_readiness.py --scope platform --strict` 作为平台验收门禁。
- 这个受限门禁仍会检查 API/安全契约、存储/向量/对象适配器、Python/Node SDK 产物、`models` 下的模型文件路径、审计和保留控制、脱敏、回滚行为以及生产配置默认值。
- 完整的 `python tools/portrait_production_readiness.py --strict` 仍然是最终切换门禁。在 `model-capabilities.yml` 还把 appearance、face detection、face embedding、gait 或 pose 标记为 fallback/placeholder 时，这个门禁预期会失败。
- 除非某个新功能扩展是为了解决已有的安全、兼容性或发布契约缺口，否则不要把它算进平台验收门禁。
- 受限平台验收清单记录在 [docs/operations/PLATFORM_ACCEPTANCE.md](docs/operations/PLATFORM_ACCEPTANCE.md)。

Ubuntu 服务器完整部署步骤见 [docs/deployment/DEPLOY_UBUNTU.md](docs/deployment/DEPLOY_UBUNTU.md)。

CPU-only（无 GPU / 无 CUDA）部署请使用独立编排：`docker compose -f docker-compose.cpu.yml up -d --build`。该编排使用 `Dockerfile.cpu` 和 `requirements-cpu.txt` / `requirements-cpu.lock`，容器内安装 CPU 版 `onnxruntime==1.20.1`，并在 compose 中固定 `FORCE_CPU="true"`，不会被通用 `.env` 里的 `FORCE_CPU=false` 覆盖。CPU 编排同时使用 `CPU_TRUSTED_HOSTS` 独立设置 Host 白名单，默认包含 `cpu-worker-0`，避免 GPU 部署的 `TRUSTED_HOSTS` 隐式覆盖 CPU 服务名。

运行镜像基于 `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`，容器内使用 Python 3.12。当前依赖固定为 `onnxruntime-gpu==1.20.1`，需要 CUDA 12.x 运行库与 cuDNN 9（CUDA 12 的镜像 tag 使用不带数字的 `-cudnn-`，内置 cuDNN 9）。宿主机 NVIDIA 驱动需满足 CUDA 12.4 的最低版本要求（Linux ≥ 550.54.14）。默认开启 `CPU_FALLBACK_ENABLED=true`，无 CUDA/GPU 时会使用 `CPUExecutionProvider` 继续推理；生产若必须强制 GPU，可设为 `false`。

## 目录结构

```text
gpu-services/
├── app/
│   ├── constants.py
│   ├── core.py
│   ├── geometry.py
│   ├── image_io.py
│   ├── image_preprocess.py
│   ├── inference*.py
│   ├── metrics.py
│   ├── model_config*.py
│   ├── model_package.py
│   ├── model_refs.py
│   ├── observability.py
│   ├── postprocess.py
│   ├── runtime*.py
│   ├── security.py
│   ├── server.py
│   ├── settings.py
│   ├── schemas.py
│   ├── video_io.py
│   ├── vision.py
│   ├── routes.py
│   ├── routes_health.py
│   ├── routes_model*.py
│   ├── routes_vision.py
│   ├── routes_person*.py
│   └── routes_debug.py
├── Dockerfile
├── docker-compose.yml
├── main.py
├── models.yml
└── requirements.txt
```

`main.py` 只保留 `uvicorn main:app` 的兼容入口。`app/server.py` 只负责应用装配、middleware 和 startup。`app/routes.py` 是总路由聚合器，模型管理、人像检测/ReID/轨迹、通用视觉、健康检查和调试接口继续拆分到独立路由模块。共享能力按运行时、模型配置、模型包、图像/视频 IO、预处理、后处理、指标、安全和观测日志拆分，`app/core.py`、`app/runtime.py`、`app/inference.py`、`app/vision.py`、`app/model_config.py` 保留为兼容导出层。

共享模型目录默认与本项目目录同级。例如：

```text
~/project/
├── gpu-services/
├── other-project/
└── models/
    └── your_project/
        └── yolov8n.onnx
```

## Ubuntu 服务器要求

- 已安装 NVIDIA 驱动，宿主机 `nvidia-smi` 正常。
- 已安装 Docker Engine 与 Docker Compose v2。
- 已安装 NVIDIA Container Toolkit。
- Docker 能运行 GPU 容器，例如：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## 首次部署

创建共享模型目录：

```bash
mkdir -p models
```

把模型放入共享目录。下面命令使用本仓库默认的 PortraitHub 模型 ID：

```bash
cp "$PWD/models"/*.onnx models/
```

构建并启动：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
curl http://127.0.0.1:9001/health
curl http://127.0.0.1:9001/ready
```

## 接口

健康检查：

```bash
GET /health
GET /ready
GET /ready/deep
GET /models
GET /model-configs
GET /metrics
GET /model-info?project_name=portrait_hub&model_name=yolov8n.onnx
```

推理：

```bash
POST /predict
Content-Type: application/json

{
  "project_name": "portrait_hub",
  "model_name": "yolov8n.onnx",
  "tensor_data": [[[[0.1, 0.2, 0.3]]]]
}
```

示例：

```bash
curl -X POST http://127.0.0.1:9001/predict \
  -H "Content-Type: application/json" \
  -d '{"project_name":"portrait_hub","model_name":"yolov8n.onnx","tensor_data":[[[[0.1,0.2,0.3]]]]}'
```

响应：

```json
{
  "status": "success",
  "model": "portrait_hub/yolov8n.onnx",
  "outputs": []
}
```

`outputs` 是 ONNX 模型的全部输出，按输出顺序返回二维或多维 list。

运维接口：

```bash
POST /infer/persons
POST /infer/person-embeddings
POST /infer/person-tracks
POST /infer/video/person-tracks
POST /infer/stream/person-tracks
POST /vision/infer
POST /vision/batch-infer
POST /v1/compare/batch
POST /v1/gallery/search/batch
POST /v1/admin/backup
WS   /ws/jobs/{job_id}
WS   /ws/streams/{stream_id}
POST /debug/model-output
POST /warmup
POST /reload
POST /unload
POST /reload-config
GET /model-package
```

控制台 `/console` 的“解析结果”视图会集中展示图片解析、视频解析和视频流解析输出：图片解析保留当前会话最近结果，视频解析汇总已完成任务的人像帧缩略图，视频流解析展示流状态、worker 会话和最近事件快照。人员库入库生成的底库特征缩略图会随人员详情返回，并在人员管理页的“特征图片列表”中按模态、质量分和模型信息展示，便于人工核验入库特征。

通用图像识别接口：

```bash
curl -X POST http://127.0.0.1:9001/vision/infer \
  -F "model_id=person_detector_default" \
  -F "files=@frame-001.jpg" \
  -F "confidence=0.25" \
  -F "iou=0.45"
```

`/vision/infer` 和 `/vision/batch-infer` 会按照 `models.yml` 中的 `task` 自动分派到检测、分类或 ReID 后处理。`model_id` 可以是 `aliases` 中的稳定别名，也可以直接使用 `project_name/model_name.onnx`。如果不使用别名，也可以传 `project_name` 和 `model_name`。单次请求默认最多 16 张图，可通过 `MAX_VISION_IMAGES` 调整。

v1 业务层也提供批量能力：`/v1/compare/batch` 支持同数量的 `image_a[]` / `image_b[]` 成对比对，`/v1/gallery/search/batch` 支持多张查询图批量检索。批量接口支持 `async_mode=true`：服务会立即返回 `batch_id` 和 Jobs 摘要，后台执行批量比对或图库检索，调用方可以继续通过 `/v1/jobs/{batch_id}` 和 `/v1/jobs/{batch_id}/result` 查询进度与结果。视频任务和流事件可分别通过 `/ws/jobs/{job_id}` 与 `/ws/streams/{stream_id}` 获得实时快照推送。

多人检测接口：

```bash
curl -X POST http://127.0.0.1:9001/infer/persons \
  -F "project_name=portrait_hub" \
  -F "model_name=yolov8n.onnx" \
  -F "confidence=0.25" \
  -F "iou=0.45" \
  -F "files=@frame-001.jpg" \
  -F "files=@frame-002.jpg"
```

`/infer/persons` 会在服务内完成图片解码、letterbox 预处理、YOLO 推理、person 类过滤和 NMS，只返回每帧的人体框，不再要求调用方解析 YOLO 原始 tensor。单次请求默认最多 16 张图，每张图默认最大 10MB，可通过 `MAX_PERSON_FRAMES` 和 `MAX_IMAGE_BYTES` 调整。

响应示例：

```json
{
  "status": "success",
  "model": "portrait_hub/yolov8n.onnx",
  "frame_count": 2,
  "person_count": 3,
  "frames": [
    {
      "frame_index": 0,
      "filename": "frame-001.jpg",
      "width": 1920,
      "height": 1080,
      "person_count": 2,
      "persons": [
        {
          "box": [100.5, 80.2, 230.1, 420.9],
          "score": 0.91,
          "class_id": 0,
          "class_name": "person"
        }
      ]
    }
  ]
}
```

ReID 向量接口：

```bash
curl -X POST http://127.0.0.1:9001/infer/person-embeddings \
  -F "project_name=portrait_hub" \
  -F "model_name=osnet_ibn_x1_0.onnx" \
  -F "include_vectors=true" \
  -F "files=@person-001.jpg" \
  -F "files=@person-002.jpg"
```

组合检测 + ReID 接口：

```bash
curl -X POST http://127.0.0.1:9001/infer/person-tracks \
  -F "detector_project_name=portrait_hub" \
  -F "detector_model_name=yolov8n.onnx" \
  -F "reid_project_name=portrait_hub" \
  -F "reid_model_name=osnet_ibn_x1_0.onnx" \
  -F "include_embeddings=false" \
  -F "files=@frame-001.jpg" \
  -F "files=@frame-002.jpg"
```

`/infer/person-tracks` 会先检测每帧人体，再裁剪人体并生成 ReID embedding。它不会伪造跨帧 `track_id`；调用方可以用返回的 `embedding_index`、`embedding_dim` 和可选 `embedding` 做自己的轨迹关联。

离线视频解析接口：

```bash
curl -X POST http://127.0.0.1:9001/infer/video/person-tracks \
  -F "file=@clip.mp4" \
  -F "frame_interval=15" \
  -F "max_frames=64" \
  -F "include_embeddings=false"
```

`/infer/video/person-tracks` 会上传视频文件、按帧间隔抽帧，再复用检测 + ReID 流水线。响应中的每帧会包含 `source_frame_index` 和可推导的 `source_seconds`。

视频流解析接口：

```bash
curl -X POST http://127.0.0.1:9001/infer/stream/person-tracks \
  -F "stream_url=rtsp://user:password@camera-host/stream1" \
  -F "frame_interval=15" \
  -F "max_frames=32" \
  -F "read_timeout_seconds=10"
```

`/infer/stream/person-tracks` 默认关闭，需要设置 `ALLOW_STREAM_URLS=true` 后才允许服务端主动拉取 RTSP/RTMP/HTTP/HTTPS 视频流。生产环境建议仅在可信内网启用，并通过网关限制可访问的摄像头地址。

模型输出调试接口：

```bash
curl -X POST http://127.0.0.1:9001/debug/model-output \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "project_name=portrait_hub" \
  -F "model_name=yolov8n.onnx" \
  -F "model_type=yolo" \
  -F "sample_values=12" \
  -F "file=@frame-001.jpg"
```

`/debug/model-output` 只返回输入 shape、输出 shape、min/max 和少量 sample 值，用于排查模型导出格式，不返回完整大 tensor。该接口默认关闭，需要显式设置 `DEBUG_ENDPOINTS_ENABLED=true` 后才可访问。

预热示例：

```bash
curl -X POST http://127.0.0.1:9001/warmup \
  -H "Content-Type: application/json" \
  -d '{"models":[{"project_name":"portrait_hub","model_name":"yolov8n.onnx"}]}'
```

模型元信息示例：

```bash
curl "http://127.0.0.1:9001/model-info?project_name=portrait_hub&model_name=yolov8n.onnx"
```

`/model-info` 会返回输入名、输入 shape、输入 dtype、输出名、输出 shape、provider、模型 hash、文件大小、加载时间和推理次数。

## 运行机制与容量规划

### 并发模型

- `docker-compose.yml` 默认启动 2 个 worker：`gpu-worker-0` 绑定 GPU 0，`gpu-worker-1` 绑定 GPU 1。
- 每个 worker 内只启动 1 个 Uvicorn 进程，避免同一 GPU 上被多个进程重复加载模型。
- 每个 worker 使用 `--limit-concurrency 100` 限制进入服务层的并发请求数量。超过该限制时，Uvicorn 会拒绝或延迟处理新请求，调用方应设置合理超时。
- `GPU_QUEUE_LIMIT` 默认是 `1`，表示单 worker 内同一时间只允许 1 个请求进入 GPU 推理段。这样延迟更可控，也更适合显存较紧张的 2080 Ti。
- 同一个模型在同一个 worker 内使用独立推理锁串行执行，避免同一 ONNX Runtime session 被并发调用导致显存峰值或上下文竞争。
- 不同模型在同一个 worker 内各自有锁，代码层允许并发进入不同模型的推理逻辑，但实际 GPU 仍是共享资源；如果不同模型同时推理导致显存或延迟波动，应在业务侧固定路由或降低并发。

### 模型缓存

- 模型按 `project_name/model_name` 懒加载，第一次请求时从共享模型目录加载到当前 worker 的进程内存和 GPU 显存。
- 缓存是 worker 本地缓存，不在两个 worker 之间共享。同一个模型如果同时打到 `gpu-worker-0` 和 `gpu-worker-1`，会分别在两张 GPU 上各加载一份。
- 模型加载后默认不会自动卸载，直到容器重启或进程退出。这可以降低后续请求延迟，但会持续占用显存。
- 首次并发请求同一个模型时有加载锁，只有一个请求执行加载，其它请求等待加载完成后复用缓存。
- `MAX_LOADED_MODELS=0` 表示不限制缓存模型数量。设置为正整数后会启用 LRU 淘汰，超过上限时卸载最久未使用的模型。
- 可以通过 `WARMUP_MODELS` 在容器启动时预热模型，格式为逗号分隔的 `project/model.onnx`，例如 `portrait_hub/yolov8n.onnx,portrait_hub/osnet_ibn_x1_0.onnx`。
- `/unload` 可以手动卸载单个模型，`/reload` 可以在替换 ONNX 文件后强制重新加载。
- 如果替换了共享模型目录里的 ONNX 文件，已加载 worker 不会自动热更新。需要重启对应 worker 才能加载新模型：

```bash
docker compose restart gpu-worker-0
docker compose restart gpu-worker-1
```

### 模型配置

`models.yml` 用于声明业务模型类型、输入尺寸、后处理参数、模型包侧车文件和别名。没有配置的模型仍可通过 `/predict` 使用，但业务接口建议显式配置。新版配置兼容旧字段，例如 `type`、`input_size`、`confidence`、`iou` 仍然可以使用。

```yaml
aliases:
  person_detector_default:
    target: portrait_hub/yolov8n.onnx
  person_reid_default:
    target: portrait_hub/osnet_ibn_x1_0.onnx

models:
  portrait_hub/yolov8n.onnx:
    task: detection
    type: yolo
    runtime: onnxruntime
    version: 1.0.0
    precision: fp32
    input:
      size: [640, 640]
      layout: nchw
      dtype: float32
      color: rgb
      resize: letterbox
      normalize: none
    output:
      format: yolo
      classes: coco
      class_filter: [person]
      confidence: 0.25
      iou: 0.45
      max_detections: 100
    artifact:
      model_card: yolov8n.model-card.yml
      labels: yolov8n.labels.txt
      sha256: ""
  portrait_hub/osnet_ibn_x1_0.onnx:
    task: reid
    type: reid
    input:
      size: [256, 128]
      normalize: imagenet
    output:
      format: embedding
      embedding_normalize: l2
```

`model-capabilities.yml` 中可以把 `person_detection` 指向 `person_detector_default`，把 `body_embedding` 指向 `portrait_hub/osnet_ibn_x1_0.onnx` 并设置 `adapter: reid`。v1 的 persons、compare 和 gallery body 链路会优先使用 YOLO 裁剪人体，再生成 OSNet 512 维 ReID 向量；模型不可用时回退到本地 64 维图像指纹。appearance 能力可以在补齐真实衣着属性/attribute ReID ONNX 后切到 `adapter: attribute_reid`，`/v1/infer/appearance`、`/v1/fusion/compare`、gallery appearance 入库和视频任务帧级 appearance 会统一走该生产入口，未就绪时继续回退到颜色直方图。

可以通过 `/model-configs` 查看当前加载的配置和别名，通过 `/reload-config` 在不重启容器的情况下重新读取配置，通过 `/model-package` 查看模型卡、labels、sha256 匹配状态等模型包信息。

### 测试与上线校验

本项目提供三类工程校验：

```bash
python -m pip install -r requirements/dev.txt
pytest -q
python tools/deploy_check.py --import-app
python tools/validate_model_package.py --config models.yml --models-root models --strict-hash --strict-sidecars
```

- `pytest` 覆盖 API 契约、路径安全、模型配置兼容解析、检测/分类/ReID 后处理和模型包校验脚本。
- `tools/deploy_check.py` 用于部署前静态检查，会验证关键文件、Python 语法、`models.yml`、Docker Compose GPU 配置和核心路由。
- `tools/type_check.py` 用于运行聚焦的 `mypy --strict` 类型门禁，当前覆盖本轮拆出的核心兼容模块和类型检查脚本自身。
- `tools/portrait_production_readiness.py --scope platform --strict` 用于平台级生产门禁，覆盖安全契约、部署模板、代码质量、共享状态锁、控制台静态资源和工具链。
- `tools/validate_model_package.py` 用于上线前校验算法侧交付的模型包。生产上线建议加 `--strict-hash --strict-sidecars`，要求 sha256、模型卡和 labels 齐全。
- `tools/regression_check.py` 用于固定样例回归检查，可以对运行中的服务发起 HTTP 请求，并按期望输出子集和浮点容忍阈值比对。
- `tools/portrait_migrate.py`、`tools/portrait_backup_scheduler.py` 和 `tools/load_test.py` 分别用于数据迁移、备份调度和 HTTP 压测；`tools/portrait_migrate.py gallery-to-vector --dry-run --skip-load-state` 可用于快速验证本地向量迁移路径。
- 对外接入应用密钥使用 `X-API-Key`，`service_smoke_test.py`、`regression_check.py` 和 `load_test.py` 均支持 `--auth-scheme api-key`；内部运维 JWT/全局 token 继续使用默认 Bearer。Postman 集合位于 `tools/portrait_hub_postman_collection.json`，变量为 `base_url`、`tenant_id` 和 `api_key`。
- 两个业务 demo client 位于 `examples/demo-clients/`：Python 默认 `tenant-a`，Node 默认 `tenant-b`，均通过 SDK 使用应用 API Key，可用 `--dry-run` 验证接入步骤。

服务启动后可以执行 HTTP 冒烟测试：

```bash
python tools/service_smoke_test.py \
  --base-url http://127.0.0.1:9001 \
  --token "$API_TOKEN" \
  --auth-scheme api-key \
  --require-ready \
  --model-id person_detector_default
```

Compose 生产默认关闭 `/openapi.json`，冒烟测试默认接受 404；如果需要验证 OpenAPI 路径契约，显式增加 `--check-openapi`。

默认开启 CPU fallback 后，本地开发没有 CUDA 时 `/ready` 也可以返回 200，并在真实模型加载时使用 `CPUExecutionProvider`。真实上线前如果必须确认 GPU/CUDA 路径，应设置 `CPU_FALLBACK_ENABLED=false` 后执行 `--require-ready`，并按需增加 `--deep-ready --load-models --dummy-inference`。

固定回归集 manifest 示例：

```yaml
tolerance: 0.001
cases:
  - name: health_contract
    method: GET
    path: /health
    expected:
      status: healthy

  - name: detector_sample
    method: POST
    path: /vision/infer
    form:
      model_id: person_detector_default
      confidence: "0.25"
      iou: "0.45"
    files:
      files: samples/frame_001.jpg
    expected_path: expected/frame_001.expected.json
```

执行：

```bash
python tools/regression_check.py \
  --manifest regression.yml \
  --base-url http://127.0.0.1:9001 \
  --token "$API_TOKEN" \
  --auth-scheme api-key
```

多 worker 运维控制可以使用：

```bash
python tools/worker_control.py --action health
python tools/worker_control.py --action reload-config --token "$API_TOKEN"
python tools/worker_control.py --action warmup --token "$API_TOKEN" --model portrait_hub/yolov8n.onnx
```

### 上线切换和回滚

`models.yml` 的 `aliases` 用于稳定暴露模型入口，例如 `person_detector_default`。新模型上线建议流程：

1. 把新模型包放入共享模型目录。
2. 在 `models.yml` 的 `models` 中增加新模型配置，`rollout.status` 先写 `candidate`。
3. 执行模型包校验和冒烟测试。
4. 使用别名切换接口把默认别名指向新模型。

查看别名：

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  http://127.0.0.1:9001/rollout/aliases
```

dry-run 切换：

```bash
curl -X POST http://127.0.0.1:9001/rollout/aliases/switch \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "person_detector_default",
    "target_model_id": "portrait_hub/person_detector_yolov8n_v1.1.0_fp32.onnx",
    "expected_current_target": "portrait_hub/yolov8n.onnx",
    "dry_run": true
  }'
```

确认后把 `dry_run` 改为 `false`。服务会写回宿主机挂载的 `models.yml`，并重新加载当前 worker 的配置；其它 worker 可通过 `tools/worker_control.py --action reload-config` 同步新配置。回滚到上一个目标：

```bash
curl -X POST http://127.0.0.1:9001/rollout/aliases/rollback \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"alias_name":"person_detector_default","dry_run":false}'
```

按权重灰度时，可以把同一个别名配置成多目标分流。`traffic_key` 相同的请求会稳定命中同一个目标；如果不传 `traffic_key`，服务会使用请求 ID：

```bash
curl -X POST http://127.0.0.1:9001/rollout/aliases/weighted \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "person_detector_default",
    "expected_current_target": "portrait_hub/yolov8n.onnx",
    "dry_run": true,
    "targets": [
      {
        "target_model_id": "portrait_hub/yolov8n.onnx",
        "weight": 90,
        "status": "active"
      },
      {
        "target_model_id": "portrait_hub/person_detector_yolov8n_v1.1.0_fp32.onnx",
        "weight": 10,
        "status": "candidate"
      }
    ]
  }'
```

预览某个流量 key 会命中的目标：

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://127.0.0.1:9001/rollout/aliases/preview?alias_name=person_detector_default&traffic_key=customer-001"
```

业务调用 `/vision/infer` 时也可以传 `traffic_key`：

```bash
curl -X POST http://127.0.0.1:9001/vision/infer \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "model_id=person_detector_default" \
  -F "traffic_key=customer-001" \
  -F "files=@frame-001.jpg"
```

### 调用方建议

- 低延迟场景建议业务侧固定访问某一个 worker，避免同一模型在多张卡上重复冷启动。
- 高吞吐场景可以在业务侧或网关侧按 GPU worker 做负载均衡，但要接受每张卡各自缓存模型带来的显存占用。
- 调用方应设置连接超时和读取超时；读取超时需要覆盖“排队时间 + 推理时间 + 首次模型加载时间”。
- 推理接口不是幂等写操作，但计算结果可重复；网络超时后的重试可能造成同一个请求被重复推理，业务侧需要自行去重或接受重复计算成本。
- 大 tensor 会增加 JSON 解析、网络传输和内存复制成本。`MAX_TENSOR_ITEMS` 只限制元素数量，不限制 HTTP body 字节数；生产环境建议在反向代理层额外限制请求体大小。

### 显存与性能影响

- 显存主要由已加载模型、ONNX Runtime CUDA arena、输入输出 tensor、并发中的临时 buffer 决定。
- `gpu_mem_limit` 当前为 `0`，表示由 ONNX Runtime 自动管理显存。显存紧张时，优先减少单个 worker 上加载的模型数量或降低 batch size。
- `GPU_QUEUE_LIMIT` 控制单 worker 同时进入 GPU 段的请求数量，`MODEL_CONCURRENCY_LIMIT` 控制单模型默认并发，模型配置里的 `max_concurrency` 或 `runtime.max_concurrency` 可以单独覆盖。
- `MODEL_QUEUE_TIMEOUT_SECONDS` 大于 0 时，请求在模型队列或 GPU 队列等待超过该秒数会返回 503，避免无限堆积。
- FP16 ONNX 输入会按模型输入 dtype 自动 cast；如果模型输入是 `tensor(float16)`，图像预处理结果会在进入 session 前转为 `float16`。
- 配置 `runtime: tensorrt` 且 `ENABLE_TENSORRT=true` 时，服务会优先请求 ONNX Runtime 的 `TensorrtExecutionProvider`。TensorRT 不可用但 CUDA 可用时会回退 CUDA；无 CUDA 且 `CPU_FALLBACK_ENABLED=true` 时会回退 CPU。
- 当前接口使用 JSON 传输 tensor，简单通用但不是最高性能方案。如果单次输入很大或 QPS 很高，后续可考虑改成二进制协议、共享对象存储路径、gRPC，或让业务端只传图片路径并在服务端预处理。
- `/health` 表示服务进程正常，`/ready` 表示当前运行时 provider 可用。两者公开响应只返回最小状态信息；provider、模型状态和模型加载细节通过受保护的 `/ready/deep` 或 `/v1/admin/status` 查看。生产探活建议使用 `/ready`，强制 GPU 的环境应设置 `CPU_FALLBACK_ENABLED=false`。

### 可观测性

- 每个 HTTP 请求都会返回 `X-Request-ID`。调用方也可以传入 `X-Request-ID`，服务会沿用该值。
- `/predict` 和业务接口响应包含 `request_id`、是否冷加载、排队耗时、模型加载耗时、推理耗时、总耗时。
- 业务接口会额外记录 `decode_seconds`、`preprocess_seconds`、`postprocess_seconds`、`frame_count`、`person_count` 和 `inference_mode`。
- 服务日志使用 JSON 字符串记录关键事件，包括 `http_request`、`predict_completed`、`persons_infer_completed`、`embeddings_infer_completed`、`person_tracks_infer_completed`、模型加载和模型卸载。
- `/metrics` 受保护并需要 `metrics:read` RBAC 权限；它暴露 Prometheus 文本格式指标，包括请求量、推理失败数、模型加载数、缓存命中/未命中、已加载模型数、排队耗时总和、推理耗时总和、图片解码耗时、预处理耗时、后处理耗时、检测人数和处理帧数。
- `gpu_worker_gpu_queue_depth` 与 `gpu_worker_gpu_device_queue_depth{device="..."}` 使用运行时等待者计数，分别反映全局和按设备的 GPU 队列深度。
- `/metrics` 默认使用 `PROMETHEUS_METRICS_CACHE_SECONDS` 做短缓存，降低高频采集时的文本拼接开销。
- `/metrics` 还暴露模型维度指标，例如 `gpu_worker_model_config_info`、`gpu_worker_model_loaded_info`、`gpu_worker_model_inference_count_total`，标签包含 `model`、`task`、`version` 和 `status`。
- 设置 `OPENTELEMETRY_ENABLED=true` 且安装 optional 依赖后，服务会自动为 FastAPI 请求生成 OpenTelemetry span，并通过 OTLP HTTP exporter 输出。
- 别名切换、weighted rollout 和 rollback 会追加写入 `ROLLOUT_AUDIT_PATH` 指向的 JSONL 文件。Docker Compose 默认把审计文件放在宿主机 `./runtime-state/` 中，便于容器重建后继续保留。
- 管理员可通过 `GET /rollout/audit?limit=20` 查看最近非 dry-run 发布、灰度和回滚记录；接口只返回时间、事件、别名、目标、灰度权重和写入状态等白名单字段。

## 业务容器接入

业务容器如果和本服务在同一台 Docker 主机上，建议加入同一个网络：

```yaml
networks:
  gpu-bridge:
    external: true
```

然后通过容器名调用：

```text
http://gpu-worker-0:8000/predict
http://gpu-worker-1:8000/predict
```

宿主机本地调试端口：

- GPU 0 worker: `http://127.0.0.1:9001`
- GPU 1 worker: `http://127.0.0.1:9002`

端口默认只绑定 `127.0.0.1`，外部机器不能直接访问。需要跨机器访问时，建议放在受控内网网关或反向代理后面，再加鉴权和限流。

业务项目正式接入 `/v1` 人像中台接口时，建议从以下交付物起步：

- [PortraitHub 接入指南](docs/operations/INTEGRATION_GUIDE.md)：稳定 API、SDK 示例、错误处理、SLO 和接入验收清单。
- 接入中心提供 `GET /v1/access/error-codes`，控制台“错误码”页可直接查看稳定 `detail.code`、HTTP 状态和重试建议。
- [Nginx 网关模板](ops/nginx-gateway.example.conf)：内网 TLS、租户限流、请求体大小、`X-Request-ID` 透传和受保护指标入口示例。

## 配置项

通过 `docker-compose.yml` 的环境变量调整：

- `MODELS_HOST_DIR`: 宿主机模型目录，默认 `./models`，即本项目目录下的 `models`。
- `MODELS_ROOT`: 容器内模型目录，固定为 `/models`。
- `MODEL_CONFIG_HOST_FILE`: 宿主机模型配置文件，默认 `./models.yml`，Compose 会可写挂载到容器内。
- `MODEL_CONFIG_PATH`: 容器内模型配置文件路径，默认 `/workspace/models.yml`；本地直接运行默认读取当前目录 `models.yml`。
- `MODEL_CONFIG_READ_FAIL_CLOSED`: 模型配置文件缺失、损坏或根节点格式错误时是否启动/重载失败，默认 `true`。
- `CONFIG_HOT_RELOAD_ENABLED`: 是否启用 `models.yml` / `model-capabilities.yml` 轻量 mtime 热重载，默认 `true`。
- `LOG_LEVEL`: 日志级别，默认 `INFO`。
- `MAX_TENSOR_ITEMS`: 单次请求最大 tensor 元素数，默认 `12582912`。
- `MAX_LOADED_MODELS`: 单 worker 最大缓存模型数，默认 `0` 表示不限制；正整数启用 LRU 淘汰。
- `GPU_QUEUE_LIMIT`: 单 worker 同时进入 GPU 推理段的请求数，默认 `1`。
- `MODEL_CONCURRENCY_LIMIT`: 单模型默认并发限制，默认 `1`。
- `MODEL_QUEUE_TIMEOUT_SECONDS`: 模型队列和 GPU 队列等待超时秒数，默认 `0` 表示不超时。
- `CPU_FALLBACK_ENABLED`: 无 CUDA/GPU 或 CUDA session 初始化失败时是否回退 `CPUExecutionProvider`，默认 `true`；生产强制 GPU 时设为 `false`。
- `ENABLE_TENSORRT`: 是否允许 `runtime: tensorrt` 模型使用 TensorRT Execution Provider，默认 `false`。
- `TENSORRT_ENGINE_CACHE_ENABLE`: 是否启用 TensorRT engine cache，默认 `true`。
- `TENSORRT_ENGINE_CACHE_PATH`: TensorRT engine cache 路径，默认 `/tmp/tensorrt-engine-cache`。
- `RUNTIME_STATE_HOST_DIR`: 宿主机运行期状态目录，默认 `./runtime-state`。
- `ROLLOUT_AUDIT_PATH`: 灰度/别名变更审计 JSONL 文件路径，默认 `/workspace/runtime-state/rollout-audit.jsonl`。
- `PORTRAIT_REVIEW_STATE_PATH`: 轨迹审阅人工标注状态文件路径，默认 `/workspace/runtime-state/portrait-review-annotations.json`；这些标注进入评估数据池，不直接修改线上模型；评估中心会通过 `/v1/evaluation/track-reviews/summary` 展示租户内汇总、最近样本和证据索引，通过 `/v1/evaluation/datasets` 展示由标注池派生的动态数据集列表，并通过 `/v1/evaluation/threshold-recommendations` 给出只读阈值推荐；推荐不会自动写入阈值配置。
- `MAX_IMAGE_BYTES`: `/infer/persons` 单张上传图片大小上限，默认 `10485760`。
- `MAX_PERSON_FRAMES`: `/infer/persons` 单次请求图片数量上限，默认 `16`。
- `MAX_EMBEDDING_IMAGES`: `/infer/person-embeddings` 单次请求图片数量上限，默认 `64`。
- `MAX_PIPELINE_FRAMES`: `/infer/person-tracks` 单次请求帧数量上限，默认 `16`。
- `MAX_VIDEO_BYTES`: `/infer/video/person-tracks` 单个视频文件大小上限，默认 `104857600`。
- `VIDEO_FRAME_INTERVAL`: 离线视频默认抽帧间隔，默认 `15`。
- `MAX_VIDEO_FRAMES`: 离线视频单次最多抽取帧数，默认 `64`。
- `MAX_REQUEST_BODY_BYTES`: 全局 HTTP 请求体大小上限，默认 `805306368`；设为 `0` 可关闭。
- `STREAM_FRAME_INTERVAL`: 视频流默认抽帧间隔，默认 `15`。
- `MAX_STREAM_FRAMES`: 视频流单次最多抽取帧数，默认 `32`。
- `STREAM_READ_TIMEOUT_SECONDS`: 视频流单次读取软超时，默认 `10`。
- `STREAM_WORKER_POLL_INTERVAL_SECONDS`: 长驻 stream worker 轮询运行中流的间隔秒数，默认 `5`。
- `STREAM_WORKER_MAX_RECONNECTS`: stream worker 单次会话断线后的最大重连次数，默认 `3`。
- `STREAM_WORKER_LEASE_TTL_SECONDS`: stream worker 状态 lease 的 TTL 秒数，默认 `30`。
- `STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS`: 进程级 lock 文件超过该秒数后可被视为 stale 并清理，默认 `300`。
- `STREAM_WORKER_LOCK_DIR`: 每个 stream 的 daemon 进程级 lock 文件目录，容器部署建议放在 `/workspace/runtime-state/stream-worker-locks`。
- `ALLOW_STREAM_URLS`: 是否允许服务端主动拉取视频流 URL，默认 `false`。
- `WARMUP_MODELS`: 容器启动时自动预热的模型列表，格式为逗号分隔的 `project/model.onnx`。
- `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST`: 本地 tenant/path token bucket 限流速率和突发容量；Compose 默认 `120`/`240`，本地直接运行默认关闭。
- `RATE_LIMIT_MAX_BUCKETS` / `RATE_LIMIT_BUCKET_TTL_SECONDS`: 本地限流 bucket 的最大数量和空闲清理时间，避免大量 tenant/path 组合导致内存无界增长。
- `AUDIT_WRITE_FAIL_CLOSED`: 审计事件写入失败时是否让管理操作失败，默认 `true`。
- `STATE_READ_FAIL_CLOSED`: 已存在的本地 JSON 状态文件读取失败或根结构错误时是否启动/重载失败，默认 `true`。
- `STATE_WRITE_FAIL_CLOSED`: 本地 JSON 状态写入失败时是否让请求失败，默认 `true`。
- `PORTRAIT_GALLERY_WAL_ENABLED` / `PORTRAIT_GALLERY_WAL_COMPACT_EVERY`: 控制本地 JSON 底库增量 WAL 和 compact 阈值。
- `REQUIRE_ENCRYPTION`: 缺少 `ENCRYPTION_KEY` 时是否拒绝写入敏感 payload；Compose 默认 `true`，本地直接运行默认 `false`。
- `AUTH_REQUIRED`: 是否要求受保护接口必须存在可用鉴权后端；Compose 默认 `true`，本地直接运行默认 `false`。
- `DEBUG_ENDPOINTS_ENABLED`: 是否启用 `/debug/model-output`；默认 `false`。
- `ENABLE_API_DOCS`: 是否启用 `/docs`、`/redoc` 和 `/openapi.json`；Compose 默认 `false`，本地直接运行默认 `true`。
- `TRUSTED_HOSTS`: 允许的 HTTP Host header 列表，逗号分隔；Compose 默认允许 `127.0.0.1,localhost,gpu-worker-0,gpu-worker-1`，本地直接运行默认 `*`。
- `TENANT_HEADER_REQUIRED`: v1 多租户接口是否必须携带 `X-Tenant-ID`；Compose 默认 `true`，本地直接运行默认 `false`。
- `SECURITY_HEADERS_ENABLED` / `CONTENT_SECURITY_POLICY`: 是否启用安全响应头以及默认 CSP；Compose 默认启用。
- `HSTS_ENABLED` / `HSTS_MAX_AGE_SECONDS` / `HSTS_INCLUDE_SUBDOMAINS` / `HSTS_PRELOAD`: HTTPS 部署的 HSTS 响应头配置；Compose 默认启用，本地直接运行默认关闭。
- `JWT_AUDIENCE`: RBAC JWT 的目标受众，默认 `portrait-hub-api`。
- `JWT_ALGORITHM`: JWT 验签算法，默认 `HS256`，可配置 `RS256`/`ES256` 等非对称算法。
- `JWT_SECRET_ID` / `JWT_SECRET_KEYRING`: 当前 HS256 JWT secret 的 `kid` 以及旧 secret keyring，格式为 `kid=secret`，用于平滑轮换 JWT 签名密钥。
- `JWT_PUBLIC_KEY` / `JWT_PUBLIC_KEY_PATH` / `JWT_PUBLIC_KEYRING`: RS/ES JWT 的公钥或公钥环配置。
- `JWT_REQUIRE_TENANT`: RBAC JWT 是否必须通过 `tenant_id`、`tenant` 或 `tenants` claim 绑定请求租户；默认 `true`。
- `JWT_REQUIRE_EXP` / `JWT_REQUIRE_ISS` / `JWT_REQUIRE_AUD`: RBAC JWT 是否必须携带过期时间、签发方和受众；默认 `true`。
- `API_TOKEN`: 简单接口令牌；设置后业务接口、调试接口、模型管理接口和深度 ready 需要携带令牌。若 `AUTH_REQUIRED=true`，生产/容器启动前应设置 `API_TOKEN` 或启用可用 RBAC。
- `MAX_PUBLIC_METADATA_BYTES` / `MAX_PUBLIC_METADATA_DEPTH` / `MAX_PUBLIC_METADATA_KEYS` / `MAX_PUBLIC_METADATA_STRING_LENGTH`: 用户可写 metadata/settings 的大小和复杂度限制。
- `MAX_AUDIT_PAYLOAD_BYTES` / `MAX_AUDIT_DEPTH` / `MAX_AUDIT_KEYS` / `MAX_AUDIT_LIST_ITEMS` / `MAX_AUDIT_STRING_LENGTH`: 脱敏审计 payload 的大小和复杂度限制。
- `API_LIST_DEFAULT_LIMIT` / `MAX_API_LIST_LIMIT` / `STREAM_EVENT_LIST_DEFAULT_LIMIT` / `MAX_STREAM_EVENT_LIST_LIMIT`: 列表、导出和流事件响应的分页大小限制。
- `POSTGRES_POOL_MIN_SIZE` / `POSTGRES_POOL_MAX_SIZE`: PostgreSQL 连接池大小。
- `QDRANT_PREFER_GRPC`: Qdrant 客户端是否优先使用 gRPC，并复用单例客户端。
- `PROMETHEUS_METRICS_CACHE_SECONDS`: Prometheus 指标文本缓存秒数。
- `READY_CHECK_DEPENDENCIES`: `/ready` 是否检查 Postgres、向量库、对象存储、任务队列和磁盘空间。
- `OPENTELEMETRY_ENABLED` / `OTEL_SERVICE_NAME`: OpenTelemetry 自动埋点开关和服务名。
- `NVIDIA_VISIBLE_DEVICES`: 当前 worker 可见 GPU。
- `NVIDIA_DRIVER_CAPABILITIES`: 默认 `compute,utility`。

Uvicorn 启动参数在 `Dockerfile` 的 `CMD` 中配置：

- `--workers 1`: 每个容器单进程运行，保证进程内模型缓存和锁有效。
- `--limit-concurrency 100`: 限制单 worker 的服务层并发数量，可按 GPU 性能和业务超时调整。

启用鉴权后的请求示例：

```bash
curl -X POST http://127.0.0.1:9001/predict \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"project_name":"portrait_hub","model_name":"yolov8n.onnx","tensor_data":[[[[0.1,0.2,0.3]]]]}'
```

## 设计说明

- 每个 worker 只运行一个 Uvicorn 进程，避免多进程重复加载同一模型占用显存。
- 模型按 `project_name/model_name` 懒加载并缓存。
- 首次加载同一模型时使用加载锁，避免并发请求重复加载模型。
- 支持启动预热、手动预热、手动卸载、手动重载和 LRU 缓存上限。
- 支持模型配置文件，把 YOLO/ReID 的输入尺寸、类别 ID 和归一化策略显式化。
- 每个模型有独立 semaphore，默认串行，也可以通过模型配置或环境变量提高并发。
- 额外提供全局 GPU 推理信号量，避免不同模型同时挤占同一张 GPU。
- 支持 FP16 输入自动 cast，支持按模型配置启用 TensorRT Execution Provider。
- 提供 `tools/worker_control.py` 对多个 worker 统一执行健康检查、配置重载、预热、重载和卸载。
- 路径使用 `Path.resolve()` 限制在共享模型目录内，避免路径穿越。
- `/ready` 会检查当前推理 provider 是否可用；默认无 CUDA 时可回退 `CPUExecutionProvider`。`/ready/deep` 可进一步检查配置模型、加载模型和虚拟推理，并返回 `runtime_provider` 诊断字段。

## 压测记录模板

上线前建议为每个模型记录一次压测结果：

| 项目 | 数值 |
| --- | --- |
| 模型 | `portrait_hub/yolov8n.onnx` |
| GPU | 例如 `RTX 2080 Ti 11GB` |
| 输入 shape | 例如 `[1, 3, 256, 128]` |
| batch size | 例如 `1` |
| 冷启动耗时 | 例如 `2.3s` |
| 热缓存平均延迟 | 例如 `18ms` |
| P95 / P99 | 例如 `35ms / 60ms` |
| 稳定 QPS | 例如 `40` |
| 单模型显存占用 | 例如 `1.2GB` |
| 推荐 `GPU_QUEUE_LIMIT` | 例如 `1` |

## 常见问题

如果需要强制 GPU，但 `/ready/deep` 显示没有 `CUDAExecutionProvider`：

1. 确认宿主机 `nvidia-smi` 正常。
2. 确认 NVIDIA Container Toolkit 已安装。
3. 确认 `docker run --rm --gpus all ... nvidia-smi` 正常。
4. 确认 `docker compose` 版本支持 GPU device reservation。

如果显存不足：

1. 减少同时加载的模型数量。
2. 降低输入 batch size。
3. 将模型拆到不同 worker 或不同 GPU。
4. 考虑导出 FP16 或 TensorRT 版本。
