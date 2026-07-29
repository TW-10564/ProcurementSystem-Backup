import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Same-origin API access: the browser calls /api on whatever host it
    // loaded the page from, and Vite forwards to the backend locally. Avoids
    // (a) CORS entirely, (b) pinning a LAN IP into the bundle, and (c) the
    // corporate proxy hijacking calls to a bare LAN IP (proxy.twave.co.jp
    // is in http_proxy, and 172.16.x is not in the no_proxy bypass list).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8600',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
    watch: {
      // repo lives on /mnt/c (Windows drive) — inotify events don't propagate
      // through the 9p mount in WSL2, so without polling Vite serves stale
      // cached modules forever after a file edit
      usePolling: true,
      interval: 500,
    },
  },
})
