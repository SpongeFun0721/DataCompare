import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  
  // 修改点1：从 include 改为 exclude
  optimizeDeps: {
    exclude: ['pdfjs-dist'],  // 不预构建，保持 WASM 路径正确
  },
  
  // 添加这个配置
  build: {
    target: 'esnext',  // 支持现代 JS 特性
  },
  
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})