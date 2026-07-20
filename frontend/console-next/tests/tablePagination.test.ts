import { nextTick, ref } from "vue";
import { describe, expect, it } from "vitest";

import { useTablePagination } from "../src/utils/tablePagination";

describe("useTablePagination", () => {
  it("slices rows and keeps sequence offsets continuous", () => {
    const rows = ref(Array.from({ length: 24 }, (_, index) => index + 1));
    const pager = useTablePagination(rows, 10);

    expect(pager.total).toBe(24);
    expect(pager.items).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(pager.startIndex).toBe(0);

    pager.page = 2;
    expect(pager.items).toEqual([11, 12, 13, 14, 15, 16, 17, 18, 19, 20]);
    expect(pager.startIndex).toBe(10);
  });

  it("resets page size and clamps pages when data shrinks", async () => {
    const rows = ref(Array.from({ length: 24 }, (_, index) => index + 1));
    const pager = useTablePagination(rows, 10);

    pager.page = 3;
    pager.pageSize = 20;
    expect(pager.page).toBe(1);
    expect(pager.items).toHaveLength(20);

    pager.page = 2;
    rows.value = rows.value.slice(0, 5);
    await nextTick();

    expect(pager.page).toBe(1);
    expect(pager.total).toBe(5);
    expect(pager.items).toEqual([1, 2, 3, 4, 5]);
  });
});
