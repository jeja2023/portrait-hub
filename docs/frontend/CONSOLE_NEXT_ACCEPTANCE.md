# Console Next 验收报告

- 验收日期：2026-07-17
- 发布版本：0.10.0
- 验收对象：frontend/console-next 与相关 Python 静态服务、业务集合契约、WS ticket、CI/Docker/readiness
- 结论：代码级批次 0、A、B、C、D 已具备灰度条件；批次 E 的生产观察与 legacy 删除尚未开始

## 自动化结果

| 检查 | 结果 |
|---|---|
| Ruff（新增/修改 Python） | 通过 |
| 控制台后端契约 | 9 passed |
| ESLint | 通过，0 warning |
| Vue TypeScript | 通过 |
| Vitest | 3 files，8 passed |
| Vite production build | 通过 |
| Playwright | 11 passed，4 skipped（全路由巡检仅桌面 Chromium 执行） |
| 浏览器项目 | Chromium desktop/tablet/mobile、Firefox desktop、WebKit desktop |
| axe | serious/critical 0 |
| CSP 运行期事件 | 0 |
| npm audit | 0 vulnerabilities |
| deploy_check --skip-node | 通过 |
| 本地主 Docker 镜像构建 | 未完成：Docker Desktop 代理 127.0.0.1:10808 拒绝连接，基础镜像 manifest 无法拉取 |

## 性能预算

最新生产构建的公共入口资源：

| 资源 | gzip | 预算 |
|---|---:|---:|
| 入口、登录、路由与运行时依赖 | 约 130.17 KiB | 合并计入 JS 预算 |
| 总览路由增量 | 约 2.75 KiB | 合并计入 JS 预算 |
| 首个总览路由 JS 合计 | 132.92 KiB | 300 KB |
| 公共与总览 CSS 合计 | 50.22 KiB | 100 KB |
开发者中心和系统管理均为路由级懒加载，页面 chunk 未计入首个工作台路由。

## 安全边界

- / 登录壳、/console、/console/next 与哈希静态资源可匿名读取；/v1/console/me、任务和人员数据在生产鉴权配置下返回 401。
- API Key/JWT 仅存 sessionStorage；E2E 验证 localStorage 和 URL 不含密钥。
- 静态资源路由拒绝目录穿越、点目录、HTML 内部文件与 source map。
- script-src 仅 self，无 unsafe-inline 与 unsafe-eval；运行期无 securitypolicyviolation。
- 人员列表和详情不返回 embedding；敏感 metadata 递归脱敏。
- WS ticket 为短期、单次、租户/资源/权限绑定，日志只记录哈希指纹。
- 高风险模型发布、特征重建、数据清理均有预演/影响摘要/文本二次确认。

## 交付与回退

- Docker 与 CPU Dockerfile 都在 Node 22 builder 中 npm ci 并构建 next，只复制 dist 到运行镜像。
- CI 检查锁文件、OpenAPI 生成差异、lint、unit、type、build、pytest 和 E2E。
- / 为统一登录入口，登录后进入 /console；CONSOLE_DEFAULT_VERSION 默认 next，/console/legacy 与 /console/next 继续作为回退和验收入口。
- 三个角色域使用独立 feature flag、租户 allowlist 和稳定百分比分桶。
- 回退只需关闭对应 flag 或将 CONSOLE_DEFAULT_VERSION 设为 legacy，不删除业务数据或新构件。

## 未关闭条件

- 生产灰度五级观察尚无真实数据。
- 全量连续 14 天与两个发布周期尚未发生。
- 多 worker/多副本前，进程内 WS ticket 存储必须迁移到共享原子消费实现。
- 旧版删除所需的产品、安全、运维共同签字尚未取得。
- 键盘人工验收和 Chrome/Edge 最近两个实际主版本的企业环境抽检需在灰度环境执行。
- 在可访问 Docker Hub 的构建环境重跑主 Dockerfile 与 Dockerfile.cpu；本地失败仅由 Docker Desktop 失效代理导致。

因此，本报告不批准删除 legacy；它只确认当前实现可以进入内部租户灰度。
