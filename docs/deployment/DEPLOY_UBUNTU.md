# PortraitHub Ubuntu 部署教程

本文档面向当前 PortraitHub 项目，说明如何在 Ubuntu 服务器上通过 Docker Compose 部署 GPU 推理服务。文档按仓库版本 0.14.3、当前 Dockerfile、docker-compose.yml、.env.example 和生产门禁实现编写。

> 项目名称、仓库目录和 Compose 项目名统一为 portrait-hub。

官方安装入口：

- Docker Engine：<https://docs.docker.com/engine/install/ubuntu/>
- NVIDIA Container Toolkit：<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>

## 1. 当前部署架构

GPU Compose 定义四个服务：

| 服务                      |     默认设备 |           端口 | 用途                  |
| ------------------------- | -----------: | -------------: | --------------------- |
| gpu-worker-0              | 宿主机 GPU 0 | 127.0.0.1:9001 | API、控制台和同步推理 |
| gpu-worker-1              | 宿主机 GPU 1 | 127.0.0.1:9002 | API、控制台和同步推理 |
| portrait-video-job-worker | 宿主机 GPU 0 |             无 | 异步视频任务          |
| portrait-stream-worker    |     默认 CPU |             无 | 实时流守护进程        |

所有服务共享：

- 可写的宿主机 runtime-state/models.yml；
- 只读的宿主机模型目录；
- 可写的宿主机 runtime-state；
- 同一个 gpu-bridge Docker 网络；
- 同一份 .env 配置。

### 1.1 双 GPU 编号规则

默认每个 API 容器只看到一张物理 GPU。NVIDIA Runtime 会把容器可见的单张卡映射为容器内逻辑 GPU 0：

- gpu-worker-0 内的逻辑 GPU 0 对应宿主机物理 GPU 0；
- gpu-worker-1 内的逻辑 GPU 0 对应宿主机物理 GPU 1。

因此双卡单 worker 模式下保持：

```dotenv
GPU_WORKER_0_DEVICE=0
GPU_WORKER_1_DEVICE=1
GPU_DEVICE_IDS=0
```

不要把共享的 GPU_DEVICE_IDS 改成 0,1。模型中心显示 GPU 0 是正常现象，物理卡分配由 Compose 的 GPU_WORKER_*_DEVICE 决定。

### 1.2 推荐启动方式

首次部署只启动两个 API worker：

```bash
docker compose -p portrait-hub up -d gpu-worker-0 gpu-worker-1
```

确认同步推理稳定后，再根据业务需要启动视频或视频流 worker。直接执行不带服务名的 docker compose up -d 会启动全部四个服务。

## 2. 部署模式

### 2.1 单机验收模式

适合首次安装、内网验收和功能测试：

- PORTRAIT_RUNTIME_PROFILE=development；
- JSON/SQLite/本地对象目录；
- 本地持久任务队列；
- API 端口只绑定 127.0.0.1；
- 通过 SSH 隧道访问，不能直接暴露公网。

该模式不是多副本生产数据架构。两个 API worker 会共享本地文件，不能替代 PostgreSQL、Redis 和对象存储提供的一致性。

### 2.2 严格生产模式

当 PORTRAIT_RUNTIME_PROFILE=production 且 PRODUCTION_EXTERNAL_SERVICES_REQUIRED=true 时，应用启动会执行硬门禁，至少要求：

- PostgreSQL 业务存储；
- pgvector 或 Qdrant 向量后端；
- S3 兼容对象存储；
- Redis 任务队列；
- OpenTelemetry OTLP 采集端；
- API Token 或 RBAC；
- 生产模型能力门禁；
- 生产可选 Python 依赖。

ops/production.env.example 只是生产变量参考，不会被 Compose 自动读取。需要把适用配置合并到根目录 .env。

## 3. 服务器要求

推荐基线：

- Ubuntu 22.04 LTS 或 24.04 LTS；
- 两张 NVIDIA GPU；
- NVIDIA 驱动支持 CUDA 12.4；
- Docker Engine 与 Docker Compose v2；
- NVIDIA Container Toolkit；
- 至少 32 GiB 系统内存；
- 至少 50 GiB 可用系统盘空间，另行预留模型、日志、数据库和对象存储容量。

当前 GPU 镜像基于：

- nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04；
- Python 3.12；
- onnxruntime-gpu==1.20.1；
- Node.js 22.14 构建 Console Next。

宿主机不需要另装 CUDA Toolkit 或 cuDNN，只需要兼容驱动和 NVIDIA Container Toolkit。为避免兼容性不确定，建议 Linux 驱动不低于 550.54.14，并确认 nvidia-smi 显示的最高 CUDA 版本不低于 12.4。

## 4. 安装 NVIDIA 驱动

已有驱动时先检查：

```bash
nvidia-smi
nvidia-smi --query-gpu=index,name,memory.total,driver_version,uuid --format=csv
```

必须能看到预期数量的 GPU，且没有驱动通信错误。

没有驱动时可以使用 Ubuntu 推荐驱动：

```bash
sudo apt-get update
sudo apt-get install -y ubuntu-drivers-common
sudo ubuntu-drivers devices
sudo ubuntu-drivers autoinstall
sudo reboot
```

重启后重新执行 nvidia-smi。生产服务器不要同时安装发行版驱动、NVIDIA runfile 驱动和多个第三方驱动源。

## 5. 安装 Docker Engine

优先按照 Docker 官方 Ubuntu 文档安装。以下命令使用 Docker 官方 apt 仓库：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证：

```bash
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

如需免 sudo 使用 Docker：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

> Docker 用户组等价于主机 root 权限，只能授予受信任的运维账号。

## 6. 安装 NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

验证容器 GPU：

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

这里必须看到与宿主机一致的 GPU 数量和 UUID。

## 7. 获取 PortraitHub

```bash
sudo install -d -o "$USER" -g "$USER" /opt/portrait-hub
git clone https://github.com/jeja2023/portrait-hub.git /opt/portrait-hub
cd /opt/portrait-hub
git checkout main
git pull --ff-only origin main
```

确认当前版本和提交：

```bash
git status --short --branch
git log -1 --oneline
grep '^version = ' pyproject.toml
```

正式环境应记录部署提交 SHA。不要在服务器工作目录直接修改受版本控制的源码。

## 8. 准备环境配置

```bash
cd /opt/portrait-hub
cp .env.example .env
chmod 600 .env
```

.env 已被 Git 忽略。不要把真实密钥提交到仓库、镜像或工单。

分别生成 API Token、会话密钥和数据加密密钥：

```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

每个用途必须使用不同值。

### 8.1 双卡验收配置

编辑 .env，至少复核以下项目：

```dotenv
COMPOSE_PROJECT_NAME=portrait-hub

MODELS_HOST_DIR=./models
MODEL_CONFIG_HOST_FILE=./runtime-state/models.yml
MODEL_CONFIG_PATH=/workspace/models.yml
RUNTIME_STATE_HOST_DIR=./runtime-state

GPU_WORKER_0_DEVICE=0
GPU_WORKER_1_DEVICE=1
GPU_DEVICE_IDS=0
GPU_QUEUE_LIMIT=1
GPU_QUEUE_LIMIT_PER_DEVICE=1
MODEL_CONCURRENCY_LIMIT=1
MODEL_QUEUE_TIMEOUT_SECONDS=30
MAX_LOADED_MODELS=2
CPU_FALLBACK_ENABLED=false
FORCE_CPU=false
ENABLE_TENSORRT=false

INSTALL_PROD_OPTIONAL=true
PORTRAIT_RUNTIME_PROFILE=development
PRODUCTION_EXTERNAL_SERVICES_REQUIRED=true

AUTH_REQUIRED=true
API_TOKEN=替换为独立随机值
API_TOKEN_TENANT_ID=default
API_TOKEN_ALLOW_TENANT_OVERRIDE=false

LOCAL_AUTH_ENABLED=true
LOCAL_AUTH_ALLOW_REMOTE=true
LOCAL_AUTH_USERNAME=admin
LOCAL_AUTH_PASSWORD=替换默认密码
LOCAL_AUTH_SESSION_SECRET=替换为至少32字节随机值
LOCAL_AUTH_COOKIE_SECURE=false

REQUIRE_ENCRYPTION=true
ENCRYPTION_KEY=替换为独立随机值
ENCRYPTION_KEY_ID=primary

DEBUG_ENDPOINTS_ENABLED=false
ENABLE_API_DOCS=false
TENANT_HEADER_REQUIRED=true
TRUSTED_HOSTS=127.0.0.1,localhost,gpu-worker-0,gpu-worker-1

VIDEO_JOB_WORKER_IN_PROCESS=false
VIDEO_JOB_WORKER_GPU_DEVICES=0
STREAM_WORKER_FORCE_CPU=true
STREAM_WORKER_GPU_DEVICES=none
```

