import { createPinia } from "pinia";
import { createApp } from "vue";

import "element-plus/dist/index.css";
import "./styles/base.css";

import App from "./App.vue";
import { clearSession } from "./auth/session";
import router from "./router";
import { useCapabilitiesStore } from "./stores/capabilities";

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);
app.use(router);

function redirectToLogin(): void {
  clearSession();
  useCapabilitiesStore().clear();
  if (window.location.pathname !== "/") {
    const redirect = encodeURIComponent(router.currentRoute.value.fullPath);
    window.location.replace(`/?redirect=${redirect}`);
  }
}

window.addEventListener("portrait:unauthorized", redirectToLogin);
window.addEventListener("portrait:session-expired", redirectToLogin);

app.mount("#app");