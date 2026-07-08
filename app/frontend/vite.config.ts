import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      // repo lives on /mnt/c (Windows drive) — inotify events don't propagate
      // through the 9p mount in WSL2, so without polling Vite serves stale
      // cached modules forever after a file edit
      usePolling: true,
      interval: 500,
    },
  },
})
