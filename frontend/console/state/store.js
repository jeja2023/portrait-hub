(function registerConsoleState(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  function defaultAlertConfig(config) {
    return {
      maxErrorRate: Number(config?.alertDefaults?.maxErrorRate ?? 0.05),
      maxP95Latency: Number(config?.alertDefaults?.maxP95Latency ?? 1.5),
      minFreeGpuMemoryGb: Number(config?.alertDefaults?.minFreeGpuMemoryGb ?? 1),
    };
  }

  function loadAlertConfig(storage, config) {
    const defaults = defaultAlertConfig(config || {});
    try {
      const payload = JSON.parse(storage.getItem("portraitHubAlertConfig") || "{}");
      return {
        maxErrorRate: Number(payload.maxErrorRate ?? defaults.maxErrorRate),
        maxP95Latency: Number(payload.maxP95Latency ?? defaults.maxP95Latency),
        minFreeGpuMemoryGb: Number(payload.minFreeGpuMemoryGb ?? defaults.minFreeGpuMemoryGb),
      };
    } catch {
      return defaults;
    }
  }

  function createInitialState(storage, config) {
    return {
      tenantId: storage.getItem("portraitHubTenant") || "default",
      apiKey: storage.getItem("portraitHubApiKey") || "",
      bearer: storage.getItem("portraitHubBearer") || "",
      view: storage.getItem("portraitHubView") || "overview",
      analysisResultsTab: storage.getItem("portraitHubAnalysisResultsTab") || "image",
      isLoggedIn: storage.getItem("portraitHubLoggedIn") === "true",
      dashboard: {},
      galleryExport: {},
      latestPayloads: {},
      analysisResults: { image: [], video: null, stream: null },
      alertConfig: loadAlertConfig(storage, config),
      sockets: {},
      visionPreviews: [],
      visionPreviewSignature: "",
      visionResultVisuals: [],
      visionLightboxIndex: null,
      comparePreviews: { A: [], B: [] },
    };
  }

  modules.state = { createInitialState, defaultAlertConfig, loadAlertConfig };
})(window);
