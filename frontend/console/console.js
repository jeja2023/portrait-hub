(function bootstrapPortraitConsole(global) {
  const runtime = global.PortraitConsoleRuntime;
  if (!runtime || typeof runtime.init !== "function") {
    throw new Error("Portrait console runtime module did not load");
  }
  runtime.init({
    config: global.PortraitConsoleConfig || {},
    modules: global.PortraitConsoleModules || {},
  });
})(window);