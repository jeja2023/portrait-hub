# 影鉴 PortraitHub 人像智能比对中台方案

## 1. 项目命名

建议项目名称：**影鉴 PortraitHub**

建议仓库名：`portrait-hub`

项目定位：面向图片、离线视频、实时视频流的人像推理、比对、检索与多模态融合中台。

对外描述：

> 影鉴 PortraitHub 是一个面向业务系统的人像智能中台，提供人脸、人体 ReID、姿态、步态、衣着等多模态推理能力，并支持 1:1 比对、1:N 检索、人员底库管理、视频任务分析和实时视频流分析。

## 2. 当前项目基础

当前项目已经具备成为人像智能比对中台的基础能力：

- 基于 FastAPI 的 HTTP 推理服务。
- 基于 ONNXRuntime 的模型推理运行时。
- 支持模型配置文件 `models.yml`。
- 已有人体检测模型 `yolov8n.onnx`。
- 已有人体 ReID 模型 `osnet_ibn_x1_0.onnx`。
- 已有 `/v1/vision/infer` 人体特征提取接口。
- 已有 `/v1/infer/tracks` 检测 + ReID 组合接口。
- 已有离线视频上传抽帧接口。
- 已有 RTSP/RTMP/HTTP/HTTPS 视频流抽帧接口。
- 已有基础指标、日志、模型加载和路径安全逻辑。

当前主要短板：

- 还不是完整比对平台，只返回 embedding，不负责最终相似度、阈值和身份判断。
- 没有人脸检测、对齐、人脸 embedding 和人脸质量评估。
- 没有人员底库和向量检索。
- 没有统一媒体解析层。
- 离线视频还不适合长视频异步分析。
- 视频流还不是长期会话式处理。
- 没有姿态、步态、衣着等辅助模态。
- 没有多模态融合评分。
- 没有管理控制台和 SDK。

## 3. 平台目标

将当前推理服务升级为独立的人像智能比对中台，供其它业务项目通过 API 或 SDK 调用。

目标能力：

- 图片、离线视频、实时视频流统一接入。
- 支持人脸、人像、骨骼、姿态、步态、衣着等多模态推理。
- 支持 1:1 比对。
- 支持 1:N 底库检索。
- 支持人员底库注册、更新、删除和重建索引。
- 支持离线视频异步分析。
- 支持实时视频流注册、启动、停止、状态查询和事件输出。
- 支持模型版本管理、阈值配置、质量评估和审计追踪。
- 提供 OpenAPI 文档、Python/Node SDK 和轻量管理控制台。

## 4. 总体架构

```text
业务系统 / 管理控制台 / SDK
  |
API Gateway / Auth
  |
PortraitHub API
  |
Media Ingest Layer
  |-- Image Decode
  |-- Video Decode
  |-- Stream Session
  |-- Frame Sampler
  |-- Quality Assessment
  |
Pipeline Orchestrator
  |-- Face Pipeline
  |-- Person Pipeline
  |-- Pose Pipeline
  |-- Gait Pipeline
  |-- Clothing Pipeline
  |
Model Runtime
  |-- ONNXRuntime GPU
  |-- TensorRT Optional
  |-- Model Registry
  |
Feature Store / Vector DB
  |-- Face Vectors
  |-- Body Vectors
  |-- Gait Vectors
  |-- Appearance Vectors
  |
Compare / Search / Fusion Engine
  |
Audit / Metrics / Logs / Admin Console
```

## 5. 核心模块

### 5.1 媒体解析层

媒体解析层是平台入口，负责将图片、视频和流统一转换为标准帧结构。

建议目录：

```text
app/media/
  image_decode.py
  video_decode.py
  stream_decode.py
  frame_sampler.py
  media_schema.py
  quality.py
```

统一内部结构：

```json
{
  "source_type": "image",
  "source_id": "src_xxx",
  "frame_index": 0,
  "pts_ms": 0,
  "width": 1280,
  "height": 720,
  "filename": "example.jpg"
}
```

图片解析要求：

- 支持 `jpg`、`jpeg`、`png`、`webp`、`bmp`。
- 可选支持 `heic`、`heif`。
- 修正 EXIF orientation。
- 支持灰度图、RGBA 转 RGB。
- 限制文件大小。
- 限制像素总量。
- 防止解压炸弹图片。
- 检查格式白名单。
- 输出图片质量评估结果。

离线视频解析要求：

