import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Socket } from 'node:net'

function resolveBackendOrigin(): URL {
  const explicitUrl =
    process.env.APEX_BACKEND_URL?.trim() ||
    process.env.BACKEND_URL?.trim() ||
    ''
  if (explicitUrl) {
    return new URL(explicitUrl)
  }

  const legacyHost = process.env.BACKEND_HOST?.trim()
  if (legacyHost) {
    return new URL(`http://${legacyHost}:8000`)
  }

  // Default to localhost forwarding instead of a brittle hardcoded WSL adapter IP.
  return new URL('http://127.0.0.1:8000')
}

const backendOrigin = resolveBackendOrigin()
const backendHttpTarget = backendOrigin.origin
const backendWsTarget = backendOrigin.origin.replace(/^http/i, 'ws')

function sendProxyUnavailable(res: ServerResponse<IncomingMessage>, path: string): void {
  if (res.headersSent) {
    return
  }

  res.writeHead(503, { 'Content-Type': 'application/json' })
  res.end(
    JSON.stringify({
      status: 'error',
      cache_state: 'error',
      message: `Backend unavailable for ${path}`,
      target: backendHttpTarget,
    }),
  )
}

function attachQuietApiErrorHandler(proxy: {
  removeAllListeners(event?: string): void
  on(
    event: 'error',
    listener: (err: Error, req: IncomingMessage, res: ServerResponse<IncomingMessage>) => void,
  ): void
}): void {
  proxy.removeAllListeners('error')
  proxy.on('error', (_err, req, res) => {
    sendProxyUnavailable(res, req.url || '/api')
  })
}

function attachQuietWsErrorHandler(proxy: {
  removeAllListeners(event?: string): void
  on(event: 'error', listener: (err: Error, req: IncomingMessage, socket: Socket) => void): void
}): void {
  proxy.removeAllListeners('error')
  proxy.on('error', (_err, _req, socket) => {
    try {
      socket.end()
    } catch {
      // best effort
    }
  })
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('react-router')) return 'router'
          if (id.includes('lucide-react')) return 'icons'
          if (id.includes('@tanstack/react-query') || id.includes('@tanstack/query-core')) return 'query'
          if (id.includes('motion') || id.includes('framer-motion')) return 'motion'
          if (id.includes('recharts') || id.includes('lightweight-charts') || id.includes('d3-') || id.includes('victory-vendor') || id.includes('internmap') || id.includes('fancy-canvas')) return 'charts'
          if (id.includes('react-virtuoso')) return 'virtual'
          if (id.includes('/react/') || id.includes('react-dom') || id.includes('scheduler')) return 'react-core'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    watch: {
      usePolling: true,
      interval: 300,
    },
    proxy: {
      '/api': {
        target: backendHttpTarget,
        changeOrigin: true,
        proxyTimeout: 30000,
        timeout: 30000,
        configure(proxy) {
          attachQuietApiErrorHandler(proxy as never)
        },
      },
      '/ws': {
        target: backendWsTarget,
        ws: true,
        changeOrigin: true,
        configure(proxy) {
          attachQuietWsErrorHandler(proxy as never)
        },
      },
    },
  },
})
