(function registerConsoleOperationsView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    operations: {
      navGroup: "ops",
      views: ["access-credentials", "sdk-examples", "openapi-docs", "api-playground", "call-logs", "error-codes", "webhooks", "slo-panel", "multimodal-compare", "track-review", "evaluation-center", "release-center", "models", "admin-threshold", "admin-data", "audit-compliance", "alerts"],
      resultTargets: ["access-credentials-json", "sdk-json", "openapi-json", "playground-json", "call-logs-json", "error-codes-json", "webhook-json", "slo-json", "multimodal-json", "track-review-json", "evaluation-json", "release-json", "models-json", "admin-threshold-json", "admin-data-json", "audit-json", "alerts-json"],
    },
  };
})(window);
