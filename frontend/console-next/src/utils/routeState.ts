import { ref, watch, type Ref } from "vue";
import { useRoute, useRouter } from "vue-router";

export function useRouteTab(defaultValue: string): Ref<string> {
  const route = useRoute();
  const router = useRouter();
  const tab = ref(typeof route.query.tab === "string" ? route.query.tab : defaultValue);

  watch(tab, (value) => {
    if (route.query.tab === value) return;
    void router.replace({ query: { ...route.query, tab: value } });
  });

  watch(
    () => route.query.tab,
    (value) => {
      if (typeof value === "string" && value !== tab.value) tab.value = value;
      if (value === undefined && tab.value !== defaultValue) tab.value = defaultValue;
    },
  );

  return tab;
}