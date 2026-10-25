import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The dashboard only ever talks to its own origin: in development Vite forwards /api and
// /health to the engine, in production nginx does. That keeps the API key off cross-origin
// requests and means the engine needs no CORS configuration.
const engine = process.env.RECON_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { '/api': engine, '/health': engine },
  },
  build: { sourcemap: false, target: 'es2022' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
