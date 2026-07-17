<script setup lang="ts">
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { ElConfigProvider } from "element-plus";

import { hasSession } from "./auth/session";
import LoginView from "./views/LoginView.vue";

const isRootLogin = window.location.pathname === "/";
if (isRootLogin && hasSession.value) window.location.replace("/console#/");
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <LoginView v-if="isRootLogin" />
    <RouterView v-else />
  </ElConfigProvider>
</template>