说明：

- 验收阶段通过 HTTP/SSH 隧道访问时，LOCAL_AUTH_COOKIE_SECURE=false；
- 正式 HTTPS 网关部署必须改为 LOCAL_AUTH_COOKIE_SECURE=true；
- Docker 部署使用本地账号时应设置 LOCAL_AUTH_ALLOW_REMOTE=true，因为应用看到的来源可能是 Docker 网桥地址；此时必须替换默认密码和默认会话密钥；
- 不需要本地账号时设置 LOCAL_AUTH_ENABLED=false，生产优先使用 OIDC；
- 强制 GPU 环境设置 CPU_FALLBACK_ENABLED=false，否则 CUDA 故障可能静默回退到 CPU。
- 视频 worker 通过 NVIDIA runtime 使用 VIDEO_JOB_WORKER_GPU_DEVICES；流 worker 默认强制 CPU，改用 GPU 时必须同时设置 STREAM_WORKER_FORCE_CPU=false 和 STREAM_WORKER_GPU_DEVICES=设备编号。

### 8.2 严格生产配置

将 ops/production.env.example 中的生产项合并进 .env，并配置真实端点：

```dotenv
PORTRAIT_RUNTIME_PROFILE=production
PRODUCTION_EXTERNAL_SERVICES_REQUIRED=true

AUTH_REQUIRED=true
RBAC_ENABLED=true
ENABLE_API_DOCS=false
DEBUG_ENDPOINTS_ENABLED=false

PORTRAIT_STORAGE_BACKEND=postgres
POSTGRES_DSN=postgresql://用户:密码@postgres.example:5432/portrait

PORTRAIT_VECTOR_BACKEND=pgvector
PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND=true
PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES=true

PORTRAIT_OBJECT_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.example
S3_REGION=us-east-1
S3_BUCKET=portrait-hub
S3_ACCESS_KEY_ID=替换为真实值
S3_SECRET_ACCESS_KEY=替换为真实值

TASK_QUEUE_BACKEND=redis
REDIS_URL=redis://:密码@redis.example:6379/0

READY_CHECK_DEPENDENCIES=true
OPENTELEMETRY_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.example:4318/v1/traces

REQUIRE_ENCRYPTION=true
CPU_FALLBACK_ENABLED=false
INSTALL_PROD_OPTIONAL=true
```

生产模式启动前必须应用 PostgreSQL/pgvector 架构：

```bash
export POSTGRES_DSN='替换为真实 PostgreSQL DSN'
psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 \
  -f tools/portrait_postgres_schema.sql
```

数据库账号需要创建 vector 扩展和表结构的权限。API、视频 worker、流 worker 必须使用相同的 PostgreSQL、S3、Redis 和加密密钥配置。

## 9. 准备模型

当前 models.yml 使用以下模型 ID：

- portrait_hub/yolov8n.onnx；
- portrait_hub/osnet_ibn_x1_0.onnx。

模型 ID 中包含项目命名空间，但当前 artifact.path 是模型目录根部文件名，因此宿主机实际结构为：

```text
/opt/portrait-hub/
├── models.yml
├── model-capabilities.yml
├── runtime-state/
│   └── models.yml
└── models/
    ├── yolov8n.onnx
    ├── yolov8n.model-card.yml
    ├── yolov8n.labels.txt
    ├── yolov8n.governance.yml
    ├── osnet_ibn_x1_0.onnx
    ├── osnet_ibn_x1_0.model-card.yml
    └── osnet_ibn_x1_0.governance.yml
```

仓库根目录的 models.yml 是只读模板；容器实际读写 runtime-state/models.yml。首次部署时初始化一次，后续更新不得覆盖运行时副本：

```bash
cd /opt/portrait-hub
mkdir -p models runtime-state
chmod 750 models runtime-state
if [ ! -f runtime-state/models.yml ]; then
  install -m 640 models.yml runtime-state/models.yml
fi
find models -maxdepth 1 -type f -print
```

