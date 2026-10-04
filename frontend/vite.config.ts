import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时 npm run dev：页面由 Vite 提供，/api 和 /ws 请求转发给 Python 后端
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
