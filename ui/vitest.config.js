import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 前端单测配置（3.1 完整拆分的前置保护网）
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    css: false,
  },
})
