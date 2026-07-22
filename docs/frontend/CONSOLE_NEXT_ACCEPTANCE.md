# Console Next 验收报告

- 验收日期：2026-07-22
- 发布版本：0.16.0
- 验收对象：frontend/console-next 左侧导航、全站数据表序号、配置中心边框/分页/分类排序、生产静态入口与版本元数据
- 结论：0.16.0 导航与数据表体验优化已完成；Console Next 继续作为唯一生产入口。生产上线仍需执行组织审批、镜像回退演练，并验证共享 Redis、外部服务与生产 OIDC 目录。


> 2026-07-22 / 0.16.0 验收：侧栏隐藏垂直滚动条且桌面菜单间隔收紧；全项目 19 个原生表和 4 个 Element Plus 表统一显示序号；配置中心全部配置表格具备边框、分页和分类排序；Vitest 9 files / 35 tests、ESLint、Vue TypeScript 与 Vite production build 通过。
>
> 2026-07-20 / 0.14.0 验收：受管成员与租户治理、OIDC 成员绑定、模型灰度、数据导出、轨迹/视频/流高级参数、比对检索、统一分页和新 Logo 通过；Python 全量 567 passed / 4 skipped、Vitest 8 files / 32 tests、Playwright 五项目 15 passed、四种 SDK 和真实浏览器桌面/390px 验收通过。
>
> 2026-07-19 / 0.13.0 验收：工作台八个入口和 /analysis 删除通过；登录页默认 admin 用户名/密码并将 API Key/JWT 收进高级入口；本地账号与 OIDC 会话的 RBAC、租户隔离、CSRF、退出清理和显式凭证优先级通过；身份页显示认证来源和五级角色权限矩阵；本机 admin / 123456 登录与退出浏览器流程通过。
>
> 2026-07-19 / 0.12.1 补丁验收：环境模板与运行时配置漂移测试通过；Console HTML 热读取避免哈希资源 404；路由进入时滚动到顶部；标题栏说明文字、问号修复、重复标题隐藏和空租户标签处理通过；登录页与 14 个产品路由完成中文文案复核，普通业务枚举统一中文，必要技术专名保持原名。
> 2026-07-19 / 0.12.0 补充验收：SLO 使用日志聚合并标记保留完整性；WebSocket 降级启用详情轮询；会话按 expires_at 到期；页签、筛选和详情可深链恢复；解析档案支持按 ID 查询；图片推理开放高级参数；接入应用支持编辑、配额与用量；REDIS_URL 启用跨副本 ws-ticket 原子消费。

> 2026-07-18 / 0.11.2 补充验收：二次复核修复 WS query 主凭证回退、`/v1/*` no-store、默认 CSP、流详情深链、meta.nav 导航、aria-live、错误横幅 request_id、值级脱敏与搜索质量分；新增 me auth_kind×3、403、ws-ticket 401/stream/TTL、空库契约测试，并完成 typecheck、ruff、pytest、npm check、Playwright E2E 与 diff check。

> 2026-07-18 / 0.11.1 补充验收：deploy_check --import-app 已新增旧源码目录缺失断言，duplicate /v1 检查改为扫描 frontend/console-next/src；平台 strict readiness 同步阻断旧灰度变量、旧静态路径和旧 DOM 标记回归。

## 0.16.0 增量验收

| 范围 | 完成状态 | 关键检查 |
| --- | --- | --- |
| 左侧导航 | 完成 | 隐藏桌面垂直滚动条；1px 菜单间隔；底部选中态完整 |
| 全站数据表 | 完成 | 19 个原生表、4 个 Element Plus 表均有序号列；分页序号连续 |
| 配置中心 | 完成 | `ElTable border`、10/20/50 分页、分类稳定排序、移动/桌面共用页数据 |
| 回归门禁 | 完成 | `tableSequence.test.ts` 扫描表格和配置中心关键模板契约 |

## 功能闭环

