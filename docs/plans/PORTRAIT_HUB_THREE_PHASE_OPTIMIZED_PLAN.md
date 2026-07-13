# PortraitHub 三阶段落地优化方案

版本：`0.6.0`

日期：2026-07-13

本文档把当前三阶段方案重新收敛为一条可执行路线：先让本项目基于人体 ReID 完成自我闭环，再对其它项目提供稳定的 GPU 人像重识别推理服务，最后扩展为完整多模态 PortraitHub。方案包含后端、模型、数据栈、部署、对外契约、前端 UI、验收门禁和风险控制。

## 0.6.0 落地状态

截至 2026-07-13，本计划中的平台代码、控制台入口、接入中心、SDK 示例、调用日志、错误码目录、Webhook、SLO 面板、rollout 审计读回、轨迹审阅标注池、评估中心只读阈值推荐、审计链校验、审计事件筛选和备份快照读回已纳入 `0.6.0`。平台级 strict readiness 当前为 `ok=true`、`strict_failure_count=0`、`checks=180`。完整生产切换仍需真实模型包、真实 held-out 回归报告、cutover 证据、真实 staging 数据栈/服务联调、容量基线和备份恢复演练。

## 1. 当前判断

当前项目已经具备较完整的平台底座：

- FastAPI + ONNXRuntime GPU 推理服务。
- Docker Compose 多 GPU worker 拓扑。
- `/v1` 人像中台接口、图库、比对、视频任务、视频流、模型管理和阈值管理接口。
- Python/Node SDK、Postman、OpenAPI、生产就绪检查、模型回归工具、压测工具、审计和保留清理。
- 前端控制台已有登录、总览、图片解析、视频解析、视频流解析、解析结果、比对检索、人员库、模型管理、阈值、数据保留与备份、告警评估等页面骨架。

当前完整生产门禁的主要缺口集中在真实模型能力，而不是平台代码结构：

| 能力 | 当前状态 | 目标状态 |
| --- | --- | --- |
| 人体检测 | ready | production |
| 人体 ReID | ready | production |
| 人脸检测 | fallback | production |
| 人脸 embedding | fallback | production |
| 姿态 | placeholder | production |
| 步态 | fallback | production |
| 衣着/外观 | fallback | production |

因此推荐路线是：**ReID-only 先闭环，外部服务化再增强，多模态最后补齐**。

## 2. 总体目标

### 2.1 自我闭环使用

系统自身需要完成以下业务闭环：

1. 管理员登录控制台。
2. 创建或选择租户。
3. 注册人员底库，上传人体图片或关键帧。
4. 自动提取人体 ReID embedding，写入数据库和向量库。
5. 上传查询图、视频或视频流。
6. 系统完成检测、裁剪、特征提取、检索、聚合和排序。
7. 控制台展示候选人员、分数、阈值、质量、证据图和风险标签。
8. 运维人员查看 GPU、队列、模型、任务、流、告警和审计状态。
9. 算法/运维人员用评估集和线上反馈更新阈值、模型版本和回归门禁。

### 2.2 对外 GPU ReID 服务

系统需要给其它项目提供稳定服务：

- 统一 HTTP API 和 SDK。
- 稳定租户隔离。
- 可观测的 GPU 推理能力。
- 可控的批量、异步和视频流处理。
- 明确错误码、限流、超时、SLO 和回滚策略。
- 支持内网服务发现、网关转发或容器网络接入。

### 2.3 完整多模态人像中台

最终形成：

- 人脸、人体、姿态、步态、衣着/外观多模态推理。
- 1:1 比对、1:N 检索、多模态融合。
- 视频任务、实时流事件、tracklet 模板和跨帧证据聚合。
- 模型治理、灰度、回滚、回归评估、审计和合规。

## 3. 总体架构

