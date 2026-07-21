# Ubuntu 服务器部署教程

本文档用于把本项目部署到 Ubuntu 服务器，通过 Docker Compose 启动两个 GPU 推理 worker，为其它业务容器提供 ONNX GPU 推理服务。

官方参考：

- Docker Engine Ubuntu 安装文档: <https://docs.docker.com/engine/install/ubuntu/>
- NVIDIA Container Toolkit 安装文档: <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>

## 1. 服务器准备

推荐环境：

- Ubuntu 22.04 LTS 或 24.04 LTS
- NVIDIA GPU 2 张，例如 2080 Ti
- NVIDIA 驱动正常
- Docker Engine + Docker Compose v2
- NVIDIA Container Toolkit
- 服务容器内运行 Python 3.12，镜像构建时会安装 Python 3.12

先登录服务器：

```bash
ssh your_user@your_server_ip
```

更新系统包索引：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release git
```

## 2. 安装或确认 NVIDIA 驱动

如果服务器已经装好驱动，直接检查：

```bash
nvidia-smi
```

能看到 GPU 列表、驱动版本、显存信息，就可以继续。

如果是纯 CPU 主机，可以跳过 NVIDIA 驱动检查，直接使用 CPU-only 编排：`docker compose -f docker-compose.cpu.yml up -d --build`。该编排使用 `Dockerfile.cpu`、`requirements-cpu.txt` 和 `requirements-cpu.lock`，安装 CPU 版 `onnxruntime==1.20.1`，并在 compose 中固定 `FORCE_CPU="true"`。即使共享 `.env` 仍保留 GPU 部署默认的 `FORCE_CPU=false`，CPU 容器也会强制使用 `CPUExecutionProvider`。CPU 版 Host 白名单使用 `CPU_TRUSTED_HOSTS`，默认包含 `cpu-worker-0`。

> 镜像基于 CUDA 12.4（`onnxruntime-gpu==1.20.1`，需 cuDNN 9）。宿主机 NVIDIA 驱动需满足 CUDA 12.4 的最低版本要求（Linux ≥ 550.54.14）。默认 `CPU_FALLBACK_ENABLED=true` 时，容器内 `CUDAExecutionProvider` 不可用会回退 `CPUExecutionProvider` 继续推理；生产若必须强制 GPU，请设为 `false`。`nvidia-smi` 右上角的 `CUDA Version` 表示驱动支持的最高 CUDA 版本，需 ≥ 12.4。

如果没有驱动，可以用 Ubuntu 推荐驱动安装：

```bash
sudo ubuntu-drivers devices
sudo ubuntu-drivers autoinstall
sudo reboot
```

重启后再次确认：

```bash
nvidia-smi
```

## 3. 安装 Docker Engine 和 Compose 插件

卸载可能存在的旧版本：

```bash
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  sudo apt-get remove -y "$pkg"
done
```

添加 Docker 官方 apt 源：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
```

安装 Docker：

```bash
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证：

```bash
docker --version
docker compose version
sudo docker run --rm hello-world
```

可选：允许当前用户免 `sudo` 运行 Docker：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker run --rm hello-world
```

## 4. 安装 NVIDIA Container Toolkit

