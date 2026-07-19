import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { setSessionExpiry, setSessionTenant } from "../auth/session";
import { apiRequest } from "../api/client";
import type { ConsoleCapabilities } from "../api/contracts";

function permissionMatches(granted: string, required: string): boolean {
  return granted === "*" || granted === required || granted === required.split(":", 1)[0];
}

export const useCapabilitiesStore = defineStore("capabilities", () => {
  const capabilities = ref<ConsoleCapabilities | null>(null);
  const loading = ref(false);
  const loaded = computed(() => capabilities.value !== null);

  async function load(force = false): Promise<ConsoleCapabilities> {
    if (capabilities.value && !force) return capabilities.value;
    loading.value = true;
    try {
      capabilities.value = await apiRequest<ConsoleCapabilities>("/v1/console/me");
      setSessionTenant(capabilities.value.tenant_id);
      setSessionExpiry(capabilities.value.expires_at);
      return capabilities.value;
    } finally {
      loading.value = false;
    }
  }

  function clear(): void {
    capabilities.value = null;
  }

  function hasPermission(required?: string): boolean {
    if (!required) return true;
    return Boolean(capabilities.value?.permissions.some((granted) => permissionMatches(granted, required)));
  }

  return { capabilities, loading, loaded, load, clear, hasPermission };
});