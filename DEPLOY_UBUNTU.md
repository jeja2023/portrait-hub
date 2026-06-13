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

> 镜像基于 CUDA 12.4（`onnxruntime-gpu==1.20.1`，需 cuDNN 9）。宿主机 NVIDIA 驱动需满足 CUDA 12.4 的最低版本要求（Linux ≥ 550.54.14）。驱动过低会导致容器内 `CUDAExecutionProvider` 加载失败而静默回退 CPU。`nvidia-smi` 右上角的 `CUDA Version` 表示驱动支持的最高 CUDA 版本，需 ≥ 12.4。

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
RUNTIME_STATE_HOST_DIR=./runtime-state
STATE_READ_FAIL_CLOSED=true
ROLLOUT_AUDIT_PATH=/workspace/runtime-state/rollout-audit.jsonl
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
- `WARMUP_MODELS` 可写成 `portrait_hub/yolov8n.onnx,portrait_hub/osnet_ibn_x1_0.onnx`。
- `MODEL_CONCURRENCY_LIMIT` 和 `GPU_QUEUE_LIMIT` 建议先保持 `1`，压测后再提高。
- `ENABLE_TENSORRT=true` 只在确认容器内 ONNX Runtime 暴露 `TensorrtExecutionProvider` 后开启。
- 如果服务器只有 1 张 GPU，先删除或注释 `docker-compose.yml` 里的 `gpu-worker-1` 服务，或者只启动 `gpu-worker-0`。

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
```

推荐用内置冒烟测试做一次完整接口检查：

```bash
python3 tools/service_smoke_test.py \
  --base-url http://127.0.0.1:9001 \
  --token "$TOKEN" \
  --require-ready \
  --model-id person_detector_default
```

如果需要在上线前加载模型并跑虚拟推理：

```bash
python3 tools/service_smoke_test.py \
  --base-url http://127.0.0.1:9001 \
  --token "$TOKEN" \
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
  --token "$TOKEN"
```

新模型通过校验后，可以用别名切换接口发布。先 dry-run：

```bash
curl -X POST http://127.0.0.1:9001/rollout/aliases/switch \
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
curl -X POST http://127.0.0.1:9001/rollout/aliases/rollback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"alias_name":"person_detector_default","dry_run":false}'
```

如果需要按比例灰度，可以配置 weighted alias。下面示例表示 90% 旧模型、10% 新模型：

```bash
curl -X POST http://127.0.0.1:9001/rollout/aliases/weighted \
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
  "http://127.0.0.1:9001/rollout/aliases/preview?alias_name=person_detector_default&traffic_key=customer-001"
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
  "http://127.0.0.1:9001/model-info?project_name=portrait_hub&model_name=yolov8n.onnx"
```

查看模型配置：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9001/model-configs
```

深度 readiness 检查：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9001/ready/deep?load_models=true"
```

手动预热：

```bash
curl -X POST http://127.0.0.1:9001/warmup \
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
curl -X POST http://127.0.0.1:9001/infer/persons \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Request-ID: persons-test-001" \
  -F "project_name=portrait_hub" \
  -F "model_name=yolov8n.onnx" \
  -F "confidence=0.25" \
  -F "iou=0.45" \
  -F "files=@frame-001.jpg" \
  -F "files=@frame-002.jpg"
```

`/infer/persons` 直接返回每张图里的 `persons` 列表，包含人体框、置信度和类别信息。业务侧处理视频时，建议先按固定间隔抽帧，再把多张帧图作为 `files` 批量提交。

ReID 向量请求示例：

```bash
curl -X POST http://127.0.0.1:9001/infer/person-embeddings \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_name=portrait_hub" \
  -F "model_name=osnet_ibn_x1_0.onnx" \
  -F "include_vectors=true" \
  -F "files=@person-001.jpg"
```

检测 + ReID 组合请求示例：

```bash
curl -X POST http://127.0.0.1:9001/infer/person-tracks \
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
curl -X POST http://127.0.0.1:9001/infer/video/person-tracks \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@clip.mp4" \
  -F "frame_interval=15" \
  -F "max_frames=64" \
  -F "include_embeddings=false"
```

视频流解析请求示例：

```bash
curl -X POST http://127.0.0.1:9001/infer/stream/person-tracks \
  -H "Authorization: Bearer $TOKEN" \
  -F "stream_url=rtsp://user:password@camera-host/stream1" \
  -F "frame_interval=15" \
  -F "max_frames=32" \
  -F "read_timeout_seconds=10"
```

视频流解析默认关闭。需要在 `.env` 中设置 `ALLOW_STREAM_URLS=true` 并重建/重启容器后才会启用。生产环境建议只允许可信内网摄像头地址。

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
curl -X POST http://127.0.0.1:9001/reload \
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

### `/ready` 返回没有 `CUDAExecutionProvider`

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
- `/warmup` 手动预热。

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
- `/ready` 返回 `CUDAExecutionProvider`。
- `/model-info` 能返回正确输入 shape 和 dtype。
- `/warmup` 成功。
- 业务请求 tensor shape 与模型输入一致。
- 已设置 `API_TOKEN`。
- 已设置调用方超时、重试和日志 request id。
- 已完成至少一次真实模型压测。
