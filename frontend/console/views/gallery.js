(function registerConsoleGalleryView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    gallery: {
      navGroup: "gallery",
      views: ["gallery-enroll", "gallery-manage", "gallery-rebuild"],
      resultTargets: ["enroll-json", "gallery-json", "feature-rebuild-json"],
    },
  };
})(window);
