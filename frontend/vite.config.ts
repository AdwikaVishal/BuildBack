import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
const ADAPTER_PORT = process.env.RIPPLEBACK_ADAPTER_PORT ?? '8001'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward /api/* → the Python local API adapter (rippleback-adapter).
      // The adapter owns the genuine MCP session (official Python SDK) and
      // exposes REST: /api/health, /api/mcp/tools, /api/mcp/tool,
      // /api/assistant/chat. The browser never speaks MCP directly.
      '/api': {
        target: `http://127.0.0.1:${ADAPTER_PORT}`,
        changeOrigin: true,
      },
    },
  },
})