```text
业务项目 / 控制台 / SDK
  |
API Gateway / Auth / Rate Limit / TLS
  |
PortraitHub API
  |-- v1 infer / compare / gallery / jobs / streams / models / admin
  |
任务编排层
  |-- 同步图片请求
  |-- 异步批量请求
  |-- 离线视频任务
  |-- 实时视频流 worker
  |
媒体解析层
  |-- 图片解码、质量评分、去重
  |-- 视频抽帧、帧选择
  |-- 流拉取、重连、背压
  |
模型运行时
  |-- ONNXRuntime GPU
  |-- TensorRT 可选
  |-- 模型预热、卸载、灰度、回滚
  |
业务算法层
  |-- 人体检测
  |-- 人体 ReID
  |-- 人脸、姿态、步态、衣着逐步接入
  |
数据层
  |-- PostgreSQL：租户、人员、任务、流、阈值、审计、对象元数据
  |-- pgvector 或 Qdrant：embedding 检索
  |-- S3/MinIO：图片、视频、证据图、导出快照
  |-- Redis：异步任务和事件转交
  |
观测与治理
  |-- Prometheus / Grafana
  |-- OpenTelemetry
  |-- 审计链
  |-- 备份、恢复、保留清理
```

## 4. 前端 UI 总体设计

当前控制台已经采用侧边栏工作台结构，建议继续沿用，不做营销页，不把控制台改成大屏展示页。它应该是密集、稳定、可扫描的业务工具。

### 4.1 导航信息架构

建议控制台保留并强化以下一级分组：

| 分组 | 页面 | 阶段 |
| --- | --- | --- |
| 总览 | 服务总览、SLO、快捷入口、最近任务 | 阶段一 |
| 解析处理 | 图片解析、视频解析、视频流解析、解析结果 | 阶段一 |
| 比对检索 | 人像比对、以图搜人、批量检索 | 阶段一 |
| 人员库 | 人员注册、人员管理、特征管理、重建索引 | 阶段一 |
| 接入中心 | API Key/JWT、SDK 示例、OpenAPI、调用日志、Webhook | 阶段二 |
| 运维治理 | 模型管理、阈值、灰度、备份、保留、告警、审计 | 阶段一到三 |
| 评估中心 | 数据集、回归结果、阈值标定、模型对比 | 阶段三 |
| 多模态分析 | 人脸、人体、步态、衣着、融合证据 | 阶段三 |

### 4.2 通用交互原则

- 控制台首页直接进入业务总览，不做欢迎落地页。
- 所有操作默认带当前租户，租户切换需要明确显示。
- 所有推理结果都展示 `model_id`、`model_version`、阈值 profile、质量分和 request_id。
- embedding 默认不在 UI 展示，只提供受权限保护的复制/下载调试入口。
- 图片、视频和流结果要有证据缩略图、候选排序和风险标签。
- 模型、阈值、数据清理和删除人员等高风险操作必须二次确认，并写入审计。
- 页面状态分为：空状态、加载中、成功、部分失败、失败、权限不足。
- 控制台内所有表格需要分页、过滤、复制 request_id、导出当前视图。

### 4.3 关键 UI 组件

| 组件 | 用途 |
| --- | --- |
| 租户栏 | 显示当前租户、认证方式、权限角色 |
| 服务健康条 | API、GPU、数据库、向量库、对象存储、队列状态 |
| 模型状态徽标 | active、candidate、fallback、placeholder、production |
| 证据图片网格 | 展示入库图、查询图、检测框、裁剪图、关键帧 |
| 候选结果表 | person_id、display_name、similarity、threshold、passed、quality、risk |
| 任务时间线 | 视频任务和流事件的状态变化 |
| 指标卡片 | QPS、p95、GPU 队列、显存、错误率、活跃流 |
| 审计抽屉 | 展示操作时间、租户、操作者、对象、结果、hash chain 状态 |
| 模型发布向导 | 上传/登记模型、校验、回归、预热、灰度、回滚 |

## 5. 阶段一：ReID-only 自我闭环 MVP

### 5.1 阶段目标

用当前 ready 的人体检测和人体 ReID 能力，先完成可生产演练的最小闭环：

- 人员入库。
- 人体特征提取。
- 1:1 人像比对。
- 1:N 以图搜人。
- 图片、离线视频、视频流三类输入。
- 控制台可操作、可查看、可排错。
- PostgreSQL + 向量库 + 对象存储 + Redis 的真实 staging 演练。