添加 NVIDIA 官方 apt 源：

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
```

安装：

```bash
sudo apt-get install -y nvidia-container-toolkit
```

配置 Docker runtime 并重启 Docker：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

验证 GPU 容器：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

能在容器内看到 GPU 列表，说明 Docker GPU 环境正常。

## 5. 上传项目代码

方式一：使用 Git：

```bash
cd /opt
sudo mkdir -p /opt/gpu-services
sudo chown -R "$USER":"$USER" /opt/gpu-services
git clone <your_repo_url> /opt/gpu-services
cd /opt/gpu-services
```

方式二：从本地机器上传项目目录。下面命令在你的本地电脑执行，按实际本地路径调整：

```bash
scp -r /path/to/gpu-services your_user@your_server_ip:/opt/gpu-services
ssh your_user@your_server_ip
cd /opt/gpu-services
```

确认文件：

```bash
ls -la
```

应该能看到：

```text
Dockerfile
docker-compose.yml
main.py
requirements.txt
README.md
DEPLOY_UBUNTU.md
.env.example
```

## 6. 配置环境变量

复制配置模板：

```bash
cp .env.example .env
```

编辑：

```bash
nano .env
```

常用配置：

```dotenv
LOG_LEVEL=INFO
MODELS_HOST_DIR=./models
MODEL_CONFIG_HOST_FILE=./models.yml
MODEL_CONFIG_PATH=/workspace/models.yml
MODEL_CONFIG_READ_FAIL_CLOSED=true
MAX_TENSOR_ITEMS=12582912
MAX_LOADED_MODELS=0
GPU_QUEUE_LIMIT=1
MODEL_CONCURRENCY_LIMIT=1
MODEL_QUEUE_TIMEOUT_SECONDS=0
ENABLE_TENSORRT=false
ALLOW_STREAM_URLS=false
STREAM_WORKER_LEASE_TTL_SECONDS=30
STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS=300
RUNTIME_STATE_HOST_DIR=./runtime-state
STREAM_WORKER_LOCK_DIR=/workspace/runtime-state/stream-worker-locks
STATE_READ_FAIL_CLOSED=true
ROLLOUT_AUDIT_PATH=/workspace/runtime-state/rollout-audit.jsonl
PORTRAIT_STORAGE_BACKEND=postgres
POSTGRES_DSN=postgresql://portrait:change-me@postgres.internal:5432/portrait
PORTRAIT_OBJECT_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://minio.internal
S3_REGION=us-east-1
S3_BUCKET=portrait-hub
S3_ACCESS_KEY_ID=change-me
S3_SECRET_ACCESS_KEY=change-me
ENCRYPTION_KEY=change-me-to-a-different-long-random-secret
ENCRYPTION_KEY_ID=primary
ENCRYPTION_KEYRING=
REQUIRE_ENCRYPTION=true
ANALYSIS_ARCHIVE_ENABLED=true
ANALYSIS_ARCHIVE_PREVIEW_MAX_SIDE=480
WARMUP_MODELS=
API_TOKEN=change-me-to-a-long-random-token
GPU_WORKER_0_DEVICE=0
GPU_WORKER_1_DEVICE=1
```

说明：

- `API_TOKEN` 建议生产环境设置为长随机字符串。
- `MODEL_CONFIG_HOST_FILE` 默认指向当前目录的 `models.yml`，该文件会可写挂载进容器；别名切换、灰度和回滚写回后可持久化。
- `MODEL_CONFIG_READ_FAIL_CLOSED=true` 会让缺失、损坏或根节点格式错误的 `models.yml` 直接导致启动/重载失败，避免静默退化为空配置。
- `RUNTIME_STATE_HOST_DIR` 默认保存审计日志等运行期文件，容器重建后不会丢失。
- `STATE_READ_FAIL_CLOSED=true` 会让已有 JSON 状态文件损坏或根结构错误时直接失败，避免重启后静默丢失 gallery、任务、流和阈值状态。
- `ANALYSIS_ARCHIVE_ENABLED=true` 会让图片、离线视频和每个实时流完成批次写入统一解析档案。生产使用 `PORTRAIT_STORAGE_BACKEND=postgres` 时索引进入 PostgreSQL；本地 JSON 存储配置下索引位于 `PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH` 指定的 SQLite 文件。
- `PORTRAIT_OBJECT_STORAGE_BACKEND=s3` 用于长期保存完整结果图、预览图和视频源文件。API、视频 worker 与流 worker 必须共享同一个 PostgreSQL、S3 bucket 和加密密钥配置。
- `REQUIRE_ENCRYPTION=true` 时必须设置 `ENCRYPTION_KEY`。它与 `API_TOKEN` 必须使用不同的随机值；轮换后把旧密钥按 `key_id=secret` 写入 `ENCRYPTION_KEYRING`，直到全部历史对象完成重写。详细格式见 `docs/security/docs-security.md`。
- `STREAM_WORKER_LOCK_DIR` 建议跟随 `RUNTIME_STATE_HOST_DIR` 持久化；多个 stream daemon 共享同一运行期状态时，应共享该 lock 目录，`STREAM_WORKER_LEASE_TTL_SECONDS` 和 `STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS` 控制 lease 过期与 stale lock 回收。
- `WARMUP_MODELS` 可写成 `portrait_hub/yolov8n.onnx,portrait_hub/osnet_ibn_x1_0.onnx`。
- `MODEL_CONCURRENCY_LIMIT` 和 `GPU_QUEUE_LIMIT` 建议先保持 `1`，压测后再提高。
- `ENABLE_TENSORRT=true` 只在确认容器内 ONNX Runtime 暴露 `TensorrtExecutionProvider` 后开启。
- 如果服务器只有 1 张 GPU，先删除或注释 `docker-compose.yml` 里的 `gpu-worker-1` 服务，或者只启动 `gpu-worker-0`。

### 6.1 从 0.8.x 升级到 0.9.0

`0.9.0` 删除旧结果历史接口和存储实现，不迁移旧记录，也不会读取旧 JSON/PostgreSQL 图片历史。升级窗口按以下顺序执行：

1. 停止所有 API、视频 worker 和流 worker，备份当前 PostgreSQL、`runtime-state` 和对象存储。
2. 更新代码与镜像，确认 `.env` 已启用 PostgreSQL 索引和持久对象存储，并让所有服务使用相同配置。
3. 幂等应用数据库架构：

```bash
psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 -f tools/portrait_postgres_schema.sql
```

4. 重新构建并启动服务：

```bash
docker compose up -d --build
```

5. 分别完成一次图片解析、视频任务和视频流批次，然后检查：

```bash
curl -G "http://127.0.0.1:9001/v1/analysis/results" \
  -H "X-API-Key: ${API_TOKEN}" \
  --data-urlencode "source_type=image" \
  --data-urlencode "limit=1"