- 支持 `mp4`、`avi`、`mov`、`mkv` 等常见格式。
- 获取视频元信息：宽、高、fps、帧数、时长、编码格式。
- 支持按帧间隔抽帧。
- 支持按时间间隔抽帧。
- 支持按最大帧数限制。
- 支持关键帧或检测触发抽帧。
- 长视频走异步任务，不阻塞 HTTP 请求。
- 支持任务进度、失败重试和临时文件清理。

视频流解析要求：

- 支持 RTSP、RTMP、HLS、HTTP-FLV 或普通 HTTP 视频流。
- 视频流必须按会话管理，而不是单次请求处理完。
- 支持连接超时、读取超时、断线重连。
- 支持帧缓冲队列。
- 支持丢帧策略和背压控制。
- 支持每路流资源限制。
- 支持 URL 白名单和内网 IP 段限制。
- 日志中隐藏流地址里的账号密码。

### 5.2 人脸模块

能力：

- 人脸检测。
- 人脸关键点。
- 人脸对齐。
- 人脸质量评估。
- 人脸 embedding。
- 1:1 人脸比对。
- 1:N 人脸检索。

推荐算法方向：

- 人脸检测：SCRFD、RetinaFace 或同类轻量模型。
- 人脸识别：ArcFace / InsightFace 体系。
- 人脸质量：模糊、亮度、姿态角、遮挡、人脸尺寸、检测分。

输出示例：

```json
{
  "faces": [
    {
      "box": [100.2, 80.5, 220.3, 240.8],
      "score": 0.992,
      "landmarks": [[120, 120], [180, 120], [150, 150], [125, 190], [175, 190]],
      "quality": {
        "score": 0.91,
        "blur": 0.08,
        "brightness": 0.74,
        "occlusion": 0.12
      },
      "embedding_dim": 512
    }
  ]
}
```

### 5.3 人体与人像 ReID 模块

能力：

- 人体检测。
- 人体框裁剪。
- 人体 ReID embedding。
- 跨摄像头相似度比对。
- 短时间轨迹关联。

当前项目已有基础：

- `yolov8n.onnx` 用于人体检测。
- `osnet_ibn_x1_0.onnx` 用于人体 ReID。
- ReID 输出已做 L2 normalize。

需要新增：

- `/v1/compare/persons`。
- 人体图片质量评估。
- 多人体场景的目标选择策略。
- 向量入库和检索。
- 按模型版本记录 embedding。

### 5.4 姿态与骨骼模块

能力：

- 2D 人体关键点。
- 姿态评分。
- 骨架结构输出。
- 可选动作或行为基础特征。

推荐算法方向：

- MMPose / RTMPose。
- 可导出 ONNX 的轻量姿态模型。

用途：

- 判断姿态是否适合人像比对。
- 为步态分析提供骨骼序列。
- 为行为分析和轨迹研判提供基础数据。

### 5.5 步态模块

能力：

- 从视频或视频流中生成 tracklet。
- 基于连续人体序列提取 gait embedding。
- 支持背影、远距离、低清人脸场景下的辅助识别。

输入要求：

- 步态不适合单张图片。
- 需要连续帧。
- 需要稳定的人体检测框或分割轮廓。
- 需要 tracklet 质量控制。

推荐算法方向：

- OpenGait。
- Gait3D 相关模型。

### 5.6 衣着与外观模块

能力：

- 衣服颜色识别。
- 上衣、下装、鞋、帽、包等属性识别。
- 人体解析或人体分割。
- 外观 embedding。

用途：

- 短时间跨镜追踪辅助。
- 多候选人排序辅助。
- 人工研判解释字段。

注意：

- 衣着不能作为长期身份特征。
- 换衣、遮挡、光照会显著影响结果。
- 融合评分中衣着权重应低于人脸、人体 ReID 和步态。

## 6. 比对与融合设计

### 6.1 单模态比对

人脸、人像 ReID、步态和衣着分别输出自己的相似度。

已做 L2 normalize 的 embedding 可以使用点积作为余弦相似度：

```text
similarity = dot(embedding_a, embedding_b)
```

也可以使用欧氏距离：

```text
distance = norm(embedding_a - embedding_b)
```

对于 L2 归一化向量，余弦相似度和欧氏距离具有单调关系。

### 6.2 阈值策略

不能用同一个阈值处理所有算法。

建议按模态、模型版本和业务场景配置阈值：

```yaml
threshold_profiles:
  face:
    strict: 0.82
    normal: 0.76
    loose: 0.70
  body:
    strict: 0.74
    normal: 0.68
    loose: 0.62
  gait:
    strict: 0.70
    normal: 0.64
    loose: 0.58
```

阈值必须通过真实验证集标定，不能只依赖经验值。

### 6.3 多模态融合

第一阶段可使用规则加权融合：

