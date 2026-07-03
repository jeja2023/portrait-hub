(function registerConsoleAnalysisView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    analysis: {
      navGroup: "analysis",
      views: ["vision", "video", "streams", "video-results"],
      resultTargets: ["vision-json", "jobs-json", "streams-json"],
    },
  };
})(window);
