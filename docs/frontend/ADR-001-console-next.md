# ADR-001：控制台 next 技术与安全基线

- 状态：仓库实现基线已接受；生产灰度前仍需产品、安全、运维负责人签字
- 日期：2026-07-17
- 交付版本：0.11.1
- 决策范围：frontend/console-next、控制台静态服务、OpenAPI 类型生成、灰度与回退
- 关联方案：根目录《控制台前端重建方案.md》


> 2026-07-18 / 0.11.1 补充：部署门禁和 readiness 已改为扫描 Console Next 源码，并显式阻断旧控制台目录、旧灰度变量、旧静态路径和旧 DOM 标记回归。
## 背景

旧控制台由单页 HTML、全局状态和手写 DOM 组成，页面按后端接口而非业务任务组织。它同时存在 JSON 直出、深链缺失、权限能力由前端猜测、凭证持久化边界不清和 CSP 难以收紧等问题。0.10.0 完成并行迁移，0.11.0 在批次 E 删除旧代码与运行时切换面。

## 决策

### 工具链与依赖

- Node.js 锁定主版本 22；本地基线 22.14.0，engines 为大于等于 22.12 且小于 23。
- npm workspace 使用根目录唯一 package-lock.json，CI 与 Docker 一律 npm ci。
- Vue 3.5.40、Vue Router 4.6.4、Pinia 4.0.2、Element Plus 2.14.3、Lucide Vue 1.24.0。
- Vite 8.1.5、TypeScript 5.9.3、Vitest 4.1.10、Playwright 1.61.1、axe 4.12.1。
- 依赖使用精确版本；升级必须重新执行 CSP、浏览器矩阵、bundle 预算与视觉验收。

### 交付形态

- 源码位于 frontend/console-next；产物位于 frontend/console-next/dist。
- Python 服务只提供构建后的 index.html 和哈希资产，不在运行时转译源码。
- / 是统一登录入口，认证成功后进入 /console；/console/next 仅用于直达验收，/console/legacy 与 CONSOLE_DEFAULT_VERSION 已在批次 E 删除。
- 批次 E 后所有角色域统一由 Console Next 承载；导航、路由与按钮只根据服务端权限裁剪，不再存在 legacy feature flag。
- 新版路由使用 hash history，保证静态壳下的直接深链与浏览器后退行为。
- 生产分包采用 Vite 默认模块图；禁止把整个组件库强制合并为单一 manualChunks。任何人工分包都必须先通过五项目运行时矩阵，防止循环依赖副作用顺序变化。

### 契约与鉴权

- OpenAPI 是前端 API 类型的唯一来源；tools/export_openapi.py 生成 generated.ts，CI 检查无未提交差异。
- /v1/console/me 对任何有效认证主体返回主体、租户、角色、权限、scope 和过期时间；不要求 admin:status，也不返回已删除的 feature flag。
- 导航、按钮和路由守卫使用同一权限能力源；后端依旧独立执行 401/403 和租户隔离。
- API Key 与 JWT 只存 sessionStorage；租户优先由单租户凭证自动解析，不在登录页手工填写；localStorage 仅保存非敏感界面偏好。
- WebSocket 浏览器连接只使用短期、单次、租户与资源绑定 ticket。当前 ticket 存储为单进程有界内存，扩展到多 worker 或多副本前必须迁移到共享原子消费存储。

### 运维就绪契约

- 调试台必须覆盖批量检索、批量比对和视频流注册/查询，并展示端点模板、HTTP 状态、错误码与 request_id。
- SLO 面板优先使用 30 天租户调用日志，缺少 access:read 或窗口无数据时明确回退 Prometheus；成功率来源、错误预算、推理/排队分位数和设备队列必须进入脱敏原始数据。
- 审计页面消费 records 而非旧 events 字段；事件筛选、链校验、路径指纹、错误数和备份快照摘要均使用后端公开脱敏字段。
- 轨迹复核只提交后端支持的标签，并同时展示复核摘要、数据集和 auto_apply=false 的阈值建议。
- readiness 递归扫描 frontend/console-next/src，不得重新引用 frontend/console 或旧 DOM 标记。
### CSP 与静态资源

