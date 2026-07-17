<script setup lang="ts">
import type { Component } from "vue";

withDefaults(
  defineProps<{
    label: string;
    value: string;
    tone?: "neutral" | "success" | "warning" | "danger";
    icon: Component;
    detail?: string;
  }>(),
  { tone: "neutral", detail: "" },
);
</script>

<template>
  <article class="stat-card" :data-tone="tone">
    <div class="stat-card__icon" aria-hidden="true"><component :is="icon" :size="19" /></div>
    <div class="stat-card__body">
      <span class="stat-card__label">{{ label }}</span>
      <strong class="stat-card__value">{{ value }}</strong>
      <span v-if="detail" class="stat-card__detail">{{ detail }}</span>
    </div>
  </article>
</template>