```

把 `source_type` 依次改为 `video` 和 `stream`，并使用响应中的 `content_url` 验证完整对象可读取。档案默认不设数量上限，上线前应为 PostgreSQL、S3 bucket 或 `OBJECT_STORAGE_DIR` 配置容量告警和联合备份。

生成随机 token 示例：

```bash
openssl rand -hex 32
```

## 7. 创建共享模型目录

默认模型目录与本项目目录同级。推荐结构：

```text
~/project/
├── gpu-services/
├── other-project/
└── models/
```

如果本项目在 `~/project/gpu-services`，模型目录就是：

```text
~/project/gpu-services/models
```

创建目录：

```bash
cd /opt/gpu-services
mkdir -p ./models
```

准备本地模型目录，例如：

```bash
mkdir -p /opt/model-upload/portrait_hub
cp /path/to/yolov8n.onnx /opt/model-upload/portrait_hub/
cp /path/to/osnet_ibn_x1_0.onnx /opt/model-upload/portrait_hub/
```

把模型复制到共享目录，目录必须是 `项目名/模型文件`：

```bash
mkdir -p ./models
cp /opt/model-upload/portrait_hub/*.onnx ./models/
```

检查共享目录内模型：

```bash
find ./models -maxdepth 2 -type f -name '*.onnx' -print
```

预期类似：

```text
./models/yolov8n.onnx
./models/osnet_ibn_x1_0.onnx
```

如果你之前已经按旧结构放过模型，例如：

```text
./models/portrait_hub/models/yolov8n.onnx
```

可以迁移成新结构：

```bash
for d in ./models/*/models; do
  [ -d "$d" ] || continue
  project="$(dirname "$d")"
  cp "$d"/*.onnx "$project"/
done
```

确认新结构没问题后，再按需清理旧的 `models` 子目录。

上线前建议先校验模型包。开发依赖只用于服务器验收，不会进入生产镜像：

```bash
python3 -m pip install -r requirements-dev.txt
python3 tools/validate_model_package.py \
  --config models.yml \
  --models-root ./models \
  --strict-hash \
  --strict-sidecars
```

如果模型刚开始接入、sha256 或侧车文件还没补齐，可以先去掉 `--strict-hash --strict-sidecars` 查看告警；正式上线前应补齐模型卡、labels 或类别定义、sha256。

同时执行本地部署静态检查：

```bash
python3 tools/deploy_check.py --import-app
pytest -q
```

## 8. 构建并启动服务

在项目目录执行：

```bash
cd /opt/gpu-services
test -f models.yml
mkdir -p ./runtime-state
docker compose up -d --build
```

构建镜像时会访问 Docker Hub、Ubuntu apt 源、deadsnakes Python 3.12 PPA 和 Python 包镜像源。Dockerfile 已将 Ubuntu apt 源切到清华镜像，并使用 `python3.12 -m ensurepip` 初始化 pip，避免访问 `bootstrap.pypa.io`。服务器如果不能访问外网，建议在能联网的机器上构建镜像后推送到内网镜像仓库。

### 8.1 离线/无网部署镜像搬运

如果部署服务器处于完全无互联网的环境（如内网物理机或隔离专网），无法执行在线 build。可以采用“有网构建 - 离线搬运”的方案：

1. **在有网的开发机上构建镜像**：
   ```bash
   docker compose build
   ```
   构建完成后，镜像会被打上 `portrait-hub:latest` 的标签。

2. **导出镜像为归档文件**：
   ```bash
   docker save -o portrait-hub-latest.tar portrait-hub:latest
   ```

3. **搬运至无网服务器**：
   通过移动硬盘或内网中转将 `portrait-hub-latest.tar` 归档文件以及项目代码拷贝到目标无网服务器。

4. **在无网服务器上导入镜像**：
   ```bash
   docker load -i portrait-hub-latest.tar
   ```

5. **在无网服务器上直接启动服务**：
   在项目目录内执行：
   ```bash
   docker compose up -d
   ```
   Docker Compose 会自动匹配并拉起本地已导入的 `portrait-hub:latest` 镜像，无需任何联网请求。

> **注意事项**：如果在无网环境下需要接入局域网内部视频流（如私有 RTSP 流地址），请务必在 `.env` 中将 `ALLOW_PRIVATE_STREAM_HOSTS` 设置为 `true`。否则，服务默认将拦截私网/局域网 IP 以防范 SSRF 安全漏洞。

### 8.2 查看容器：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f --tail=100
```

只看某个 worker：

```bash
docker compose logs -f --tail=100 gpu-worker-0
```

确认容器内 Python 版本：

```bash
docker exec gpu-worker-0 python --version
```

预期为 Python 3.12.x。

## 9. 验证服务

检查健康状态：

```bash
curl http://127.0.0.1:9001/health
curl http://127.0.0.1:9001/ready
```

如果启用了 `API_TOKEN`：

```bash
TOKEN="你的API_TOKEN"
# 如果这里使用接入中心生成的应用密钥，下面的 smoke/regression 命令增加 --auth-scheme api-key。
```

推荐用内置冒烟测试做一次完整接口检查：

```bash
python3 tools/service_smoke_test.py \
  --base-url http://127.0.0.1:9001 \
  --token "$TOKEN" \
  --auth-scheme api-key \
  --require-ready \
  --model-id person_detector_default
```

如果需要在上线前加载模型并跑虚拟推理：

```bash
python3 tools/service_smoke_test.py \
  --base-url http://127.0.0.1:9001 \
  --token "$TOKEN" \
  --auth-scheme api-key \
  --require-ready \
  --deep-ready \
  --load-models \
  --dummy-inference
```

如果已经准备固定样例回归集，可以继续执行：

```bash
python3 tools/regression_check.py \
  --manifest regression.yml \
  --base-url http://127.0.0.1:9001 \
  --token "$TOKEN" \
  --auth-scheme api-key
```

新模型通过校验后，可以用别名切换接口发布。先 dry-run：

```bash
curl -X POST http://127.0.0.1:9001/v1/admin/models/rollout/aliases/switch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "person_detector_default",
    "target_model_id": "portrait_hub/person_detector_yolov8n_v1.1.0_fp32.onnx",
    "expected_current_target": "portrait_hub/yolov8n.onnx",
    "dry_run": true
  }'
```

确认目标、模型包和回归结果都正确后，将 `dry_run` 改为 `false`。需要撤回时：

`docker-compose.yml` 默认把 `models.yml` 可写挂载到所有 worker。切换接口会写回这个宿主机文件并重载当前 worker；其它 worker 可以通过统一控制工具同步配置：

```bash
python3 tools/worker_control.py --action reload-config --token "$TOKEN"
```

```bash
curl -X POST http://127.0.0.1:9001/v1/admin/models/rollout/aliases/rollback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"alias_name":"person_detector_default","dry_run":false}'
```

如果需要按比例灰度，可以配置 weighted alias。下面示例表示 90% 旧模型、10% 新模型：

```bash
curl -X POST http://127.0.0.1:9001/v1/admin/models/rollout/aliases/weighted \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "person_detector_default",
    "expected_current_target": "portrait_hub/yolov8n.onnx",
    "dry_run": true,
    "targets": [
      {"target_model_id": "portrait_hub/yolov8n.onnx", "weight": 90, "status": "active"},
      {"target_model_id": "portrait_hub/person_detector_yolov8n_v1.1.0_fp32.onnx", "weight": 10, "status": "candidate"}
    ]
  }'
```

预览某个业务 key 的命中结果：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9001/v1/admin/models/rollout/aliases/preview?alias_name=person_detector_default&traffic_key=customer-001"
```

多个 worker 的健康检查、配置重载和预热可以用统一控制工具：

```bash
python3 tools/worker_control.py --action health
python3 tools/worker_control.py --action reload-config --token "$TOKEN"
python3 tools/worker_control.py --action warmup --token "$TOKEN" --model portrait_hub/yolov8n.onnx
```

如果 worker 端口不是默认的 `9001/9002`，可以重复传入 `--base-url`：

```bash
python3 tools/worker_control.py \
  --base-url http://127.0.0.1:9001 \
  --base-url http://127.0.0.1:9002 \
  --action aliases \
  --token "$TOKEN"
```

查看模型元信息：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9001/v1/models/portrait_hub/yolov8n.onnx"
```

查看模型配置：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9001/v1/models
```

深度 readiness 检查：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9001/ready/deep?load_models=true"
```

手动预热：

```bash
curl -X POST http://127.0.0.1:9001/v1/admin/models/warmup \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"models":[{"project_name":"portrait_hub","model_name":"yolov8n.onnx"}]}'
```

查看 Prometheus 指标：

```bash
curl http://127.0.0.1:9001/metrics
```

推理请求示例：

```bash
curl -X POST http://127.0.0.1:9001/predict \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Request-ID: test-001" \
  -H "Content-Type: application/json" \
  -d '{"project_name":"portrait_hub","model_name":"yolov8n.onnx","tensor_data":[[[[0.1,0.2,0.3]]]]}'
```

注意：上面的 `tensor_data` 只是格式示例，实际 shape 必须匹配 ONNX 模型输入。

多人检测请求示例：

```bash
curl -X POST http://127.0.0.1:9001/v1/vision/infer \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Request-ID: persons-test-001" \
  -F "project_name=portrait_hub" \
  -F "model_name=yolov8n.onnx" \
  -F "confidence=0.25" \
  -F "iou=0.45" \
  -F "files=@frame-001.jpg" \
  -F "files=@frame-002.jpg"
```

`/v1/vision/infer` 在统一 `data.results` 中返回每张图的检测、分类或 ReID 结果；检测任务包含人体框、置信度和类别信息。

ReID 向量请求示例：

```bash
curl -X POST http://127.0.0.1:9001/v1/vision/infer \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_name=portrait_hub" \
  -F "model_name=osnet_ibn_x1_0.onnx" \
  -F "include_vectors=true" \
  -F "files=@person-001.jpg"
```

检测 + ReID 组合请求示例：

```bash
curl -X POST http://127.0.0.1:9001/v1/infer/tracks \
  -H "Authorization: Bearer $TOKEN" \
  -F "detector_project_name=portrait_hub" \
  -F "detector_model_name=yolov8n.onnx" \
  -F "reid_project_name=portrait_hub" \
  -F "reid_model_name=osnet_ibn_x1_0.onnx" \
  -F "include_embeddings=false" \
  -F "files=@frame-001.jpg" \
  -F "files=@frame-002.jpg"
```

离线视频解析请求示例：

```bash
curl -X POST http://127.0.0.1:9001/v1/jobs/video \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@clip.mp4" \
  -F "sample_interval_seconds=0.5" \
  -F "batch_size=64" \
  -F "include_embeddings=false"
```

接口返回任务 ID；通过 `GET /v1/jobs/{job_id}` 查询状态，通过 `GET /v1/jobs/{job_id}/result` 获取检测、ReID 和轨迹结果。

视频流解析请求示例：

```bash
curl -X POST http://127.0.0.1:9001/v1/streams \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"stream_url":"rtsp://user:password@camera-host/stream1","name":"camera-1","settings":{"sample_interval_seconds":0.5,"batch_size":32,"read_timeout_seconds":10,"include_embeddings":false}}'
```

随后调用 POST /v1/streams/{stream_id}/start 启动分析，通过 GET /v1/streams/{stream_id}/events 或 WS /ws/streams/{stream_id} 读取 stream_analysis_completed 事件。

视频流解析默认关闭。需要在 .env 中设置 ALLOW_STREAM_URLS=true；开发机访问私网流时还需设置 ALLOW_PRIVATE_STREAM_HOSTS=true。生产环境建议只允许可信内网摄像头地址。

长驻视频流拉取推荐使用 python -m app.portrait_stream_worker_daemon 或 Compose 中的 portrait-stream-worker 服务。daemon 会为每条运行中的 stream 获取可过期的 state lease，并在 STREAM_WORKER_LOCK_DIR 下创建原子 lock 文件做进程级兜底，避免重复拉流。

#### 视频流无解析图片排查

先查询最近事件，而不是只读取默认的少量起始事件：

```bash
curl "http://127.0.0.1:9001/v1/streams/{stream_id}/events?limit=200" \
  -H "Authorization: Bearer $TOKEN"
```

- 出现 `stream_analysis_completed` 且 payload 中有 `thumbnails`，说明服务端已经产出图片；控制台会选择最新分析批次展示。
- 若只有 `stream_registered`、`stream_started`、`stream_worker_start_requested` 或 `stream_worker_session_started`，同时 `processed_frames=0`，优先检查 `portrait-stream-worker` 是否运行、是否持续重启以及日志中的拉流错误。
- API 与 worker 必须共享相同的状态后端和路径；JSON 后端需共享 `PORTRAIT_STREAMS_STATE_PATH`，容器部署需挂载同一数据卷。两者还必须使用一致的 `ALLOW_STREAM_URLS`、`ALLOW_PRIVATE_STREAM_HOSTS` 和 `STREAM_ALLOWED_HOSTS`。
- `worker_lease_active=false` 通常表示 daemon 未接管该流；若 daemon 已运行，检查 `STREAM_WORKER_LOCK_DIR` 权限、残留锁和 lease 过期时间。
- CPU 兜底推理首批结果可能需要 20～60 秒。启动后应等待一个完整批次，再以 `stream_analysis_completed` 事件确认结果，而不是仅凭流状态为“分析中”判断成功。

本地开发使用 `python dev_start.py` 时会自动启动 API 和流 worker，并把同一份 `.dev_start.env` 传给两个进程；生产环境仍应使用 Compose、systemd 或 Kubernetes 中的独立 worker 服务。

模型输出调试：

```bash
curl -X POST http://127.0.0.1:9001/debug/model-output \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_name=portrait_hub" \
  -F "model_name=yolov8n.onnx" \
  -F "model_type=yolo" \
  -F "file=@frame-001.jpg"
```

## 10. 业务容器接入

如果业务项目也运行在同一台 Docker 主机上，把业务容器加入 `gpu-bridge` 网络。

业务项目的 Compose 中加入：

```yaml
networks:
  gpu-bridge:
    external: true
```

服务中引用：

```yaml
services:
  your-business-service:
    networks:
      - gpu-bridge

networks:
  gpu-bridge:
    external: true
```

业务容器内调用：

```text
http://gpu-worker-0:8000/predict
http://gpu-worker-1:8000/predict
```

建议：

- 低延迟场景固定访问一个 worker，避免重复冷加载。
- 高吞吐场景在业务侧按 worker 做负载均衡。
- 调用方读取超时要覆盖排队时间、模型加载时间和推理时间。
- 生产环境建议统一携带 `X-Request-ID`，方便串联业务日志和推理日志。

## 11. 更新模型

把新 ONNX 文件复制进共享模型目录后，已加载模型不会自动热更新。

复制新模型：

```bash
mkdir -p ./models
cp /opt/model-upload/portrait_hub/yolov8n.onnx ./models/yolov8n.onnx
```

重载单个 worker 的模型：

```bash
curl -X POST http://127.0.0.1:9001/v1/admin/models/reload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_name":"portrait_hub","model_name":"yolov8n.onnx"}'
```

也可以直接重启 worker：

```bash
docker compose restart gpu-worker-0
docker compose restart gpu-worker-1
```

## 12. 常用运维命令

停止服务：

```bash
docker compose down
```

重启服务：

```bash
docker compose restart
```

重新构建：

```bash
docker compose up -d --build
```

查看资源：

```bash
docker stats
nvidia-smi
watch -n 1 nvidia-smi
```

进入容器：

```bash
docker exec -it gpu-worker-0 bash
```

容器内确认 ONNX Runtime provider：

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

## 13. 常见问题

### `/ready/deep` 显示没有 `CUDAExecutionProvider`

检查：

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
docker compose logs --tail=100 gpu-worker-0
```

通常原因：

- 宿主机驱动未安装或异常。
- NVIDIA Container Toolkit 未安装。
- 没有执行 `sudo nvidia-ctk runtime configure --runtime=docker`。
- Docker 没有重启。

### `model not found`

确认模型路径：

```bash
find ./models -maxdepth 2 -type f -print
```

服务要求路径：

```text
./models/<artifact.path from models.yml>
```

请求里的 `project_name` 和 `model_name` 必须和共享目录里的目录、文件名完全一致。

### 显存不足

处理方式：

- 降低 batch size。
- 减少同一个 worker 加载的模型数。
- 设置 `MAX_LOADED_MODELS` 启用 LRU 缓存淘汰。
- 固定某些模型只走某张 GPU。
- 使用 FP16 / TensorRT 优化后的模型。

### 首次请求很慢

首次请求会加载 ONNX 模型并初始化 CUDA provider。可以使用：

- `WARMUP_MODELS` 启动预热。
- `/v1/admin/models/warmup` 手动预热。

### 外部机器访问不到 9001/9002

Compose 默认绑定：

```yaml
127.0.0.1:9001:8000
127.0.0.1:9002:8000
```

这是为了避免推理服务直接暴露公网。跨机器访问建议使用内网反向代理或 API 网关，并开启鉴权、限流和请求体大小限制。

## 14. 上线检查清单

- `nvidia-smi` 正常。
- GPU 容器内 `nvidia-smi` 正常。
- `docker compose ps` 显示 worker healthy。
- `/ready/deep` 的 `runtime_provider.available_providers` 包含 `CUDAExecutionProvider`。
- `/v1/models/{model_id}` 能返回正确输入 shape 和 dtype。
- `/v1/admin/models/warmup` 成功。
- 业务请求 tensor shape 与模型输入一致。
- 已设置 `API_TOKEN`，并配置 `API_TOKEN_TENANT_ID`；只有受控平台运维场景才可显式设置 `API_TOKEN_ALLOW_TENANT_OVERRIDE=true`。
- 已设置调用方超时、重试和日志 request id。
- 已完成至少一次真实模型压测。

## 新版控制台灰度与回退

本节适用于 0.14.1。Console Next 是唯一生产控制台，根路径 / 是正式登录入口。0.14.1 修复退出后自动续登，支持按实际可见 GPU 配置模型调度，并优化按权重灰度的版本角色选择；公开 PortraitHub v1 API 保持兼容。0.13.0 和 0.14.0 的登录、导航和会话安全边界继续有效。

构建镜像时，Node 22 builder 会在根 npm workspace 中执行 npm ci 与 npm run console:build；运行镜像只复制 frontend/console-next/dist，不安装 Node，也不再包含旧版 frontend/console。非镜像部署必须先在仓库根目录执行相同构建命令，并确认 dist/index.html 与 dist/.vite/manifest.json 存在。

入口与回退：

- /：新版登录入口；默认显示本地管理员用户名/密码表单，已有有效浏览器会话时自动进入 /console。
- 默认本地账号为 admin / 123456，仅允许 loopback 登录；生产或远程使用必须更换 LOCAL_AUTH_PASSWORD 和至少 32 字节的 LOCAL_AUTH_SESSION_SECRET，否则本地登录自动禁用。
- 不需要本地账号时设置 LOCAL_AUTH_ENABLED=false；允许远程本地账号前必须设置 LOCAL_AUTH_ALLOW_REMOTE=true 并替换默认密码和会话密钥。
- 企业登录设置 OIDC_ENABLED=true，并配置 OIDC_ISSUER、OIDC_CLIENT_ID、OIDC_CLIENT_SECRET、OIDC_SESSION_SECRET、OIDC_ROLE_MAPPING 和回调地址；生产保持 Secure Cookie 与 HTTPS。
- /console：登录后的新版业务控制台，已是唯一生产入口。
- /console/next：新版直接验收别名；/console/legacy 已删除。
- CONSOLE_WORKBENCH_V2、CONSOLE_DEVELOPER_V2、CONSOLE_ADMIN_V2 与 CONSOLE_DEFAULT_VERSION 已删除，不再作为生产回退手段。
- 旧版删除后，控制台回退必须使用上一版镜像或受控静态构件；不得删除业务数据，也不得通过已移除的 legacy 环境变量回退。

浏览器账号会话使用 HttpOnly、SameSite Cookie；所有写请求必须携带与 CSRF Cookie 一致的 X-CSRF-Token。反向代理必须保留 Cookie、X-CSRF-Token、Host 和外部协议，并确保本地登录来源判断不被伪造的转发头绕过。OIDC 回调地址必须与身份平台登记值完全一致，外部角色/用户组必须显式映射为 admin、operator、algorithm、auditor、viewer。
上线前执行 `npm test`、`npm run console:e2e`、控制台/门禁定向或全量 `python -m pytest`、`python tools/deploy_check.py --json --import-app` 和 `python tools/portrait_production_readiness.py --scope platform --strict`。严格 readiness 必须为 strict_failure_count=0；真实 CSP 必须保持 script-src self，禁止 unsafe-inline 与 unsafe-eval。

0.14.1 额外上线检查：

- 确认 APP_VERSION、Python/npm 工程和四种 SDK 均为 0.14.1，受保护的 /ready/deep 返回 version=0.14.1。
- 整体发布新版 index.html、哈希 JS/CSS 与 portrait-hub-mark SVG；不要只替换 HTML 或单个资源。
- 在生产 OIDC 目录中验证已验证手机号、subject 绑定、成员启停和租户停用能即时收回权限。
- 用只读账号确认成员、模型、导出、备份、Webhook 和特征重建写操作均被权限拒绝。
- 对模型权重灰度、回滚、特征重建和数据清理执行预演与审计检查后再确认正式操作。
- 在模型中心确认 GPU 清单与容器实际暴露设备一致；需要显存信息时安装 `requirements/prod-optional.txt` 中的 `nvidia-ml-py`。
- 确认退出按钮清理浏览器会话并回到登录页，且不会因匿名开发会话自动恢复；确认灰度目标中的版本角色显示为“当前稳定版本/候选灰度版本”。

CONSOLE_WS_TICKET_TTL_SECONDS 默认 60 秒，ticket 单次消费并绑定租户、资源和权限。设置 `REDIS_URL` 后，ticket 使用 Redis TTL 与 Lua `GET + DEL` 原子消费，可供多 worker/多副本共享；未设置 Redis 时回退到进程内有界内存，仅适用于本地开发或单进程测试。生产多副本上线前必须验证 Redis 连通性、TTL、原子消费和故障告警。

生产观察、上一版镜像回退演练和多副本 WS ticket 前置条件以根目录《控制台前端重建方案.md》及 docs/frontend/CONSOLE_NEXT_ACCEPTANCE.md 为准。
