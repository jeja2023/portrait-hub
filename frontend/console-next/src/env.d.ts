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
      section: "工作台" | "开发者中心" | "系统管理";
      order: number;
      icon: FunctionalComponent;
      label?: string;
    };
  }
}
