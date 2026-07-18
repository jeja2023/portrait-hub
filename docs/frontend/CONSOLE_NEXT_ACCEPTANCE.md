# Console Next 验收报告

- 验收日期：2026-07-17
- 发布版本：0.11.1
- 验收对象：frontend/console-next、生产静态入口、权限与 WS ticket、SDK 示例、SLO、审计备份、复核池、CI/Docker/readiness
- 结论：批次 0、A、B、C、D、E 的代码交付均已完成；旧控制台和运行时灰度配置已删除，Console Next 是唯一生产入口。生产上线仍需执行组织审批、镜像回退演练和多副本 WS ticket 前置条件。


> 2026-07-18 / 0.11.1 补充验收：deploy_check --import-app 已新增旧源码目录缺失断言，duplicate /v1 检查改为扫描 frontend/console-next/src；平台 strict readiness 同步阻断旧灰度变量、旧静态路径和旧 DOM 标记回归。
## 功能闭环

| 范围 | 0.11.1 结果 |
|---|---|
| 生产入口 | /、/console、/console/next 统一提供 Console Next |
| legacy | frontend/console 已删除；/console/legacy 与 /assets/console* 返回 404 |
| 调试台 | 批量检索、批量比对、流注册/列表/事件、HTTP/错误码/request_id 诊断 |
| SDK 示例 | Python/Node 异步批量与视频任务示例，可逐项复制 |
| SLO | 30 天调用日志优先、Prometheus 回退、错误预算、推理/排队 p95/p99、设备队列 |
| 调用日志与错误码 | request_id、应用、状态、错误码、时间窗口筛选；稳定错误码处置建议 |
| 轨迹复核 | 后端合法标签、证据帧/引用、摘要、数据集与阈值建议 |
| 审计 | 事件多条件筛选、类别/结果摘要、链错误数与路径指纹 |
| 备份 | 快照 ID、后端、大小、增量起点、扫描/畸形记录摘要 |
| readiness | 原 11 个严格失败项全部通过，strict_failure_count=0 |

## 自动化结果

| 检查 | 结果 |
|---|---|
| Python 定向回归 | 48 passed，2 warnings（控制台入口、旧资产 404、deploy_check、readiness、Console Next contracts） |
| Node SDK | 通过 |
| npm test | 通过，Node SDK + console:check 标准入口 |
| Go SDK | 通过 |
| Java SDK | 主源码 javac 编译通过；本机未安装 Maven，未执行 JUnit |
| ESLint | 通过，0 warning |
| Vue TypeScript | 通过 |
| Vitest | 4 files，10 passed |
| Vite production build | 通过，随 console:check 与 npm test 二次构建 |
| Playwright | 通过，11 passed / 4 skipped |
| 平台严格 readiness | 通过，strict_failure_count=0 |
| deploy_check | 通过，--json --import-app ok=true |

## 安全边界

- / 登录壳、/console、/console/next 与哈希静态资源可匿名读取；租户数据接口继续执行认证、权限和租户隔离。
- API Key/JWT 仅存 sessionStorage；localStorage 和 URL 不保存凭证。
- /v1/console/me 对任何有效主体可读，只返回主体、租户、角色、权限、scope 和过期时间；它不要求 admin:status，也不再返回已删除的 feature flag。
- 静态资源路由拒绝目录穿越、点目录、index.html 和 source map；script-src 仅 self，无 unsafe-inline 与 unsafe-eval。
- WebSocket ticket 短期、单次、租户/资源/权限绑定，日志只记录指纹。
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
- 多 API worker/多副本前，将进程内 WS ticket 存储迁移到共享、带 TTL、原子单次消费的实现。
- 在可访问镜像仓库的构建环境重跑 Dockerfile 与 Dockerfile.cpu。
- 完成 Chrome/Edge 最近两个企业主版本的键盘和人工流程抽检。