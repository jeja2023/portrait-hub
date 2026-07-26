# PortraitHub 支持矩阵

产品版本：`0.18.0`；上一稳定版本：`0.17.0`；机器源摘要：`0eb20d2be9ebe57252a9ea144e74bb0196da3942caa9e8e9f0c768da35b1e460`。

> `supported` 表示在列明边界内支持；`limited` 必须完成所列验收后才能进入合同 SLA；`experimental` 仅供试验；`unsupported` 禁止生产使用。

## 交付形态

| 形态 | 状态 | 商业 SLA | 拓扑 | 容量边界 | 阻断项 |
| --- | --- | --- | --- | --- | --- |
| `development` | `supported` | 否 | single process or development Compose | `not_applicable` | 无 |
| `private_standard` | `limited` | 否 | Docker Compose behind a TLS reverse proxy with external production data services | `capacity_report_required` | Five commercial model artifacts and model-quality evidence are incomplete.；Signed capacity and recovery reports are not available for a production target.；Clean install, offline, N-1 upgrade and rollback acceptance is incomplete. |
| `private_ha` | `limited` | 否 | Kubernetes with multiple API/worker replicas, GPU node pool and external HA data services | `ha_capacity_report_required` | All Private Standard blockers remain open.；Multi-node failure, autoscaling, canary and RTO/RPO acceptance is incomplete. |
| `platform_api` | `experimental` | 否 | Multi-zone Kubernetes or equivalent orchestration | `managed_service_qualification_required` | Multi-zone tenant-isolation, SLA, security, cost and operations acceptance is incomplete. |

## 操作系统

| 标识 | 系统 | 架构 | 状态 | 约束 |
| --- | --- | --- | --- | --- |
| `ubuntu-22.04-amd64` | Ubuntu 22.04 LTS | `x86_64` | `supported` |  |
| `ubuntu-24.04-amd64` | Ubuntu 24.04 LTS | `x86_64` | `limited` | Clean install, offline install, N-1 upgrade and rollback evidence is pending. |
| `windows-11-amd64` | Windows 11 with Docker Desktop | `x86_64` | `experimental` | Development and automated test use only. |
| `linux-arm64` | Linux ARM64 | `arm64` | `unsupported` | No release image or model-runtime qualification. |

## GPU 与运行时

GPU 镜像基线：`nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`；CPU 镜像基线：`python:3.12-slim-bookworm`。

| 标识 | 设备范围 | 状态 | 约束 |
| --- | --- | --- | --- |
| `nvidia-cuda-12.4` | T4, A10, L4, A100 class devices with at least 16 GiB VRAM | `limited` | Each exact GPU/model combination requires a passing capacity and quality report before SLA use. |
| `cpu-only-amd64` | AVX2-capable server CPU | `limited` | Development, demonstration or separately accepted low-performance workloads only; never a silent GPU fallback. |
| `non-nvidia-accelerator` | AMD GPU, Apple Silicon NPU/GPU and other accelerators | `unsupported` | No qualified runtime image. |

## 数据服务

| 组件 | 版本 | 状态 | 证据或约束 |
| --- | --- | --- | --- |
| `postgres-pgvector` | PostgreSQL 16 / pgvector image pgvector/pgvector:pg16 | `supported` | Scheduled real-container integration suite and commercial migration idempotency test. |
| `qdrant` | Qdrant 1.9.7 | `supported` | Scheduled real-container health integration suite. |
| `redis` | Redis 7.x | `supported` | Scheduled real-container queue integration suite. |
| `minio-s3` | S3 API / MinIO RELEASE.2025-04-22T22-12-26Z | `supported` | Scheduled real-container signed object round-trip integration suite. |
| `otel-collector` | OTLP/HTTP compatible OpenTelemetry Collector 0.100+ | `limited` | Deployment-specific collector and backend load qualification required. |
| `reverse-proxy` | HTTP/1.1 and WebSocket-capable reverse proxy with TLS 1.2+ | `limited` | NGINX, Envoy or equivalent configuration must pass deployment security checks. |

## 安装与升级

| 模式 | 状态 | 必需证据 |
| --- | --- | --- |
| `online` | `limited` | clean install and strict readiness |
| `offline` | `limited` | signed image/model bundle, checksums, SBOM, migrations, release notes and rollback materials |
| `proxy-network` | `limited` | proxy allowlist, certificate trust and image/model retrieval smoke |
| `air-gapped` | `limited` | offline import, signature verification, install, N-1 upgrade and rollback |

当前正式升级路径为 `0.17.0 -> 0.18.0`。回退必须遵守发布说明的数据边界；回滚窗口内只做 expand，不删除旧表或旧字段。

## 候选硬件

候选硬件只用于容量测试起点，不构成吞吐、延迟或可用性承诺。模型、数据规模、并发和流数量的最大值保持为空，直到目标环境的签名容量报告通过。
