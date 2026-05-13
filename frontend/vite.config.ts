import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5175,
    allowedHosts: ["home.metzger.pro"],
    proxy: {
      '/api': 'http://10.0.0.16:8000',
      '/ws': {
        target: 'ws://10.0.0.16:8000',
        ws: true,
      },
    },
  },
})