```text
final_score =
  face_score * face_weight * face_quality +
  body_score * body_weight * body_quality +
  gait_score * gait_weight * gait_quality +
  clothing_score * clothing_weight * clothing_quality
```

典型策略：

- 人脸质量高时，人脸为主。
- 人脸缺失或质量差时，增强人体 ReID 和步态权重。
- 步态需要连续帧，单张图片不参与步态评分。
- 衣着只作为短期辅助，不作为长期身份判断主依据。
- 低质量模态不应强行参与融合。

融合输出示例：

```json
{
  "passed": true,
  "final_score": 0.803,
  "threshold": 0.76,
  "threshold_profile": "normal",
  "modalities": {
    "face": {
      "score": 0.84,
      "quality": 0.91,
      "used": true
    },
    "body": {
      "score": 0.71,
      "quality": 0.82,
      "used": true
    },
    "gait": {
      "score": null,
      "quality": null,
      "used": false,
      "reason": "not_enough_frames"
    },
    "clothing": {
      "score": 0.78,
      "quality": 0.75,
      "used": true
    }
  }
}
```

## 7. API 设计

### 7.1 推理接口

```text
POST /v1/infer/faces
POST /v1/infer/persons
POST /v1/infer/pose
POST /v1/infer/appearance
POST /v1/infer/gait
```

### 7.2 比对接口

```text
POST /v1/compare/faces
POST /v1/compare/persons
POST /v1/compare/gait
POST /v1/fusion/compare
```

### 7.3 底库接口

```text
POST   /v1/gallery/enroll
POST   /v1/gallery/search
GET    /v1/gallery/{person_id}
PATCH  /v1/gallery/{person_id}
DELETE /v1/gallery/{person_id}
POST   /v1/gallery/reindex
```

### 7.4 离线视频任务接口

```text
POST /v1/jobs/video
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/result
POST /v1/jobs/{job_id}/cancel
```

### 7.5 视频流接口

```text
POST /v1/streams
GET  /v1/streams
GET  /v1/streams/{stream_id}
POST /v1/streams/{stream_id}/start
POST /v1/streams/{stream_id}/stop
GET  /v1/streams/{stream_id}/status
GET  /v1/streams/{stream_id}/events
```

### 7.6 模型和配置接口

```text
GET  /v1/models
GET  /v1/models/{model_id}
POST /v1/models/{model_id}/load
POST /v1/models/{model_id}/unload
GET  /v1/thresholds
PUT  /v1/thresholds/{profile}
```

### 7.7 健康与指标接口

```text
GET /health
GET /ready
GET /metrics
```

## 8. 典型对接流程

### 8.1 门禁系统 1:1 人脸核验

```text
1. 门禁设备拍摄现场照片。
2. 业务系统调用 /v1/compare/faces。
3. 平台提取现场人脸 embedding。
4. 平台与档案照或 person_id 对应底库特征比对。
5. 返回 similarity、passed、threshold、quality。
6. 门禁系统决定是否开门。
```

### 8.2 安防系统 1:N 人像检索

```text
1. 业务系统先通过 /v1/gallery/enroll 注册人员底库。
2. 平台保存人员信息和多模态 embedding。
3. 查询图片上传到 /v1/gallery/search。
4. 平台返回 top_k 候选人。
5. 业务系统展示候选人、分数、证据图和模型版本。
```

### 8.3 离线视频分析

```text
1. 业务系统上传视频到 /v1/jobs/video。
2. 平台返回 job_id。
3. 后台异步抽帧、检测、跟踪、提特征、检索。
4. 业务系统轮询 /v1/jobs/{job_id} 或接收 callback。
5. 平台返回轨迹、候选人、关键帧和时间戳。
```

### 8.4 实时视频流分析

```text
1. 业务系统注册 RTSP/RTMP/HLS 地址。
2. 调用 /v1/streams/{stream_id}/start 启动分析。
3. 平台持续拉流、抽帧、分析和检索。
4. 业务系统通过事件接口、消息队列或 callback 接收结果。
5. 异常断线时平台自动重连并记录状态。
```

## 9. 数据与存储设计

建议三类存储：

```text
PostgreSQL:
  人员信息、图库记录、任务记录、流配置、模型版本、阈值配置、审计日志

Vector DB:
  face/body/gait/appearance embedding

Object Storage:
  原图、视频、抽帧、结果快照、证据图
```

向量库可选：

- Qdrant。
- Milvus。
- pgvector。
- FAISS 本地索引。

推荐从 Qdrant 或 Milvus 开始。小规模部署也可以先用 pgvector 或 FAISS。

人员特征记录建议字段：

