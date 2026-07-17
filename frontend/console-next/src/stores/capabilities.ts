import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { apiRequest } from "../api/client";
import type { ConsoleCapabilities, ConsoleFeature } from "../api/contracts";

function permissionMatches(granted: string, required: string): boolean {
  return granted === "*" || granted === required || granted === required.split(":", 1)[0];
}

export const useCapabilitiesStore = defineStore("capabilities", () => {
  const capabilities = ref<ConsoleCapabilities | null>(null);
  const loading = ref(false);
  const loaded = computed(() => capabilities.value !== null);
  const previewMode = window.location.pathname.endsWith("/next");

  async function load(force = false): Promise<ConsoleCapabilities> {
    if (capabilities.value && !force) return capabilities.value;
    loading.value = true;
    try {
      capabilities.value = await apiRequest<ConsoleCapabilities>("/v1/console/me");
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

  function featureEnabled(feature?: ConsoleFeature): boolean {
    if (!feature || previewMode) return true;
    return capabilities.value?.features[feature] === true;
  }

  return { capabilities, loading, loaded, load, clear, hasPermission, featureEnabled };
});