严格校验应在装有 Python 3.12 开发环境的受控机器上运行：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements/dev.txt
python tools/validate_model_package.py \
  --config runtime-state/models.yml \
  --models-root models \
  --strict-hash \
  --strict-sidecars
```

不要在生产服务器上临时修改 artifact.sha256 来绕过校验。

## 10. 构建前检查

```bash
cd /opt/portrait-hub
test -f .env
test -f models.yml
test -f runtime-state/models.yml
test -d models
mkdir -p runtime-state

docker compose -p portrait-hub config --quiet
docker compose -p portrait-hub config --services
docker compose -p portrait-hub config --images
```

当前应看到四个服务和以下镜像名：

```text
portrait-hub-gpu-worker-0
portrait-hub-gpu-worker-1
portrait-hub-portrait-video-job-worker
portrait-hub-portrait-stream-worker
```

在装有 Python 3.12 开发环境的发布机上，还应运行：

```bash
python tools/deploy_check.py --json --import-app
python tools/portrait_production_readiness.py --scope platform --strict
```

严格生产门禁必须没有失败项。

## 11. 构建与启动

构建镜像：

```bash
cd /opt/portrait-hub
docker compose -p portrait-hub build --pull
```

镜像构建会访问 Docker Hub、Ubuntu 软件源、deadsnakes PPA、npm registry 和 Python 包镜像。受限网络环境应提前配置代理或内部镜像源。

首次只启动 API worker：

```bash
docker compose -p portrait-hub up -d gpu-worker-0 gpu-worker-1
```

查看状态和日志：

```bash
docker compose -p portrait-hub ps
docker compose -p portrait-hub logs --tail=200 gpu-worker-0
docker compose -p portrait-hub logs --tail=200 gpu-worker-1
```

两个 API worker 都应进入 healthy。

需要异步视频和视频流时再启动：

```bash
docker compose -p portrait-hub up -d \
  portrait-video-job-worker portrait-stream-worker
```

## 12. 上线验证

### 12.1 验证 GPU 隔离

先记录宿主机 UUID：

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.total --format=csv
```

再检查容器：

```bash
docker exec gpu-worker-0 \
  nvidia-smi --query-gpu=index,uuid,name,memory.total --format=csv
docker exec gpu-worker-1 \
  nvidia-smi --query-gpu=index,uuid,name,memory.total --format=csv
```

每个容器只能看到一张卡，容器中的 UUID 应分别对应宿主机 GPU 0 和 GPU 1。

如已启动异步视频 worker，还必须确认 NVIDIA runtime 已实际注入 GPU：

```bash
docker exec portrait-video-job-worker \
  nvidia-smi --query-gpu=index,uuid,name,memory.total --format=csv
```

默认配置下它只能看到宿主机 GPU 0。容器显示 Started 但该命令失败，说明视频任务尚不能执行 GPU 推理。

### 12.2 验证 ONNX Runtime

```bash
docker exec gpu-worker-0 python -c \
  "import onnxruntime as ort; print(ort.get_available_providers())"
docker exec gpu-worker-1 python -c \
  "import onnxruntime as ort; print(ort.get_available_providers())"
```

必须包含 CUDAExecutionProvider。TensorRT 默认关闭，不要求出现 TensorrtExecutionProvider。

视频 worker 启动后也应检查：

```bash
docker exec portrait-video-job-worker python -c \
  "import onnxruntime as ort; print(ort.get_available_providers())"
```

### 12.3 健康检查

```bash
curl --fail http://127.0.0.1:9001/health
curl --fail http://127.0.0.1:9001/ready
curl --fail http://127.0.0.1:9002/health
curl --fail http://127.0.0.1:9002/ready
```

深度检查需要鉴权：

```bash
export PORTRAIT_TOKEN='替换为 .env 中的 API_TOKEN'

curl --fail \
  -H "Authorization: Bearer $PORTRAIT_TOKEN" \
  -H "X-Tenant-ID: default" \
  "http://127.0.0.1:9001/ready/deep?load_models=true"
```

另一个 worker 也必须单独检查：

```bash
curl --fail \
  -H "Authorization: Bearer $PORTRAIT_TOKEN" \
  -H "X-Tenant-ID: default" \
  "http://127.0.0.1:9002/ready/deep?load_models=true"
```

