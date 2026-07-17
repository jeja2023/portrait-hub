import { defineStore } from "pinia";
import { ref, watch } from "vue";

const PREFS_KEY = "portraitHubConsolePrefsV2";

function readPrefs(): { developerMode: boolean; sidebarCollapsed: boolean } {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREFS_KEY) ?? "null") as Record<
      string,
      unknown
    > | null;
    return {
      developerMode: parsed?.developerMode === true,
      sidebarCollapsed: parsed?.sidebarCollapsed === true,
    };
  } catch {
    return { developerMode: false, sidebarCollapsed: false };
  }
}

export const usePrefsStore = defineStore("prefs", () => {
  const initial = readPrefs();
  const developerMode = ref(initial.developerMode);
  const sidebarCollapsed = ref(initial.sidebarCollapsed);

  watch([developerMode, sidebarCollapsed], () => {
    window.localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ developerMode: developerMode.value, sidebarCollapsed: sidebarCollapsed.value }),
    );
  });

  return { developerMode, sidebarCollapsed };
});
