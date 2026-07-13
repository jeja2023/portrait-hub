# PortraitHub 演示客户端

这些示例是接入计划中提到的两个阶段二业务演示客户端。它们使用同一个服务端点和不同租户，便于运维人员在验收时直接验证应用 API Key、租户隔离、SDK 调用、调用日志、SLO 面板和网关路由，而不需要临时编写客户端代码。

## 环境变量

```bash
export PORTRAIT_HUB_BASE_URL="https://portrait.internal.example"
export PORTRAIT_HUB_TENANT_ID="tenant-a"
export PORTRAIT_HUB_API_TOKEN="phk_..."
export PORTRAIT_HUB_AUTH_SCHEME="api_key"
```

在同一服务上验证两个独立业务项目时，Python 演示使用 `tenant-a`，Node 演示使用 `tenant-b`。

## Python 演示

```bash
python examples/demo-clients/python_demo_client.py --dry-run
python examples/demo-clients/python_demo_client.py --image samples/person-a.jpg --image-b samples/person-b.jpg --video samples/clip.mp4
```

## Node 演示

```bash
node examples/demo-clients/node_demo_client.js --dry-run
node examples/demo-clients/node_demo_client.js --image samples/person-a.jpg --image-b samples/person-b.jpg --video samples/clip.mp4
```

演示客户端始终调用 `health`、`models` 和 `thresholds`。提供媒体路径时，还会通过 SDK 覆盖 `enroll`、`search`、`compare` 以及 `create_video_job` / `createVideoJob`。输出包含请求 ID 和响应数据键，但不会打印 API Key。