# PortraitHub 平台验收

本文档定义当前平台加固阶段的受限验收范围。

## 范围

平台将按照工业级 API、安全、状态、存储适配器、SDK、配置、审计、回滚、保留和脱敏契约进行验收。

以下内容明确不纳入本次验收范围：

- 用生产级模型替换兜底或占位模型能力。
- 使用真实 PostgreSQL、向量数据库、S3 兼容对象存储和 Redis 部署执行端到端生产数据栈演练。
- 执行真实运维演练，例如生产压测、故障注入、备份恢复演练、镜像扫描门禁、漏洞门禁、GPU OOM 演练、告警验证和回滚彩排。

## 验收门禁

在将平台范围视为已验收之前，请执行以下门禁：

```powershell
python -m pytest -q
python tools\type_check.py
npm run check
python tools\deploy_check.py --import-app --json
python tools\portrait_production_readiness.py --scope platform --strict
git diff --check
```

预期结果：

- 单元测试和契约测试通过。
- 部署检查返回 `"ok": true`。
- 平台就绪检查返回 `"ok": true`，且 `strict_failure_count` 为 `0`。
- `git diff --check` 不报告空白字符错误。Windows 上的 CRLF 转换警告仅供参考。

## 完整切换门禁

最终的生产切换门禁仍然是：

```powershell
python tools\portrait_production_readiness.py --strict
```

在被排除的真实模型能力完成模型接入，且真实数据与运维验证已在本次受限平台验收之外执行之前，不得将该完整门禁视为完成。

## 停止规则

除非某项变更补齐了现有的安全、兼容性、发布契约或验证缺口，否则不要为了满足该验收范围而新增功能。
