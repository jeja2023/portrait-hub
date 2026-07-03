(function registerConsoleDataRenderers(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  function payloadData(payload) {
    return payload && typeof payload === "object" && payload.data && typeof payload.data === "object" ? payload.data : payload;
  }

  function summaryItems(items) {
    return (items || []).filter((item) => item && item.value !== undefined && item.value !== null);
  }

  function normalizeTableRows(value) {
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item === "object" && !Array.isArray(item));
  }

  modules.renderers = { ...(modules.renderers || {}), normalizeTableRows, payloadData, summaryItems };
})(window);
