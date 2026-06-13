import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Dev server (npm run dev) — proxy /api to the backend
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      }
    }
  },
  // Production build — output to dist/
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