| 范围 | 0.14.0 结果 |
|---|---|
| 生产入口 | /、/console、/console/next 统一提供 Console Next |
| 工作台导航 | 八个业务入口直接展示；无“智能分析”中间菜单；/analysis 返回页面不存在 |
| 人员登录 | 本地管理员、OIDC 企业 SSO 与高级 API Key/JWT 三类入口 |
| 身份与权限 | 成员授权、租户管理、角色权限、外部 subject 绑定、成员/租户启停形成闭环 |
| 模型发布 | 详情、预热、重载、配置重载、权重灰度、命中预览、回滚和发布审计可用 |
| 运维导出 | 租户数据导出支持资源上限；备份支持完整/增量起点 |
| 推理高级参数 | 人员轨迹、视频任务和实时流开放检测/ReID/阈值/采样/向量参数 |
| 比对与人员库 | 返回向量、批量异步、融合模态、阈值方案、注册元数据和定向特征重建可用 |
| 调试信息 | 仅完成名称优化，原可见性和脱敏行为保持不变 |
| legacy | frontend/console 已删除；/console/legacy 与 /assets/console* 返回 404 |
| 调试台 | 批量检索、批量比对、流注册/列表/事件、HTTP/错误码/request_id 诊断 |
| SDK 示例 | Python/Node 异步批量与视频任务示例，可逐项复制 |
| SLO | 调用日志聚合优先、保留完整性提示、Prometheus 回退、错误预算、推理/排队 p95/p99、设备队列 |
| 调用日志与错误码 | request_id、应用、状态、错误码、时间窗口筛选；稳定错误码处置建议 |
| 轨迹复核 | 后端合法标签、证据帧/引用、摘要、数据集与阈值建议 |
| 审计 | 事件多条件筛选、类别/结果摘要、链错误数与路径指纹 |
| 备份 | 快照 ID、后端、大小、增量起点、扫描/畸形记录摘要 |
| readiness | 原 11 个严格失败项全部通过，strict_failure_count=0 |
| 安全响应头 | WS 主凭证不进 query；`/v1/*` no-store；默认 CSP 无 unsafe-inline/jsdelivr |
| 流详情与导航 | `/analysis/stream/:streamId` 深链、流详情抽屉、KVEditor metadata；侧栏消费路由 meta.nav |
| 可访问性与错误 | aria-live 动态通告；错误横幅显示中文 error_code 与 request_id |
| 中文界面 | 登录页和 14 个产品路由的业务文案、状态与数据枚举统一中文；仅保留协议、语言、产品名、模型 ID、路径和错误码等技术专名 |
| 脱敏与搜索 | 值级兜底覆盖向量/内部地址/data URL/带凭证 URL；搜索候选展示质量分 |
| 连接与会话 | WS degraded 详情轮询；expires_at 到期清理与重登录；顶栏展示有效期 |
| 深链恢复 | 页签、结果/日志/人员筛选和解析详情 ID 与 URL 双向同步 |
| 图片分析与接入 | 人脸/人体/轨迹高级参数；应用 JWT、限流/配额；Webhook 编辑、启停、重试和超时 |
| 分页与品牌 | 业务列表统一分页与序号；新 Logo 已用于登录页、侧栏和 favicon |
| 多副本票据 | REDIS_URL 启用 Redis TTL 与 Lua 原子消费；本地无 Redis 时使用进程内实现 |

## 自动化结果

