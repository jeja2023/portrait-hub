<script setup lang="ts">
import { ElButton, ElEmpty } from "element-plus";

withDefaults(
  defineProps<{
    title: string;
    description?: string;
    actionLabel?: string;
  }>(),
  { description: "", actionLabel: "" },
);

defineEmits<{ action: [] }>();
</script>

<template>
  <div class="empty-state">
    <ElEmpty :description="title" :image-size="72">
      <p v-if="description" class="empty-state__description">{{ description }}</p>
      <ElButton v-if="actionLabel" type="primary" @click="$emit('action')">{{ actionLabel }}</ElButton>
    </ElEmpty>
  </div>
</template>