```json
{
  "person_id": "p_001",
  "modality": "face",
  "model_id": "face_arcface_r100",
  "model_version": "1.0.0",
  "embedding_dim": 512,
  "quality_score": 0.91,
  "source_id": "image_001",
  "created_at": "2026-06-05T10:00:00Z"
}
```

## 10. 模型配置设计

当前 `models.yml` 可扩展为多任务模型注册中心。

示例：

```yaml
models:
  portrait/face_arcface_r100.onnx:
    task: face_embedding
    type: arcface
    runtime: onnxruntime
    version: 1.0.0
    input:
      size: [112, 112]
      layout: nchw
      dtype: float32
      color: rgb
      normalize: arcface
    output:
      format: embedding
      embedding_normalize: l2
      metric: cosine
    thresholds:
      strict: 0.82
      normal: 0.76
      loose: 0.70

  portrait/person_osnet_ibn_x1_0.onnx:
    task: person_embedding
    type: reid
    runtime: onnxruntime
    version: 1.0.0
    input:
      size: [256, 128]
      layout: nchw
      dtype: float32
      color: rgb
      normalize: imagenet
    output:
      format: embedding
      embedding_normalize: l2
      metric: cosine
    thresholds:
      strict: 0.74
      normal: 0.68
      loose: 0.62
```

模型配置需要支持：

- 模型任务类型。
- 输入尺寸。
- 输入颜色空间。
- resize 方式。
- normalize 方式。
- 输出格式。
- embedding 归一化策略。
- 推荐阈值。
- 模型版本。
- sha256 校验。
- 模型卡。
- 灰度状态。
- 是否启动时预热。

## 11. 管理控制台

平台核心仍然是后端服务，但建议提供轻量管理控制台。

控制台用户：

- 开发人员。
- 运维人员。
- 算法人员。
- 系统集成人员。

控制台功能：

- 模型列表和加载状态。
- GPU、队列、耗时、错误率监控。
- 图片在线推理调试。
- 两图比对测试。
- 人员底库注册、删除、搜索测试。
- 离线视频任务查看。
- 视频流注册、启动、停止、状态查看。
- 阈值配置。
- 调用日志和审计记录。
- 模型版本和灰度状态查看。

不建议第一阶段开发完整业务前端，例如门禁前端、安防大屏、人员档案系统或复杂轨迹研判平台。这些应由具体业务系统负责。

## 12. SDK 与对接方式

建议提供：

- OpenAPI 文档。
- Postman 示例。
- Python SDK。
- Node.js SDK。
- Java/Go SDK 仅在明确存在调用方时再通过 OpenAPI 生成，不纳入当前主动维护范围。

Python SDK 示例：

```python
client = PortraitHubClient(
    base_url="http://portrait-hub:9001",
    api_token="xxx"
)

result = client.compare_faces(
    image_a="scene.jpg",
    image_b="profile.jpg",
    threshold_profile="normal"
)

if result.passed:
    print("same person", result.similarity)
```

## 13. 安全与合规

人脸、人像和 embedding 都属于敏感生物特征数据，必须重点处理安全与合规。

必须具备：

- API Token 或 JWT 鉴权。
- 可选 mTLS。
- 租户隔离。
- 角色权限控制。
- 操作审计。
- 图片、视频、embedding 加密存储。
- 日志脱敏。
- 删除人员和特征的接口。
- 数据留存策略。
- 模型版本追踪。
- 阈值配置追踪。
- 视频流 URL 白名单。
- 禁止任意外网 URL 拉取。
- 防 SSRF。
- 上传文件大小和格式限制。
- 临时文件定时清理。

审计日志建议记录：

- 调用方。
- request_id。
- 接口名称。
- 源类型。
- 模型版本。
- 阈值配置。
- 是否命中。
- 耗时。
- 错误信息。
- 操作时间。

## 14. 观测与运维

需要监控：

- API QPS。
- P50/P95/P99 延迟。
- 模型加载耗时。
- 推理耗时。
- 队列等待耗时。
- GPU 使用率。
- 显存使用率。
- 视频流在线数量。
- 视频流断线次数。
- 任务成功率。
- 任务失败率。
- 向量库查询耗时。
- 各模型错误率。

建议日志字段统一使用 JSON 格式：

```json
{
  "event": "fusion_compare_completed",
  "request_id": "req_xxx",
  "tenant_id": "tenant_a",
  "modalities": ["face", "body"],
  "final_score": 0.803,
  "passed": true,
  "total_seconds": 0.184
}
```

## 15. 部署形态

建议服务拆分：

