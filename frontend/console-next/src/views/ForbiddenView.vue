<script setup lang="ts">
import { useRouter } from "vue-router";
import { ArrowLeft, ShieldX } from "@lucide/vue";
import { ElButton } from "element-plus";

const router = useRouter();
</script>

<template>
  <div class="message-page">
    <ShieldX :size="42" />
    <h1>无权访问</h1>
    <p>当前凭证不具备此页面所需权限。</p>
    <ElButton :icon="ArrowLeft" @click="router.push('/')">返回总览</ElButton>
  </div>
</template>

<style scoped>
.message-page {
  min-height: 60vh;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: #62706d;
  text-align: center;
}
.message-page h1 {
  margin: 6px 0 0;
  color: #1c2826;
  font-size: 24px;
}
.message-page p {
  margin: 0 0 8px;
}
</style>