### 5.2 后端范围

| 模块 | 工作项 |
| --- | --- |
| 模型 | 固定 `person_detector_default` 和 `person_reid_default`，完成 warmup 配置和生产模型 hash 校验 |
| 图库 | 使用 `/v1/gallery/enroll`、`/v1/gallery/search`、`/v1/gallery/search/batch`、`/v1/gallery/reindex` |
| 比对 | 使用 `/v1/compare/persons` 和 `/v1/compare/batch` |
| 视频 | 使用 `/v1/jobs/video` 生成异步任务，结果可按 job 查询 |
| 视频流 | 使用 `/v1/streams` 注册、启动、停止和查询事件 |
| 数据 | PostgreSQL 保存业务元数据，pgvector 或 Qdrant 保存 body embedding |
| 对象 | MinIO/S3 保存入库图、查询快照、视频和证据图 |
| 阈值 | 先建立 body 的 `strict`、`normal`、`loose` 三组阈值 |
| 安全 | 强制认证、租户头、限流、上传大小、SSRF 防护、审计 |

### 5.3 前端 UI 范围

#### 总览页

展示：

- API 状态、GPU 状态、数据库、向量库、对象存储、队列。
- 今日推理量、检索量、错误率、p95、GPU 队列等待。
- 快捷入口：人员注册、以图搜人、视频解析、视频流解析。
- 最近任务、最近流事件、最近失败请求。

#### 人员注册页

表单字段：

- `person_id`，可选，不填则后端生成。
- `display_name`。
- `modality`，阶段一默认 `body`。
- 元数据 JSON。
- 多图上传。

结果展示：

- 每张图的质量分、重复标记、是否入库。
- 生成的 feature 数量。
- 对象存储状态。
- 向量库 upsert 状态。
- 审计 request_id。

#### 人员管理页

能力：

- 搜索 person_id/display_name。
- 查看人员详情。
- 查看特征列表、模型版本、质量分、来源图。
- 删除人员并清理对象。
- 按模型或模态触发 reindex。

#### 以图搜人页

流程：

1. 上传查询图。
2. 选择 `top_k` 和阈值 profile。
3. 显示候选列表。
4. 展示候选证据图、相似度、阈值、是否通过、质量和风险标签。

阶段一只展示 body 模态，不混入 fallback 人脸结果。

#### 人像比对页

流程：

1. 上传 A/B 两张图。
2. 选择阈值 profile。
3. 返回 similarity、threshold、passed、quality、risk、model_id。

UI 需要突出“通过/未通过”只是算法建议，不是最终身份裁决。

#### 图片/视频/流解析页

图片解析：

- 上传图片，显示人体检测框、裁剪图、embedding 元信息。

视频解析：

- 上传视频，创建 job。
- 展示进度、抽帧数量、候选帧、错误。
- 结果页展示关键帧和候选人员。

视频流解析：

- 注册流地址。
- 启停流。
- 展示 worker 心跳、最近事件、断线重连次数。

### 5.4 阶段一数据流

```text
上传入库图片
  -> 图片解码和质量评分
  -> 人体检测
  -> 人体裁剪
  -> OSNet ReID embedding
  -> 写入 PostgreSQL 人员/特征元数据
  -> 写入 pgvector 或 Qdrant
  -> 写入对象存储
  -> 控制台展示入库证据

上传查询图片
  -> 图片解码和质量评分
  -> 人体检测和 ReID
  -> 向量库 topK 检索
  -> 人员级聚合和阈值判断
  -> 控制台展示候选排序
```

### 5.5 阶段一验收

必须通过：

```powershell
python -m pytest -q
python tools\type_check.py
npm run check
python tools\deploy_check.py --import-app --json
python tools\portrait_production_readiness.py --scope platform --strict
```

staging 需要补充：

```powershell
$env:PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS = "1"
python -m pytest tests\test_production_integration_containers.py -q
python tools\service_smoke_test.py --base-url <staging-url> --token <token>
python tools\load_test.py --base-url <staging-url> --token <token>
```

