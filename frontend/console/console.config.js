(function () {
  const endpointMap = {
    vision: {
      faces: "/v1/infer/faces",
      persons: "/v1/infer/persons",
      pose: "/v1/infer/pose",
      appearance: "/v1/infer/appearance",
      gait: "/v1/infer/gait",
      detect: "/infer/persons",
      embeddings: "/infer/person-embeddings",
      tracks: "/infer/person-tracks",
    },
    compare: {
      faces: "/v1/compare/faces",
      persons: "/v1/compare/persons",
      gait: "/v1/compare/gait",
      fusion: "/v1/fusion/compare",
      batch: "/v1/compare/batch",
    },
  };

  const alertDefaults = {
    maxErrorRate: 0.05,
    maxP95Latency: 1.5,
    minFreeGpuMemoryGb: 1,
  };

  window.PortraitConsoleConfig = Object.freeze({ endpointMap, alertDefaults });
})();