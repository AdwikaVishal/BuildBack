import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward /api/mcp/* → the Python MCP shim (see proxy-server.cjs)
      // The shim itself listens on 3001 and wraps the MCP Streamable-HTTP calls.
      '/api/mcp': {
        target: 'http://127.0.0.1:3001',
        changeOrigin: true,
      },
    },
  },
})
