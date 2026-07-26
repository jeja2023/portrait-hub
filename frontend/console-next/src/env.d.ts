/// <reference types="vite/client" />

import type { FunctionalComponent } from "vue";

// 路由 meta 是导航的唯一数据源（方案 §6）：带 nav 的路由出现在侧栏，
// permission 同时驱动路由守卫与导航过滤，禁止在布局里维护第二份导航数据。
declare module "vue-router" {
  interface RouteMeta {
    title?: string;
    public?: boolean;
    permission?: string;
    nav?: {
      section:
        | "总览"
        | "智能分析"
        | "人员库"
        | "模型与评估"
        | "接入中心"
        | "运维合规"
        | "商业运营"
        | "平台管理";
      order: number;
      icon: FunctionalComponent;
      label?: string;
    };
  }
}