业务验收：

- 新建租户后可完成 10 人以上入库。
- 每人至少 3 张入库图，能返回特征和证据图。
- 查询图能返回 topK 候选。
- 删除人员后，数据库、向量库、对象存储一致清理。
- 视频任务能完成并在控制台展示结果。
- 流任务能启动、停止并产生事件。
- 控制台所有阶段一页面有空状态、错误状态和 request_id。

### 5.6 阶段一交付物

- ReID-only staging 环境。
- 控制台阶段一 UI。
- 租户级 demo 数据。
- 阈值初始配置。
- 接口 smoke test 报告。
- 容量基线报告。
- 备份和恢复演练记录。

## 6. 阶段二：对外 GPU ReID 服务化

### 6.1 阶段目标

把阶段一能力包装成其它项目可以稳定依赖的共享 GPU 服务。

重点不是新增算法，而是增强接入体验、隔离性、稳定性和可观测性：

- 统一服务入口。
- 多项目租户隔离。
- API Key/JWT 管理。
- SDK 示例和接入文档。
- 负载均衡和 worker 健康路由。
- 批量、异步和流式结果输出。
- 对调用方可解释的错误码和 SLO。

### 6.2 服务拓扑

推荐生产拓扑：

```text
业务项目
  |
Internal DNS / API Gateway / Nginx / Envoy
  |
portrait-api service
  |
gpu-worker-0 / gpu-worker-1 / ...
  |
PostgreSQL + pgvector 或 Qdrant
MinIO/S3
Redis
Prometheus / Grafana / OTEL Collector
```

小规模部署可以保留当前 compose 多 worker；中大型部署建议改为 Kubernetes：

- API 和 GPU worker 分离。
- 每个 GPU worker 绑定一张卡。
- stream worker 独立部署。
- 网关只暴露 API。
- worker 通过 readiness、队列深度和模型热状态参与路由。

### 6.3 API 契约优化

对外推荐公开以下稳定能力：

| 场景 | 接口 |
| --- | --- |
| 人员入库 | `POST /v1/gallery/enroll` |
| 以图搜人 | `POST /v1/gallery/search` |
| 批量以图搜人 | `POST /v1/gallery/search/batch` |
| 1:1 人像比对 | `POST /v1/compare/persons` |
| 批量比对 | `POST /v1/compare/batch` |
| 图片解析 | `POST /v1/infer/persons` |
| 离线视频 | `POST /v1/jobs/video`、`GET /v1/jobs/{job_id}`、`GET /v1/jobs/{job_id}/result` |
| 实时流 | `POST /v1/streams`、`POST /v1/streams/{stream_id}/start`、`GET /v1/streams/{stream_id}/events` |
| 模型状态 | `GET /v1/models` |
| 阈值 | `GET /v1/thresholds` |

对外响应必须稳定包含：

- `request_id`
- `tenant_id`
- `model_id`
- `model_version`
- `threshold_profile`
- `similarity`
- `threshold`
- `passed`
- `quality`
- `risk_tags`
- `timing`
- `error.code`

### 6.4 接入中心 UI

阶段二需要新增“接入中心”，面向其它项目的开发者和集成方。

#### 应用凭证页

能力：

- 展示租户下的接入应用。
- 创建、禁用、轮换 API Key。
- 配置 JWT issuer/audience。
- 查看最近调用时间和错误率。

安全要求：

- 密钥只显示一次。
- 轮换操作写审计。
- 支持最小权限 scope，例如 `infer`、`compare`、`gallery:read`、`gallery:write`。

#### SDK 示例页

内容：

- Python SDK 示例。
- Node SDK 示例。
- curl 示例。
- 以图搜人最小示例。
- 批量异步示例。
- 视频任务轮询示例。

示例自动带入当前控制台配置，但敏感 token 默认遮罩。

#### API Playground

能力：

- 选择接口。
- 上传测试图片。
- 发起请求。
- 展示请求头、响应、耗时、错误码。

限制：

