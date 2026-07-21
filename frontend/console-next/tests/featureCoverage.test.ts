import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "..");

function source(path: string): string {
  return readFileSync(resolve(root, path), "utf8");
}

describe("backend feature coverage", () => {
  it.each([
    [
      "src/views/LoginView.vue",
      ["authConfig.local_enabled", "本地账号登录不可用，请检查服务端 LOCAL_AUTH_* 配置"],
    ],
    [
      "src/views/admin/ModelsView.vue",
      [
        "/v1/models/",
        "/v1/admin/models/reload-config",
        "/v1/admin/models/gpu-devices",
        "/gpu-device",
        "/v1/admin/models/rollout/aliases/weighted",
        "/v1/admin/models/rollout/aliases/rollback",
        "/v1/admin/models/rollout/aliases/preview",
        "/v1/admin/models/rollout/audit",
        "当前稳定版本",
        "候选灰度版本",
      ],
    ],
    ["src/views/admin/OpsView.vue", ["/v1/admin/export", "updated_since", "/v1/admin/backup"]],
    [
      "src/views/dev/AccessView.vue",
      ['method: "PATCH"', "retry_limit", "timeout_seconds", "jwt_issuer", "jwt_audience"],
    ],
    [
      "src/views/analysis/ImageAnalysisView.vue",
      ["/v1/infer/tracks", "fallback_to_image", "include_embedding"],
    ],
    [
      "src/views/analysis/VideoJobsView.vue",
      ["detector_model_name", "reid_model_name", "max_detections", "include_embeddings"],
    ],
    [
      "src/views/analysis/StreamsView.vue",
      ["collectedSettings", "sample_interval_seconds", "read_timeout_seconds", "include_embeddings"],
    ],
    ["src/views/CompareView.vue", ["include_vectors", "async_mode", "fusionModalities"]],
    ["src/views/SearchView.vue", ["threshold_profile", "/v1/thresholds"]],
    ["src/views/GalleryView.vue", ["reindexModelId", "reindexModality", "enrollMetadata"]],
    [
      "src/views/admin/IdentityView.vue",
      ["rate_limit_per_minute", "rate_limit_burst", "scopes", "create_default_application"],
    ],
  ])("%s exposes its backend capability controls", (path, expected) => {
    const content = source(path as string);
    for (const token of expected as string[]) expect(content).toContain(token);
  });

  it("keeps generated contracts aligned with current identity and export routes", () => {
    const generated = source("src/api/generated.ts");
    for (const endpoint of [
      "/v1/auth/config",
      "/v1/access/tenants/{tenant_id}",
      "/v1/admin/identity",
      "/v1/admin/members",
      "/v1/admin/models/gpu-devices",
      "/v1/admin/models/{model_id}/gpu-device",
      "/v1/admin/export",
    ]) {
      expect(generated).toContain(endpoint);
    }
  });
});