```text
portrait-api:
  API、鉴权、任务提交、结果查询

portrait-worker:
  图片和视频任务推理

portrait-stream-worker:
  实时视频流会话处理

portrait-vector:
  向量库

portrait-db:
  PostgreSQL

portrait-storage:
  MinIO 或 S3

portrait-console:
  管理控制台
```

小规模部署可以合并为一个服务，后续按压力拆分。

## 16. 落地路线

### 第一阶段：平台基础重构

- 保留当前 FastAPI 和 ONNXRuntime 架构。
- 抽象统一媒体层。
- 保留现有 YOLO + ReID。
- 新增 `/v1/compare/persons`。
- 新增统一响应结构。
- 新增阈值配置结构。

交付结果：

- 当前项目从纯推理服务升级为基础比对服务。

### 第二阶段：人脸能力接入

- 接入人脸检测模型。
- 接入人脸关键点和对齐。
- 接入 ArcFace/InsightFace 类 embedding 模型。
- 新增人脸质量评估。
- 新增 `/v1/infer/faces`。
- 新增 `/v1/compare/faces`。

交付结果：

- 支持标准人脸 1:1 比对。

### 第三阶段：底库与检索

- 接入 PostgreSQL。
- 接入 Qdrant、Milvus、pgvector 或 FAISS。
- 新增人员底库。
- 新增 `/v1/gallery/enroll`。
- 新增 `/v1/gallery/search`。
- 支持按模型版本重建索引。

交付结果：

- 支持 1:N 人像检索。

### 第四阶段：离线视频和视频流平台化

- 离线视频改成异步任务。
- 新增任务状态和进度。
- 视频流改成注册、启动、停止、状态会话。
- 增加断线重连和流状态监控。
- 增加 callback 或事件输出。

交付结果：

- 支持长视频和实时流接入。

### 第五阶段：姿态、衣着、步态

- 接入姿态模型。
- 接入衣着属性或人体解析模型。
- 接入步态模型。
- 增加 tracklet 质量控制。
- 增加多模态结果结构。

交付结果：

- 支持复杂场景下的人像辅助识别。

### 第六阶段：融合与管理控制台

- 新增融合评分引擎。
- 支持不同业务阈值 profile。
- 新增管理控制台。
- 新增 SDK。
- 增加模型灰度、回滚和审计。

交付结果：

- 形成完整人像智能比对中台。

## 17. 风险与注意事项

算法风险：

- 不同模型的 embedding 不可直接混用。
- 模型升级后可能需要重建向量库。
- 人脸低质量时误判风险高。
- 人体 ReID 容易受衣着、遮挡、姿态和摄像头角度影响。
- 步态需要连续帧，单帧无效。
- 衣着只能作为短期辅助。

工程风险：

- 视频流会消耗稳定 GPU 和 CPU 资源。
- 长视频同步处理会导致接口超时。
- 多模型同时推理需要队列和限流。
- 向量库索引规模增长后需要分片和备份。
- 临时文件和抽帧结果必须清理。

安全风险：

- 视频流 URL 拉取存在 SSRF 风险。
- 生物特征数据必须加密和审计。
- 日志不能记录敏感图片、向量和完整流地址认证信息。
- 对外接口必须鉴权。

## 18. 推荐技术栈

后端：

- FastAPI。
- ONNXRuntime GPU。
- TensorRT 可选。
- OpenCV / PyAV / FFmpeg。
- PostgreSQL。
- Qdrant 或 Milvus。
- MinIO 或 S3。
- Redis / RabbitMQ / Kafka 可选。

前端：

- React 或 Vue。
- 用于轻量管理控制台。

部署：

- Docker。
- Docker Compose 用于小规模部署。
- Kubernetes 用于生产集群。
- Prometheus + Grafana 监控。

## 19. 结论

影鉴 PortraitHub 的核心不是单个算法，而是一套完整的人像智能中台能力：

```text
统一媒体解析
  + 多模态人像算法
  + 人员底库和向量检索
  + 比对与融合决策
  + 视频任务和视频流会话
  + 安全审计
  + 管理控制台
```

当前项目不需要推倒重来，可以继续作为推理底座演进。第一步应优先完成媒体层抽象、比对接口和人脸能力接入，随后再扩展底库检索、视频平台化、姿态、步态、衣着和多模态融合。

## 20. 参考方向

- InsightFace / ArcFace：人脸检测、对齐和识别方向。
- MMPose / RTMPose：人体姿态估计方向。
- OpenGait / Gait3D：步态识别方向。
- SCHP / Human Parsing：人体解析和衣着区域方向。
- Qdrant / Milvus / pgvector / FAISS：向量检索方向。
