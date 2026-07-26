# OpenAPI 兼容性门禁

PortraitHub 将 `contracts/openapi-v1-baseline.json` 作为已经评审的 `/v1` 公共契约基线。CI 在每次变更中执行：

```powershell
python tools/openapi_compatibility_check.py --json
```

门禁允许新增路径、可选参数和可选响应字段；会拒绝删除路径或方法、修改 `operationId`、移除参数或响应、增加必填输入、收紧 schema 约束，以及修改安全要求。前端生成类型仍由 `tools/export_openapi.py` 校验，兼容性门禁不能替代生成代码差异检查。

## 评审流程

1. 优先通过新增字段、保留旧枚举值或新建版本化端点实现兼容演进。
2. 若确实需要破坏性变更，在需求卡和发布说明中记录影响范围、迁移窗口、回滚方式及审批人，并先完成调用方迁移。
3. 评审通过后才可更新基线：

```powershell
python tools/openapi_compatibility_check.py --write-baseline --json
python tools/openapi_compatibility_check.py --json
```

4. 将基线 JSON 与业务代码放在同一变更中评审。禁止仅为绕过 CI 而刷新基线。

检查其他契约文件时，可通过 `--baseline` 和 `--current` 指定 JSON 文件。命令退出码非零表示存在未评审破坏性变更或契约文件无效。
