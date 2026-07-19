# Console Next 验收报告

- 验收日期：2026-07-19
- 发布版本：0.12.0
- 验收对象：frontend/console-next、生产静态入口、权限与 WS ticket、SDK 示例、SLO、审计备份、复核池、CI/Docker/readiness
- 结论：批次 0、A、B、C、D、E 及 0.12.0 全面优化均已完成；旧控制台和运行时灰度配置已删除，Console Next 是唯一生产入口。生产上线仍需执行组织审批、镜像回退演练，并验证共享 Redis、外部服务和真实浏览器环境。


> 2026-07-19 / 0.12.0 补充验收：SLO 使用日志聚合并标记保留完整性；WebSocket 降级启用详情轮询；会话按 expires_at 到期；页签、筛选和详情可深链恢复；解析档案支持按 ID 查询；图片推理开放高级参数；接入应用支持编辑、配额与用量；REDIS_URL 启用跨副本 ws-ticket 原子消费。

> 2026-07-18 / 0.11.2 补充验收：二次复核修复 WS query 主凭证回退、`/v1/*` no-store、默认 CSP、流详情深链、meta.nav 导航、aria-live、错误横幅 request_id、值级脱敏与搜索质量分；新增 me auth_kind×3、403、ws-ticket 401/stream/TTL、空库契约测试，并完成 typecheck、ruff、pytest、npm check、Playwright E2E 与 diff check。

> 2026-07-18 / 0.11.1 补充验收：deploy_check --import-app 已新增旧源码目录缺失断言，duplicate /v1 检查改为扫描 frontend/console-next/src；平台 strict readiness 同步阻断旧灰度变量、旧静态路径和旧 DOM 标记回归。

## 功能闭环

| 范围 | 0.12.0 结果 |
|---|---|
| 生产入口 | /、/console、/console/next 统一提供 Console Next |
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
| 脱敏与搜索 | 值级兜底覆盖向量/内部地址/data URL/带凭证 URL；搜索候选展示质量分 |
| 连接与会话 | WS degraded 详情轮询；expires_at 到期清理与重登录；顶栏展示有效期 |
| 深链恢复 | 页签、结果/日志/人员筛选和解析详情 ID 与 URL 双向同步 |
| 图片分析与接入 | 人脸/人体高级参数；应用编辑、限流、配额、调用量和错误率 |
| 多副本票据 | REDIS_URL 启用 Redis TTL 与 Lua 原子消费；本地无 Redis 时使用进程内实现 |

## 自动化结果

| 检查 | 结果 |
|---|---|
| 0.12.0 发布验证 | Python strict typecheck 177 sources、Ruff、Pytest 549 passed / 4 skipped、npm check、Vitest 5 files / 13 tests、Playwright 15 passed / 0 skipped、deploy_check、平台 strict readiness 与 diff check 均通过 |
| Python 全量回归 | 549 passed / 4 skipped，11 warnings（既有 HTTP 状态常量弃用提醒） |
| Node SDK | 通过 |
| npm test | 通过，Node SDK + console:check 标准入口 |
| Go SDK | 通过 |
| Java SDK | 主源码 javac 编译通过；本机未安装 Maven，未执行 JUnit |
| ESLint | 通过，0 warning |
| Vue TypeScript | 通过 |
| Vitest | 5 files，13 passed |
| Vite production build | 通过，随 npm run check 完成 |
| Playwright | 通过，15 passed / 0 skipped |
| 平台严格 readiness | 通过，strict_failure_count=0 |
| deploy_check | 通过，--json --import-app ok=true |

## 安全边界

- / 登录壳、/console、/console/next 与哈希静态资源可匿名读取；租户数据接口继续执行认证、权限和租户隔离。
- API Key/JWT 仅存 sessionStorage；localStorage 和 URL 不保存凭证。
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