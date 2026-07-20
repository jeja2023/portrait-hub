import { computed, ref, toValue, watch, type MaybeRefOrGetter } from "vue";

export function useTablePagination<T>(source: MaybeRefOrGetter<readonly T[]>, initialPageSize = 10) {
  const currentPage = ref(1);
  const currentPageSize = ref(initialPageSize);
  const rows = computed<T[]>(() => [...toValue(source)]);
  const total = computed(() => rows.value.length);
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / currentPageSize.value)));

  function clampPage(value: number): number {
    return Math.min(Math.max(1, Math.trunc(value) || 1), pageCount.value);
  }

  const pageItems = computed(() => {
    const start = (currentPage.value - 1) * currentPageSize.value;
    return rows.value.slice(start, start + currentPageSize.value);
  });
  const startIndex = computed(() => (currentPage.value - 1) * currentPageSize.value);

  watch([total, currentPageSize], () => {
    currentPage.value = clampPage(currentPage.value);
  });

  return {
    get page(): number {
      return currentPage.value;
    },
    set page(value: number) {
      currentPage.value = clampPage(value);
    },
    get pageSize(): number {
      return currentPageSize.value;
    },
    set pageSize(value: number) {
      currentPageSize.value = Math.max(1, Math.trunc(value) || initialPageSize);
      currentPage.value = 1;
    },
    get total(): number {
      return total.value;
    },
    get items(): T[] {
      return pageItems.value;
    },
    get startIndex(): number {
      return startIndex.value;
    },
  };
}
