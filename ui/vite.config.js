import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  esbuild: {
    drop: process.env.NODE_ENV === 'production' ? ['console', 'debugger'] : [],
  },
  build: {
    minify: 'esbuild',
    sourcemap: false,
  },
  server: {
    port: 3012,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8002', // 游戏后端地址（独立版）
        changeOrigin: true,
        // 后端路由本身即 /api/*，proxy 原样转发，无需 rewrite 剥前缀
        // （v2.3 清理时曾误写成 replace(/^\/api/,'')，会把 /api/start 错改成
        //  /start 发后端导致 404，已修正）
        bypass: (req) => {
           // 如果请求的是静态资源（如 MP3, 图片等），不走代理，由 Vite 自己服务
           if (req.url.match(/\.(mp3|png|jpg|jpeg|gif|svg|ico)$/i) || req.url.includes('/sounds/')) {
             return req.url;
           }
        }
      }
    }
  }
})