| 检查 | 结果 |
|---|---|
| 0.14.0 发布验证 | Pytest 567 passed / 4 skipped；Vitest 8 files / 32 tests；Playwright 五项目 15 passed；四种 SDK、GPU/CPU Compose、Vue TypeScript、ESLint、Vite production build、浏览器响应式/Logo 验收和 diff check 通过 |
| 0.13.0 发布验证 | Pytest 563 passed / 4 skipped；OIDC/本地登录专项 23 passed；Vitest 6 files / 19 tests；Vue TypeScript、ESLint、Vite production build、Compose config、diff check、本机浏览器登录验收和 /ready/deep version=0.13.0 通过 |
| 0.12.1 发布验证 | Python strict typecheck 177 sources、Ruff、Pytest 549 passed / 4 skipped、npm check、Vitest 6 files / 18 tests、Playwright 15 passed / 0 skipped、deploy_check、平台 strict readiness 与 diff check 均通过 |
| Python 全量回归 | 567 passed / 4 skipped；Python SDK 11 passed |
| Node SDK | 通过 |
| npm test | 通过，Node SDK + console:check 标准入口 |
| Go SDK | Go 1.22.12 执行 `go test ./...` 通过 |
| Java SDK | Maven 3.9.9 / JDK 17 执行 JUnit，5 passed |
| ESLint | 通过，0 warning |
| Python 类型门禁 | 当前环境未安装 mypy；--fallback-ok 注解门禁通过 |
| Vue TypeScript | 通过 |
| Vitest | 8 files，32 passed |
| Vite production build | 通过，随 npm run check 完成 |
| Playwright | 通过，15 passed / 0 skipped |
| 平台严格 readiness | 通过，strict_failure_count=0 |
| deploy_check | 通过，--json --import-app ok=true |

## 安全边界

- / 登录壳、/console、/console/next 与哈希静态资源可匿名读取；租户数据接口继续执行认证、权限和租户隔离。
- 本地账号/OIDC 使用 HttpOnly、SameSite Cookie 和 CSRF 双重校验；用户名、密码、会话签名不进入 localStorage、sessionStorage 或 URL。
- API Key/JWT 仅存 sessionStorage；localStorage 和 URL 不保存凭证，显式非法凭证不会回退到浏览器 Cookie。
- 默认 admin / 123456 只允许 loopback；生产或远程登录必须替换默认密码和会话密钥。
- /v1/console/me 对任何有效主体可读，只返回主体、租户、角色、权限、scope 和过期时间；它不要求 admin:status，也不再返回已删除的 feature flag。
- 静态资源路由拒绝目录穿越、点目录、index.html 和 source map；script-src 仅 self，无 unsafe-inline 与 unsafe-eval。
- WebSocket ticket 短期、单次、租户/资源/权限绑定，日志只记录指纹；多副本通过 REDIS_URL 使用 Redis TTL 与 Lua 原子消费。
- 调试响应、调用日志、审计事件和备份快照均使用公开脱敏字段；服务端路径、对象 key、bucket、摘要和凭证不返回。
- 图片扩展名与受支持内容不一致时只写脱敏告警；未知签名、实际解码格式冲突、损坏、过大和像素超限仍拒绝。视频容器不匹配继续拒绝。

## 交付与回退

- Docker 与 CPU Dockerfile 只复制 frontend/console-next/dist，不包含 Node 运行时或旧控制台。
- CONSOLE_DEFAULT_VERSION 与角色域灰度开关已删除；不存在运行时切换回 legacy 的路径。
- 回退必须部署上一版镜像或经过签名/校验的受控静态构件；回退不得删除业务数据、解析档案或审计记录。
- CI/部署前必须执行 `npm test`、`npm run console:e2e`、控制台/门禁定向或全量 `python -m pytest`、`python tools/deploy_check.py --json --import-app` 和 `python tools/portrait_production_readiness.py --scope platform --strict`。

## 生产待办

- 完成产品、安全、运维负责人上线签字和上一版镜像回退演练。
- 在真实生产流量下记录登录、前端异常、API 4xx/5xx、关键任务成功率和 p95/p99，并按发布规范完成观察。
- 多 API worker/多副本使用 REDIS_URL 启用共享 WS ticket 存储，并在发布环境验证 Redis 连通性和故障告警。
- 在可访问镜像仓库的构建环境重跑 Dockerfile 与 Dockerfile.cpu。
- 完成 Chrome/Edge 最近两个企业主版本的键盘和人工流程抽检。