如发布机已安装项目开发环境，可以执行：

```bash
python -m tools.service_smoke_test \
  --base-url http://127.0.0.1:9001 \
  --token "$PORTRAIT_TOKEN" \
  --auth-scheme bearer \
  --tenant-id default \
  --require-ready \
  --deep-ready \
  --load-models \
  --model-id person_detector_default
```

注意必须使用 python -m tools.service_smoke_test；直接执行脚本文件会破坏项目包导入路径。

## 13. 控制台与网关

根路径 / 是正式登录入口，登录后进入 /console。不要使用已经删除的旧控制台路径。

### 13.1 SSH 隧道验收

服务端口默认只监听回环地址。管理人员可以从本地电脑建立隧道：

```bash
ssh -L 9001:127.0.0.1:9001 user@服务器地址
```

然后访问：

```text
http://127.0.0.1:9001/
```

SSH 隧道可以避免端口暴露公网，但 Docker 容器仍可能把请求来源识别为网桥地址；使用本地账号时仍按前文设置 LOCAL_AUTH_ALLOW_REMOTE=true 和强凭据。

### 13.2 HTTPS 反向代理

正式环境使用 Nginx、HAProxy 或受控网关把请求负载均衡到：

```text
127.0.0.1:9001
127.0.0.1:9002
```

ops/nginx-gateway.example.conf 是 API 网关参考模板，生产使用前必须：

- 把 upstream 改成实际可达地址；
- 配置证书和正式域名；
- 代理根路径、/assets/console-next/、/v1/ 和 /ws/；
- 为 WebSocket 保留 Upgrade 和 Connection 请求头；
- 透传 Host、X-Request-ID、X-Tenant-ID、鉴权头和外部协议；
- 将请求体上限与 MAX_REQUEST_BODY_BYTES 对齐；
- 将正式域名加入 TRUSTED_HOSTS；
- 设置 LOCAL_AUTH_COOKIE_SECURE=true 和 OIDC_COOKIE_SECURE=true。

不要把 9001/9002 改为监听 0.0.0.0 后直接暴露公网。

## 14. 模型中心的 GPU 配置

模型中心会从当前请求命中的 worker 读取 GPU 清单。在默认单卡容器模式下：

- 两个 worker 的界面通常都显示逻辑 GPU 0；
- 建议模型保持“自动分配”；
- 不要通过共享的 runtime-state/models.yml 给单卡容器指定逻辑 device_id=1；
- 物理 GPU 路由通过 GPU_WORKER_0_DEVICE 和 GPU_WORKER_1_DEVICE 管理。

模型中心修改 GPU、别名或灰度配置时，会写回共享的 runtime-state/models.yml，但请求只会主动卸载当前命中的 worker 会话。修改后应同步两个 worker：

```bash
python -m tools.worker_control \
  --action reload-config \
  --token "$PORTRAIT_TOKEN" \
  --auth-scheme bearer \
  --tenant-id default
```

涉及已加载模型的 GPU 归属或模型文件更新时，最稳妥的是滚动重启两个 worker：

```bash
docker compose -p portrait-hub restart gpu-worker-0
curl --fail http://127.0.0.1:9001/ready

docker compose -p portrait-hub restart gpu-worker-1
curl --fail http://127.0.0.1:9002/ready
```

任何控制台配置写操作前都应备份 runtime-state/models.yml，并避免多个管理员同时写入。

## 15. 视频任务和视频流注意事项

### 15.1 GPU 竞争

portrait-video-job-worker 默认使用宿主机 GPU 0，会与 gpu-worker-0 竞争显存和计算资源。GPU 队列信号量只在单进程内生效，不会跨容器协调。

两张 11 GiB GPU 的建议：

- 只做同步图片推理：不启动视频和流 worker；
- API 优先：保留两个 API worker，降低视频批次并接受 GPU 0 竞争；
- 视频优先：只运行 gpu-worker-0，把 GPU 1 专用于视频 worker；
- 上线前分别压测 API、视频以及混合负载。

显存紧张时先降低：

```dotenv
GPU_QUEUE_LIMIT=1
GPU_QUEUE_LIMIT_PER_DEVICE=1
MODEL_CONCURRENCY_LIMIT=1
VIDEO_INFERENCE_BATCH_SIZE=4
STREAM_INFERENCE_BATCH_SIZE=4
MAX_LOADED_MODELS=2
```

