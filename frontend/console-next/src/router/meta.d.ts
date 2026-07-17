import "vue-router";
import type { ConsoleFeature } from "../api/contracts";

declare module "vue-router" {
  interface RouteMeta {
    title: string;
    public?: boolean;
    permission?: string;
    feature?: ConsoleFeature;
  }
}