新版响应策略为：

- default-src self
- object-src none
- base-uri none
- frame-ancestors none
- form-action self
- img-src self data blob
- connect-src self
- font-src self data
- style-src self
- script-src self
- manifest-src self
- worker-src self blob

script-src 不允许 unsafe-inline 或 unsafe-eval。当前 Element Plus 版本在真实响应头下不需要 style-src-attr 例外，因此不登记放宽项。若以后依赖升级产生运行时内联样式需求，必须新增 ADR、安全评审和 Playwright 违规证据，不得顺带放宽脚本策略。

哈希资产使用一年 immutable 缓存；HTML 使用 no-cache、no-store。静态路由拒绝目录穿越、点目录、index.html 和 source map。

### 浏览器与体验预算

- 自动化矩阵：Chromium 桌面 1440×900、Chromium 平板 1024×768、Chromium 移动 390×844、Firefox 桌面、WebKit 桌面。
- 支持范围：Chrome/Edge 最近两个主版本、当前 Firefox ESR、当前 Safari 主版本。
- axe serious/critical 必须为 0；路由切换聚焦主内容；弹窗具名且圈定焦点；支持 prefers-reduced-motion。
- 首个业务路由 JS gzip 不超过 300 KB，CSS gzip 不超过 100 KB。开发者中心和系统管理保持路由级懒加载。

## 后果

正向结果：

- 生产只维护一套 Console Next 静态壳和权限契约，避免旧资源继续扩大安全与测试面。
- 权限、租户、灰度、类型和浏览器行为均有可自动验证的边界。
- 默认业务视图不直接显示 JSON；开发者模式中的原始数据先脱敏。
- 高风险写操作统一预演、影响摘要和文本二次确认。

代价与约束：

- 构建镜像增加 Node 22 阶段，CI 增加浏览器安装与 E2E 时间。
- Element Plus 及其依赖由 Vite 按模块图分包；首个总览路由全部 JS gzip 约 114 KiB，仍在预算内。
- legacy 已完成批次 E 删除；后续回退依赖上一版镜像或受控静态构件，而不是运行时 legacy 路由。
- 多副本部署前必须解决 WS ticket 共享消费，不能依赖负载均衡粘性会话掩盖问题。

## 验证基线

2026-07-17 本地验收结果：

- npm audit：0 个已知漏洞。
- ESLint：通过。
- vue-tsc：通过。
- Vitest：3 个文件、7 个测试通过。
- 控制台后端契约：9 个测试通过。
- Playwright：11 个执行通过，4 个按设计跳过；五个浏览器/视口项目均通过基础与深链用例，全路由与高风险弹窗巡检在 Chromium 桌面执行。
- CSP securitypolicyviolation：0。
- axe serious/critical：0。
- 生产构建首个总览路由：JS gzip 113.91 KiB；CSS gzip 48.91 KiB，低于 300/100 KB 预算。

2026-07-18 / 0.11.1 收口复验结果：

- `npm run console:typecheck`：通过。
- `npm run console:check` 与 `npm test`：通过，Vitest 4 files / 10 tests，Vite production build 通过。
- `npm run console:e2e`：通过，11 passed / 4 skipped。
- 控制台/门禁定向 Pytest：48 passed，2 warnings。
- `deploy_check --json --import-app`：ok=true。
- `portrait_production_readiness --scope platform --strict`：ok=true，strict_failure_count=0。

## 灰度与回退

批次 E 后生产默认入口为 console-next。跨租户、凭证泄露或 CSP 脚本策略回归时，回退到上一版镜像或受控静态构件，并按安全事件流程处理。

旧版删除已在 0.11.1 批次 E 完成。后续 ADR 完成条件转为回归保护：生产入口、Docker、CI、部署文档和 readiness 必须继续只指向 Console Next，并阻断 `/console/legacy`、旧灰度变量、旧静态路径和旧 DOM 标记回归。