- 只能在开发或受控内网环境启用。
- 生产环境需要权限控制和审计。

#### 调用日志页

展示：

- request_id。
- 应用名。
- 接口。
- 状态码。
- 错误码。
- 耗时。
- 模型版本。
- GPU worker。

支持按租户、应用、接口、时间、错误码过滤。

#### SLO 面板

展示：

- 30 天成功率。
- p95/p99 延迟。
- GPU 队列等待。
- 错误预算燃烧。
- 活跃流数量。
- 各 worker 模型热状态。

### 6.5 运维增强

阶段二需要补齐：

- 统一网关和 TLS。
- mTLS 可选。
- API Key/JWT 双轨认证策略。
- 项目级限流和配额。
- worker 健康路由。
- 模型预热和常驻策略。
- 业务回调或 WebSocket/SSE 事件输出。
- SDK 版本发布流程。
- 对外错误码文档。
- 消费方接入 checklist。

### 6.6 阶段二验收

技术验收：

- 两个以上业务项目或 demo client 接入同一服务。
- 租户隔离测试通过，A 租户不可访问 B 租户数据。
- Python/Node SDK 完成 enroll、search、compare、video job 示例。
- 网关压测达到阶段一容量基线。
- worker 单点重启不影响整体可用性超过约定阈值。
- API Key 轮换不影响旧 token 宽限期内请求。

UI 验收：

- 接入中心能生成最小可运行示例。
- 调用日志能通过 request_id 定位请求。
- SLO 面板能展示 p95、错误率、队列和 worker 状态。
- 模型状态页能看到 active/candidate/fallback/production 状态。

交付物：

- 对外接入文档。
- SDK 使用文档。
- 网关部署模板。
- SLO 和错误码说明。
- 接入中心 UI。
- 两个业务 demo 的联调记录。

## 7. 阶段三：完整多模态 PortraitHub

### 7.1 阶段目标

在 ReID 服务稳定后，补齐人脸、姿态、步态、衣着/外观等生产模型，形成完整人像智能中台。

阶段三的核心不是简单增加模型，而是建立完整模型治理闭环：

1. 模型交付。
2. 模型包校验。
3. 回归评估。
4. 阈值标定。
5. 灰度发布。
6. 线上观测。
7. 回滚。
8. 评估数据沉淀。

### 7.2 模型接入顺序

建议按业务收益和风险排序：

| 优先级 | 能力 | 推荐模型方向 | 原因 |
| --- | --- | --- | --- |
| P0 | 人脸检测 | SCRFD ONNX | 人脸链路基础能力 |
| P0 | 人脸 embedding | ArcFace/InsightFace ONNX | 高质量正脸场景收益大 |
| P1 | 姿态 | RTMPose ONNX | 支持质量评估、步态和行为辅助 |
| P1 | 衣着/外观 | attribute ReID / human parsing | 短期辅助检索和解释 |
| P2 | 步态 | OpenGait/Gait3D ONNX | 视频/流长序列场景增强 |

### 7.3 后端增强

| 模块 | 工作项 |
| --- | --- |
| 模型配置 | 将 fallback/placeholder 能力改为 production，并配置真实 `model_id`、adapter、sha256 |
| 推理运行时 | 验证 SCRFD、ArcFace、RTMPose、OpenGait、attribute ReID 适配器 |
| 多模态融合 | 质量感知分数融合、冲突惩罚、风险标签 |
| 阈值系统 | 按模态、模型版本、业务 profile 标定阈值 |
| 图库 | 支持 face/body/gait/appearance 多 collection 或多维度索引 |
| 视频 | tracklet 模板、人脸关键帧、步态序列和衣着证据聚合 |
| 模型治理 | candidate -> staging -> production -> rollback 流程 |
| 评估 | 固定留出集、ROC、TAR@FAR、mAP、CMC、IDF1、质量分布 |

### 7.4 多模态分析 UI

阶段三新增“多模态分析”和“评估中心”。

#### 多模态比对页

页面结构：

- 左右两侧输入证据。
- 中间显示最终融合结论。
- 下方按模态展开：
  - face。
  - body。
  - gait。
  - appearance。
  - pose quality。

