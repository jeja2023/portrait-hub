# Changelog

## 0.18.5 - 2026-07-29

- 将 Python SDK 包和 User-Agent 版本同步到 `0.18.5`。
- 公开客户端方法、请求参数和响应兼容性不变；服务端视频预览与控制台内部接口修复不要求 SDK 调用方迁移。

## 0.18.4 - 2026-07-29

- 将 Python SDK 包和 User-Agent 版本同步到 `0.18.4`。
- 公开客户端方法、请求参数和响应兼容性不变；服务端媒体上传与配置说明修复不要求 SDK 调用方迁移。

## 0.18.3 - 2026-07-27

- 将 Python SDK 包和 User-Agent 版本同步到 `0.18.3`。
- 保持公开 `/v1` 客户端方法、请求参数和兼容性契约不变。

## 0.18.2 - 2026-07-27

- 将 Python SDK 包和 User-Agent 版本同步到 `0.18.2`。
- 公开客户端方法、请求参数和 `/v1` 契约保持不变。

## 0.18.1 - 2026-07-27

- 将 Python SDK 包和 User-Agent 版本同步到 `0.18.1`。
- 公开 SDK 接口与 `0.18.0` 保持兼容，本版本没有新增客户端方法或异常类型。

## 0.18.0 - 2026-07-26

- 增加商业客户、授权许可、不可变计量、成本归集、预算和配额预测接口支持。
- 增加可恢复视频上传、任务轮询、幂等写入、指数退避与抖动重试，以及稳定异常类型。
- 增加 Webhook 签名验证、API 兼容性预检和干净虚拟环境实时烟测流程。
- 保持 `sdk.python.portrait_hub_client` 兼容导入；新项目继续使用 `portrait_hub_sdk` 包。
- Python SDK 为稳定支持客户端；Node SDK 为维护支持；Go 与 Java 客户端为实验性参考实现。

## 0.17.0 - 2026-07-23

- Declared the Python package as the stable, officially supported SDK.
- Added safe-request retries with exponential backoff and jitter.
- Added idempotency-key support, resumable video uploads, job pagination and polling.
- Added typed core response models, stable exception subclasses, webhook verification, and API compatibility checks.

Existing `sdk.python.portrait_hub_client` imports remain compatible. New installations should import from `portrait_hub_sdk`.
