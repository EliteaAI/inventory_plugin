import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default ({ mode }) => {
  // Load env file based on mode
  const env = loadEnv(mode, process.cwd(), '')

  const {
    VITE_DEV_SERVER = 'https://dev.elitea.ai',
    VITE_SERVER_URL = '/api/v2',
    VITE_SOCKET_PATH = '/socket.io/',
  } = env

  return defineConfig({
    plugins: [react()],
    base: './',
    build: {
      outDir: '../dist',
      emptyOutDir: true,
      sourcemap: true,
    },
    server: {
      port: 5175, // Different port from other UIs
      proxy: {
        // Proxy API requests to remote server
        [VITE_SERVER_URL]: {
          target: VITE_DEV_SERVER,
          changeOrigin: true,
          secure: false,
          ws: true,
        },
        // Proxy Socket.IO requests
        [VITE_SOCKET_PATH]: {
          target: VITE_DEV_SERVER,
          changeOrigin: true,
          secure: false,
          ws: true,
        },
      },
    },
  })
}