每个模态展示：

- 是否参与融合。
- 原始分数。
- 质量分。
- 调整后分数。
- 阈值。
- 冲突/风险标签。
- 使用的模型版本。

#### 视频轨迹审阅页

能力：

- 时间线查看关键帧。
- 展示 track_id、smoothed_box、稳定性、ID switch 风险。
- 选中 tracklet 后展示候选人员。
- 展示用于聚合的证据帧。
- 支持人工标记误检、错配、低质量。

这些人工标记进入评估数据池，不直接修改线上模型。

#### 模型发布中心

发布向导步骤：

1. 登记模型包。
2. 校验 ONNX、sha256、模型卡、labels、输入输出契约。
3. 运行 smoke test。
4. 运行回归 manifest。
5. 生成对比报告。
6. 预热 candidate。
7. 设置灰度比例。
8. 观察指标。
9. 切 production 或 rollback。

UI 显示：

- 当前 active 模型。
- candidate 模型。
- 回归指标。
- 线上错误率。
- p95 延迟。
- GPU 显存。
- 回滚按钮和审计记录。

#### 评估中心

页面：

- 数据集列表。
- 回归任务列表。
- ROC/TAR@FAR。
- 检索 mAP/CMC/Recall@K。
- 跟踪 IDF1/MOTA/HOTA proxy。
- 阈值推荐。
- 模型版本对比。

评估中心只展示汇总、脱敏样本和证据索引，不暴露敏感原始数据给无权限角色。

#### 合规与审计页

能力：

- 审计链校验。
- 删除请求记录。
- 数据保留策略。
- 备份快照。
- 导出记录。
- 模型版本追踪。

### 7.5 阶段三验收

必须完成：

```powershell
python tools\validate_model_package.py --config models.yml --strict
python tools\portrait_model_regression.py --manifest <real-held-out.yml> --json
python tools\portrait_cutover_check.py --regression-manifest <real-held-out.yml> --validate-onnx --json
python tools\portrait_production_readiness.py --strict
```

业务验收：

- `face_detection`、`face_embedding`、`pose`、`gait`、`appearance` 均为 `production`。
- 多模态融合能解释每个模态是否参与决策。
- 模型升级必须有回归报告和回滚路径。
- 控制台能完成模型发布、灰度观察和回滚。
- 审计链校验通过。
- 备份恢复演练通过。
- 真实视频/流场景完成端到端演练。

交付物：

- 多模态模型包。
- 模型评估报告。
- 阈值标定报告。
- 多模态控制台页面。
- 模型发布中心。
- 完整生产切换记录。

## 8. 横向工程要求

### 8.1 安全

- 生产必须开启认证。
- 租户头必须强制。
- JWT 需要 issuer、audience、exp 校验。
- 生物特征数据和对象载荷必须加密。
- 上传文件必须限制格式、大小和像素。
- 流地址必须启用 SSRF 防护和白名单。
- 审计写入失败时关键变更失败关闭。
- embedding 默认不出现在公开响应。

### 8.2 可观测性

核心指标：

- 请求总量和错误率。
- p50/p95/p99。
- GPU 队列等待。
- GPU 显存。
- 模型加载次数和失败次数。
- 各模型推理耗时。
- 向量库查询耗时。
- 视频任务成功率。
- 流 worker 心跳年龄。
- 审计链校验状态。

### 8.3 数据生命周期

需要覆盖：

- 入库对象写入。
- 特征写入。
- 删除人员时元数据、向量和对象一致清理。
- 备份。
- 恢复。
- 增量导出。
- 保留清理。
- 审计记录保留。

### 8.4 性能容量

阶段一先建立基线：

- 单图 ReID p95。
- 1:N 检索 p95。
- 批量检索吞吐。
- 视频任务每分钟处理帧数。
- 单 worker GPU 显存峰值。
- 队列等待 p95。

阶段二按业务项目拆配额：

- 每租户 QPS。
- 每租户并发。
- 每接口最大文件数。
- 每视频最大时长和帧数。
- 每流最大并发路数。