### 15.2 实时流

portrait-stream-worker 默认使用 CPU 推理：

```dotenv
STREAM_WORKER_FORCE_CPU=true
STREAM_WORKER_GPU_DEVICES=none
```

需要让流 worker 使用 GPU 时，必须成对修改并重建容器：

```dotenv
STREAM_WORKER_FORCE_CPU=false
STREAM_WORKER_GPU_DEVICES=1
```

```bash
docker compose -p portrait-hub up -d --force-recreate portrait-stream-worker
docker exec portrait-stream-worker nvidia-smi -L
```

启用私网 RTSP/HTTP 地址前，还必须同时审查：

```dotenv
ALLOW_STREAM_URLS=true
ALLOW_PRIVATE_STREAM_HOSTS=true
STREAM_ALLOWED_HOSTS=camera.internal.example
```

生产环境应优先使用域名白名单，不要无条件允许任意私网地址。

## 16. 配置变更与日常运维

### 16.1 修改环境变量

docker compose restart 不会重新读取新的环境变量。修改 .env 后应重建容器：

```bash
docker compose -p portrait-hub up -d --force-recreate
```

仅修改代码或依赖时重新构建：

```bash
docker compose -p portrait-hub up -d --build
```

### 16.2 常用命令

```bash
docker compose -p portrait-hub ps
docker compose -p portrait-hub logs -f --tail=200
docker compose -p portrait-hub restart gpu-worker-0
docker compose -p portrait-hub stop
docker compose -p portrait-hub down
docker stats
watch -n 1 nvidia-smi
```

docker compose down 不会删除 bind mount 中的 runtime-state/models.yml、模型文件和其它运行状态，但执行前仍应备份。

### 16.3 从旧的可写 models.yml 迁移

旧部署如果仍设置 MODEL_CONFIG_HOST_FILE=./models.yml，控制台写回后会把 Git 工作区变脏，进而阻塞 git pull 或回退。首次升级到新方案时，在维护窗口禁止模型中心配置写入，并执行：

```bash
cd /opt/portrait-hub
mkdir -p runtime-state
cp -a models.yml runtime-state/models.yml
cmp --silent models.yml runtime-state/models.yml

if grep -q '^MODEL_CONFIG_HOST_FILE=' .env; then
  sed -i 's#^MODEL_CONFIG_HOST_FILE=.*#MODEL_CONFIG_HOST_FILE=./runtime-state/models.yml#' .env
else
  printf '\nMODEL_CONFIG_HOST_FILE=./runtime-state/models.yml\n' >>.env
fi

# 仅在上面的 cmp 成功且运行时副本已备份后，恢复仓库模板。
git restore --source=HEAD -- models.yml
git status --short
```

不要在后续代码更新中用仓库模板覆盖 runtime-state/models.yml。

### 16.4 更新代码

先按第 17 节创建完整备份和镜像归档，再更新：

```bash
cd /opt/portrait-hub
git fetch origin
git status --short
git pull --ff-only origin main

test -f runtime-state/models.yml
docker compose -p portrait-hub config --quiet
docker compose -p portrait-hub build --pull
docker compose -p portrait-hub up -d --remove-orphans
docker compose -p portrait-hub ps
```

如果 git status --short 非空，应先确认修改来源。正常情况下，控制台写入只会修改被 Git 忽略的 runtime-state/models.yml，不应再污染仓库。不要使用 git reset --hard 覆盖服务器配置。

## 17. 备份、升级与回退

升级前至少备份：

- 当前部署提交 SHA；
- .env 的加密备份；
- runtime-state/models.yml；
- runtime-state；
- PostgreSQL；
- S3 bucket；
- Redis 中尚未完成的任务；
- 当前可用镜像的离线归档。

本地文件备份示例：

```bash
cd /opt/portrait-hub
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="/opt/portrait-hub-backup/$stamp"
install -d -m 700 "$backup_dir"
cp -a .env runtime-state "$backup_dir/"
git rev-parse HEAD >"$backup_dir/git-sha.txt"
docker compose -p portrait-hub config --images >"$backup_dir/images.txt"
docker save -o "$backup_dir/images.tar" $(cat "$backup_dir/images.txt")
```

