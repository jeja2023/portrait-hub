<script setup lang="ts">
import { ElButton, ElOption, ElPagination, ElSelect } from "element-plus";

withDefaults(
  defineProps<{
    page: number;
    pageSize: number;
    total: number;
    pageSizes?: number[];
    hasMore?: boolean;
    loadingMore?: boolean;
  }>(),
  {
    pageSizes: () => [10, 20, 50],
    hasMore: false,
    loadingMore: false,
  },
);

const emit = defineEmits<{
  "update:page": [value: number];
  "update:pageSize": [value: number];
  "load-more": [];
}>();
</script>

<template>
  <div class="data-table-pagination" aria-label="数据分页">
    <div class="pagination-summary">
      <span>共 {{ total }} 条</span>
      <ElSelect
        :model-value="pageSize"
        class="pagination-size"
        size="small"
        aria-label="每页条数"
        @update:model-value="emit('update:pageSize', Number($event))"
      >
        <ElOption v-for="size in pageSizes" :key="size" :label="`${size} 条/页`" :value="size" />
      </ElSelect>
    </div>
    <div class="pagination-actions">
      <ElPagination
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        :pager-count="5"
        background
        size="small"
        layout="prev, pager, next"
        @update:current-page="emit('update:page', $event)"
      />
      <ElButton v-if="hasMore" text size="small" :loading="loadingMore" @click="emit('load-more')">
        加载下一批
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.data-table-pagination {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top: 0;
}
.pagination-summary,
.pagination-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pagination-summary > span {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.pagination-size {
  width: 104px;
}
.pagination-actions {
  min-width: 0;
  justify-content: flex-end;
}
.pagination-actions :deep(.el-pagination) {
  --el-pagination-button-width: 28px;
  --el-pagination-button-height: 28px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

@media (max-width: 700px) {
  .data-table-pagination,
  .pagination-actions {
    align-items: stretch;
    flex-direction: column;
  }
  .pagination-actions {
    gap: 6px;
  }
  .pagination-actions :deep(.el-pagination) {
    justify-content: flex-start;
  }
}
</style>