阶段三按模态拆成本：

- face/body/gait/appearance 单模态耗时。
- 多模态融合总耗时。
- 低质量输入拒绝率。

## 9. 推荐排期

| 时间 | 目标 | 主要交付 |
| --- | --- | --- |
| 第 1-2 周 | 阶段一环境闭环 | PostgreSQL/向量库/S3/Redis staging、ReID 入库检索跑通 |
| 第 3-4 周 | 阶段一 UI 收口 | 人员库、以图搜人、比对、视频任务、流事件、总览可用 |
| 第 5-6 周 | 阶段二服务化 | 网关、SDK 示例、接入中心、调用日志、SLO 面板 |
| 第 7-8 周 | 阶段二联调 | 至少两个业务 demo 接入、压测和租户隔离验收 |
| 第 9-12 周 | 阶段三模型接入 | SCRFD、ArcFace、RTMPose、appearance、gait 候选接入和回归 |
| 第 13-14 周 | 阶段三控制台 | 多模态分析、模型发布中心、评估中心 |
| 第 15 周后 | 生产切换 | 完整 strict 门禁、故障演练、备份恢复、灰度发布 |

排期可以压缩，但不建议跳过阶段一的真实数据栈演练。否则系统看起来接口完整，实际会在删除一致性、向量检索性能、对象清理和队列故障上暴露问题。

## 10. 风险与控制

| 风险 | 表现 | 控制方式 |
| --- | --- | --- |
| fallback 被误当生产能力 | 演示可用但精度不可控 | UI 明确标识 fallback/placeholder，完整门禁阻断 |
| 向量库性能不达标 | topK 延迟随数据量恶化 | staging 压测、pgvector EXPLAIN、必要时 Qdrant |
| 多模型抢 GPU | p95 抖动和 OOM | 模型级并发、队列超时、worker 分组、预热策略 |
| 视频流拖垮服务 | CPU/GPU 长期占用 | stream worker 独立、路数上限、背压、心跳监控 |
| 阈值不可靠 | 误报或漏报 | 留出集标定、按模型版本记录阈值 |
| 删除不一致 | 数据库删了但对象或向量残留 | 补偿事务、审计、对象清理测试、reindex |
| 外部项目误用接口 | 无租户、无超时、过大文件 | SDK 默认超时、限流、错误码、接入 checklist |
| UI 暴露敏感数据 | token、embedding、流凭据泄露 | 遮罩、权限控制、脱敏、审计 |

## 11. 最终完成定义

### 阶段一完成定义

- ReID 入库、检索、比对、视频任务和流事件在控制台可用。
- 真实 PostgreSQL、向量库、S3/MinIO、Redis staging 演练通过。
- 平台门禁全绿。
- 有初始阈值和容量基线。

### 阶段二完成定义

- 其它项目能通过网关、API 和 SDK 稳定调用。
- 接入中心、调用日志、SLO 面板可用。
- 租户隔离、限流、认证、错误码和 SDK 示例验收通过。
- 至少两个业务项目或 demo 完成联调。

### 阶段三完成定义

- 所有核心模态切到 production。
- 完整 `portrait_production_readiness.py --strict` 通过。
- 模型发布中心和评估中心可用。
- 多模态融合结果可解释、可回归、可回滚。
- 完成生产级压测、故障演练、备份恢复和审计链验证。

## 12. 下一步建议

建议立即启动阶段一：

1. 在 staging 启动 PostgreSQL + pgvector 或 Qdrant、MinIO、Redis。
2. 配置生产外置存储环境变量。
3. 用 10-50 人的小型真实或脱敏数据集完成入库和检索。
4. 优先补齐控制台中的人员注册、人员管理、以图搜人、比对结果和任务结果体验。
5. 生成第一份 ReID 阈值和容量基线报告。

这个阶段完成后，PortraitHub 就已经能作为“可自用、可对外”的 GPU 人体 ReID 服务。后续再把人脸、姿态、步态和衣着作为增强模态逐步并入，而不是让完整多模态成为第一阶段的阻塞项。
