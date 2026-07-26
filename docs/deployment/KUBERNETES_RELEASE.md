# Kubernetes 发布清单

`deploy/kubernetes/base` 是 Kustomize 基础模板，不是可直接发布的清单。其中的 `example` 仓库、示例域名以及全零/全一镜像摘要用于强制发布流程注入真实值；发布预检会拒绝这些占位值。

## 生成与预检

稳定和 canary 镜像都必须使用完整的 `repository@sha256:<64 hex>` 引用，且摘要必须不同。域名必须是真实 FQDN，canary 权重必须为 `1` 到 `99`，稳定权重由工具计算为 `100 - canary`。

```powershell
python -m tools.portrait_kubernetes_release render `
  --stable-image registry.corp.internal/portrait-hub@sha256:<stable-digest> `
  --canary-image registry.corp.internal/portrait-hub@sha256:<canary-digest> `
  --hostname portrait.corp.internal `
  --canary-weight 5 `
  --output runtime-state/portrait-hub-release.yaml
```

生成过程会读取 base 的 `kustomization.yaml`、物化 namespace 和公共标签、替换所有 Deployment/Job/CronJob 镜像，并更新 HTTPRoute 域名及 stable/canary 权重。只有通过占位符、不可变镜像、域名和权重检查后才写出文件。

在 `kubectl apply` 前再次执行只读预检：

```powershell
python -m tools.portrait_release_preflight `
  --kubernetes-manifest runtime-state/portrait-hub-release.yaml
kubectl apply --dry-run=server -f runtime-state/portrait-hub-release.yaml
kubectl apply -f runtime-state/portrait-hub-release.yaml
```

也可以只运行轻量清单检查：`python -m tools.portrait_kubernetes_release validate runtime-state/portrait-hub-release.yaml`。

不要对 `deploy/kubernetes/base` 直接运行 `kubectl apply -k`；验证该目录会按设计失败，防止模板被误当成发布产物。
