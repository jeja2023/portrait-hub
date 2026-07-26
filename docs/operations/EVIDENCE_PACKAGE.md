# 商业交付证据包

`tools/portrait_evidence_package.py` 将目标环境的原始检查和制品组织成带 Ed25519 签名的不可变 ZIP。内部版包含原始文件；客户版只包含校验结论、源文件摘要和大小，不复制原始配置、日志、模型路径或内部扫描详情。

强制类别为：系统版本清单、CycloneDX SBOM、漏洞扫描、镜像签名/供应链、模型清单与治理、配置基线、COM-001~012 隐私合规、审计链、容量报告、恢复演练、SLA 报告和支持矩阵。任一类别缺失或不通过时，命令返回失败且不创建证据包。

私钥由发布系统或 HSM 管理，不能提交到仓库。验证时必须显式提供受信公钥；包内公钥只用于一致性比对，不能替代信任根。

```powershell
python tools/portrait_evidence_package.py build `
  --artifact system_inventory=artifacts/system-inventory.json `
  --artifact sbom=artifacts/sbom.cdx.json `
  --artifact vulnerability_scan=artifacts/vulnerability-scan.json `
  --artifact supply_chain=artifacts/supply-chain.json `
  --artifact model_inventory=artifacts/model-inventory.json `
  --artifact configuration_baseline=artifacts/configuration-baseline.json `
  --artifact privacy_compliance=artifacts/privacy-compliance.json `
  --artifact audit_chain=artifacts/audit-chain.json `
  --artifact capacity_report=artifacts/capacity-report.json `
  --artifact recovery_drill=artifacts/recovery-drill.json `
  --artifact sla_report=artifacts/sla-report.json `
  --artifact support_matrix=deploy/support-matrix.json `
  --output artifacts/portrait-evidence-internal.zip `
  --private-key C:/release-secrets/evidence-ed25519.pem `
  --audience internal --environment staging-a --profile private_standard `
  --tenant tenant-a --project project-a --git-commit 0123456789abcdef `
  --image-digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --actor release-manager --register --json

python tools/portrait_evidence_package.py verify artifacts/portrait-evidence-internal.zip `
  --public-key docs/security/evidence-ed25519.pub.pem `
  --environment staging-a --profile private_standard --json
```

`--register` 只在包成功生成后写入商业控制面的证据索引。生产流程应先生成内部版并归档，再以同一组源摘要生成客户版；客户版结论可通过 `source_sha256` 回查受控内部原始记录。
