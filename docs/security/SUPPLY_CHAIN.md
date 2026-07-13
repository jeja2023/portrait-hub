# 供应链安全

PortraitHub 发布证据应覆盖源代码、依赖、容器镜像和模型构件。

必要控制项：

- 为运行时容器生成 CycloneDX SBOM。
- 使用 Trivy 扫描容器镜像。
- 针对锁定依赖清单运行 `pip-audit`。
- 为发布构件发布 SLSA provenance 或等效的签名来源证明。
- 使用 cosign 签名发布镜像。
- 在 CI 中保留 OSSF Scorecard 检查。
- 使用 Dependabot 或 Renovate 维护钉版依赖更新。

模型构件控制项：

- 每个生产模型都应钉定 `artifact.sha256`。
- 每个生产模型都应具备模型卡和治理 sidecar。
- 别名切换前必须校验哈希和模型卡。