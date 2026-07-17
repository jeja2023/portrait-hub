<script setup lang="ts">
import { useRouter } from "vue-router";
import { ArrowLeft } from "@lucide/vue";
import { ElButton } from "element-plus";
const router = useRouter();
</script>
<template>
  <div class="not-found">
    <strong>404</strong>
    <h1>页面不存在</h1>
    <ElButton :icon="ArrowLeft" @click="router.push('/')">返回总览</ElButton>
  </div>
</template>
<style scoped>
.not-found {
  min-height: 60vh;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
}
.not-found strong {
  color: #087682;
  font-size: 42px;
}
.not-found h1 {
  margin: 0 0 8px;
  font-size: 22px;
}
</style>
