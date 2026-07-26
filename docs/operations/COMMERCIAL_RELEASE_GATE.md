# 商业发布总门禁

`tools/portrait_commercial_release_gate.py` 是生产候选版本的最终 fail-closed 入口。它同时检查：

- `docs/requirements/COMMERCIAL_REQUIREMENTS.json` 中 14 张需求卡均至少进入 `accepted`，状态证据路径有效且必需角色已有真实批准记录；
- 目标交付形态在机器支持矩阵中为 `supported`、已启用商业 SLA 且无阻断项；
- 证据包由受信 Ed25519 公钥验证，环境与交付形态一致，十二类强制证据完整；
- PostgreSQL 版本、迁移历史与商业控制面表完整；
- `deploy_check`、完整 strict production readiness、仓库治理、模型治理和依赖漏洞审计通过。

```powershell
$env:POSTGRES_DSN = "postgresql://..."
python tools/portrait_commercial_release_gate.py `
  --profile private_standard `
  --environment production-a `
  --evidence-package artifacts/portrait-evidence-internal.zip `
  --public-key C:/release-trust/evidence-ed25519.pub.pem `
  --json
```

输出 `decision: block` 时不得切换生产流量。工具没有跳过、降级或“忽略失败”参数；确需接受例外时，必须先由计划第 11.4 节对应责任角色和最终批准人完成有到期日的例外记录，再修正支持矩阵或对应原始证据并重新签名生成证据包。

基础工程测试、前端 E2E、镜像构建/扫描/签名应在 CI 的前置阶段完成，其原始结果进入证据包；总门禁仍会再次执行仓库和运行时可复验的检查，防止只上传历史证据。

开发和审计阶段可单独运行 `python tools/portrait_upgrade_traceability.py` 验证需求卡结构。该命令成功只表示追踪数据真实、完整，不表示可发布；生产决策必须使用 `--release`，且不得把 `verification` 或 `blocked` 当作批准。
