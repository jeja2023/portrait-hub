# PortraitHub 安全说明

## 加密密钥

生产环境中的 `ENCRYPTION_KEY` 应该使用随机生成的密钥。建议采用 32 字节随机值并用 URL 安全的 base64 编码，然后设置：

```bash
ENCRYPTION_KDF=raw-base64
ENCRYPTION_KEY=<32-byte-base64-secret>
```

如果 `ENCRYPTION_KEY` 是由运维人员管理的口令，请保留默认的 `ENCRYPTION_KDF=pbkdf2-sha256`。PortraitHub 会为每个 payload 保存随机盐值和迭代次数，用于 AES-GCM 加密载荷。历史 `sha256` 载荷在轮换期间仍可读取。

建议的生产控制：

- 在密钥轮换期间保持 `ENCRYPTION_KEYRING` 有内容，直到所有加密状态都完成重写。
- 在 CI 中使用 `python tools/security_audit.py` 对运行时依赖和生产可选依赖执行 `pip-audit`。
- 在归档审计日志之前运行 `python tools/portrait_audit_verify.py --json`。
