(function registerConsoleApi(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  function authHeaders(state) {
    const headers = { "X-Tenant-ID": state.tenantId || "default" };
    if (state.apiKey) headers["X-API-Key"] = state.apiKey;
    if (state.bearer) headers.Authorization = `Bearer ${state.bearer}`;
    return headers;
  }

  function createApiClient({ state, onStatus } = {}) {
    async function request(path, options = {}) {
      const headers = { ...authHeaders(state || {}), ...(options.headers || {}) };
      const init = { ...options, headers };
      if (options.json !== undefined) {
        headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(options.json);
        delete init.json;
      }
      const response = await fetch(path, init);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (onStatus) onStatus(payload.detail || response.statusText || "请求失败", true);
        throw new Error(payload.detail || response.statusText || "请求失败");
      }
      if (onStatus) onStatus("ready", false);
      return payload.data || payload;
    }
    return { authHeaders: () => authHeaders(state || {}), request };
  }

  modules.api = { authHeaders, createApiClient };
})(window);
