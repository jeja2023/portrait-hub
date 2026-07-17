import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "/assets/console-next/",
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    manifest: true,
    sourcemap: false,

  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
  },
});
