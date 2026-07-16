(function registerConsoleTemplateBuilder(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});
  const templates = (modules.templates = modules.templates || {});

  templates.build = (renderers) => [
    templates.buildCore(renderers),
    templates.buildAccess(),
    templates.buildGovernance(),
  ].join("");
})(window);