镜像归档让回退不依赖 Docker Hub、Ubuntu 软件源或 Python/npm 镜像。回退时加载旧镜像并切换到对应提交，不要重新构建：

```bash
cd /opt/portrait-hub
backup_dir=/opt/portrait-hub-backup/替换为备份时间戳
docker load -i "$backup_dir/images.tar"
git checkout "$(cat "$backup_dir/git-sha.txt")"
docker compose -p portrait-hub config --quiet
docker compose -p portrait-hub up -d --no-build --force-recreate --remove-orphans
```

仅当版本说明明确要求回退数据时，才恢复备份中的 runtime-state、数据库或对象存储；盲目恢复会覆盖升级后产生的新数据。回退验收完成后，后续更新前执行 git switch main 返回主分支。

不要只回退 frontend/console-next/dist/index.html；HTML、manifest 和哈希静态资源必须来自同一次构建。

历史破坏性升级和数据迁移要求见对应版本发布说明，不再放入当前新装主流程：

- docs/releases/0.14.0.md；
- docs/releases/0.14.1.md；
- docs/releases/0.14.2.md；
- docs/releases/0.14.3.md。

## 18. 离线部署

Compose 没有固定单一 portrait-hub:latest 镜像；当前会生成四个服务镜像。必须使用固定 Compose 项目名构建和导出。

联网构建机：

```bash
cd /opt/portrait-hub
docker compose -p portrait-hub build
docker compose -p portrait-hub config --images

docker save -o portrait-hub-0.14.3-images.tar \
  portrait-hub-gpu-worker-0 \
  portrait-hub-gpu-worker-1 \
  portrait-hub-portrait-video-job-worker \
  portrait-hub-portrait-stream-worker
```

同时传输：

- 项目源码与 Compose 文件；
- .env 的安全副本；
- 仓库模板 models.yml 和运行时配置 runtime-state/models.yml；
- 模型目录；
- 镜像归档。

离线服务器：

```bash
docker load -i portrait-hub-0.14.3-images.tar
cd /opt/portrait-hub
test -f runtime-state/models.yml
docker compose -p portrait-hub up -d --no-build gpu-worker-0 gpu-worker-1
```

构建机和部署机必须使用相同的 Compose 项目名 portrait-hub，否则镜像名称不会匹配。

## 19. CPU 备用部署

无 GPU 主机使用：

```bash
mkdir -p runtime-state
if [ ! -f runtime-state/models.yml ]; then
  install -m 640 models.yml runtime-state/models.yml
fi
docker compose -p portrait-hub-cpu \
  -f docker-compose.cpu.yml up -d --build
```

CPU 编排使用 Dockerfile.cpu、requirements-cpu.txt，并强制：

```dotenv
FORCE_CPU=true
NVIDIA_VISIBLE_DEVICES=none
```

GPU 与 CPU 编排不能同时占用宿主机 127.0.0.1:9001。

## 20. 故障排查

### 20.1 容器没有 CUDAExecutionProvider

```bash
nvidia-smi
docker run --rm --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
docker compose -p portrait-hub logs --tail=200 gpu-worker-0
```

检查驱动、NVIDIA Container Toolkit、nvidia-ctk runtime configure 和 Docker 重启状态。

### 20.2 worker 1 也显示 GPU 0

这是单卡容器的正常逻辑编号。比较 UUID，不要比较容器内索引。

### 20.3 模型不存在

```bash
find /opt/portrait-hub/models -maxdepth 1 -type f -print
docker exec gpu-worker-0 ls -la /models
```

当前模型文件直接位于 models/ 根目录，不是 models/portrait_hub/models/。

### 20.4 显存不足

- 保持 Uvicorn --workers 1；
- 降低推理和视频批次；
- 保持 GPU 与模型并发为 1；
- 减少预热和同时加载的模型；
- 设置 MAX_LOADED_MODELS；
- 避免 API 与视频 worker 同时占用同一张卡；
- 通过压测验证 FP16 或 TensorRT 后再启用。

### 20.5 修改 .env 后未生效

使用：

```bash
docker compose -p portrait-hub up -d --force-recreate
```

不要只执行 docker compose restart。

### 20.6 生产模式启动失败

