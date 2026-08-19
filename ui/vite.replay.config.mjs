import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 复盘验证专用：代理 /api → 本地 replay harness (:8016)，不动真实后端 (:8001)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3013,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8016', changeOrigin: true },
    },
  },
})
