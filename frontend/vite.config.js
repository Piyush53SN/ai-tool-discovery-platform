import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server: bind 0.0.0.0 so the sandbox preview can reach it, and proxy
// /api to the Django backend so the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