查看启动日志中的 production external services are not fully configured，逐项补齐 PostgreSQL、向量后端、S3、Redis、OTLP、鉴权和生产模型能力配置。不要通过关闭 PRODUCTION_EXTERNAL_SERVICES_REQUIRED 绕过正式上线门禁。

### 20.7 远程登录入口不显示

登录页根据 `/v1/auth/config` 的 `local_enabled` 决定是否显示用户名和密码表单。Docker 或反向代理访问通常会被应用识别为网桥来源，必须同时满足：

```dotenv
LOCAL_AUTH_ENABLED=true
LOCAL_AUTH_ALLOW_REMOTE=true
LOCAL_AUTH_USERNAME=admin
LOCAL_AUTH_PASSWORD=替换为强随机密码
LOCAL_AUTH_SESSION_SECRET=替换为至少32字节随机值
```

默认密码 `123456` 或默认会话密钥不能用于远程登录。生成两个互不相同的安全值：

```bash
openssl rand -hex 16
openssl rand -hex 32
```

将第一个值写入 `LOCAL_AUTH_PASSWORD`，第二个值写入 `LOCAL_AUTH_SESSION_SECRET`。HTTPS 入口设置 `LOCAL_AUTH_COOKIE_SECURE=true`；直接 HTTP 或 SSH 隧道验收阶段暂时设置为 `false`。

修改 `.env` 后不能只执行 `restart`，必须重建两个 API worker：

```bash
docker compose -p portrait-hub up -d \
  --force-recreate gpu-worker-0 gpu-worker-1
```

分别验证两个 worker，响应都必须包含 `"local_enabled":true`：

```bash
curl --fail --silent http://127.0.0.1:9001/v1/auth/config
curl --fail --silent http://127.0.0.1:9002/v1/auth/config
```

仍为 `false` 时执行以下诊断。命令只输出布尔值和密钥长度，不输出真实凭据：

```bash
docker exec gpu-worker-0 python -c \
"from app import settings as s; print({
'version': s.APP_VERSION,
'enabled': s.LOCAL_AUTH_ENABLED,
'allow_remote': s.LOCAL_AUTH_ALLOW_REMOTE,
'username_configured': bool(s.LOCAL_AUTH_USERNAME),
'password_is_default': s.LOCAL_AUTH_PASSWORD == '123456',
'session_secret_length': len(s.LOCAL_AUTH_SESSION_SECRET),
'profile': s.PORTRAIT_RUNTIME_PROFILE
})"
```

同时确认 TRUSTED_HOSTS 包含正式域名，反向代理透传 Host、Cookie 和外部协议。修复后刷新登录页即可显示用户名和密码表单。

## 21. 上线检查清单

- [ ] 记录当前 Git SHA 和版本号。
- [ ] 宿主机能识别全部 GPU，驱动满足 CUDA 12.4。
- [ ] GPU 容器测试能看到正确 UUID。
- [ ] .env 权限为 600，所有默认密码和密钥已替换。
- [ ] API Token、会话密钥、OIDC 密钥和数据加密密钥互不相同。
- [ ] 使用本地账号时，两个 worker 的 /v1/auth/config 都返回 local_enabled=true。
- [ ] runtime-state/models.yml、模型文件、模型卡、标签和 SHA256 校验通过。
- [ ] docker compose config --quiet 通过。
- [ ] 两个 API worker 都是 healthy。
- [ ] 两个 worker 都提供 CUDAExecutionProvider。
- [ ] 两个 worker 的 /ready/deep?load_models=true 都通过。
- [ ] 视频 worker 启用时，容器内 nvidia-smi 和 CUDAExecutionProvider 检查通过。
- [ ] 流 worker 的 STREAM_WORKER_FORCE_CPU 与 STREAM_WORKER_GPU_DEVICES 配置一致。
- [ ] 视频/API 混合负载经过显存和延迟压测。
- [ ] 生产外部服务和 PostgreSQL schema 已配置。
- [ ] 严格生产 readiness 没有失败项。
- [ ] 正式入口使用 HTTPS，9001/9002 未直接暴露公网。
- [ ] 已验证登录、退出、模型中心、GPU 清单和灰度发布界面。
- [ ] 已完成数据库、对象存储、runtime-state/models.yml、运行状态和当前镜像归档备份。
- [ ] 已记录并演练上一版镜像或提交 SHA 的回退步骤。
