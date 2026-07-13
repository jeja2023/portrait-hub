(function registerConsoleOperationsView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    retrieval: {
      navGroup: "retrieval",
      views: ["compare", "multimodal-compare", "gallery-search"],
      resultTargets: ["compare-json", "multimodal-json", "search-json"],
    },
    access: {
      navGroup: "access",
      views: ["access-credentials", "sdk-examples", "api-playground", "openapi-docs", "error-codes", "webhooks", "call-logs"],
      resultTargets: ["access-credentials-json", "sdk-json", "playground-json", "openapi-json", "error-codes-json", "webhook-json", "call-logs-json"],
    },
    modelGovernance: {
      navGroup: "model-governance",
      views: ["models", "admin-threshold", "track-review", "evaluation-center", "release-center"],
      resultTargets: ["models-json", "admin-threshold-json", "track-review-json", "evaluation-json", "release-json"],
    },
    ops: {
      navGroup: "ops",
      views: ["slo-panel", "alerts", "admin-data", "audit-compliance"],
      resultTargets: ["slo-json", "alerts-json", "admin-data-json", "audit-json"],
    },
  };
})(window);
