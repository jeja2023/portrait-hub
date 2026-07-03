(function registerConsoleGalleryView(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  modules.views = {
    ...(modules.views || {}),
    gallery: {
      navGroup: "gallery",
      views: ["gallery-enroll", "gallery-search", "gallery-manage"],
      resultTargets: ["enroll-json", "search-json", "gallery-json"],
    },
  };
})(window);
