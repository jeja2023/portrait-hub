(function registerConsoleOperationsView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    operations: {
      navGroup: "ops",
      views: ["models", "admin-threshold", "admin-data", "alerts"],
      resultTargets: ["models-json", "admin-threshold-json", "admin-data-json", "alerts-json"],
    },
  };
})(window);
