(function bootstrapPortraitConsole(global) {
  const runtime = global.PortraitConsoleRuntime;
  if (!runtime || typeof runtime.init !== "function") {
    throw new Error("人像控制台运行模块未加载");
  }
  runtime.init({
    config: global.PortraitConsoleConfig || {},
    modules: global.PortraitConsoleModules || {},
  });
})(window);