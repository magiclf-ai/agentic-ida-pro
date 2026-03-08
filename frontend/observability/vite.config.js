import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8765'
  const cwd = process.cwd()
  const isMountedFs = cwd.startsWith('/mnt/')
  const usePolling = env.VITE_USE_POLLING === '1' || (env.VITE_USE_POLLING !== '0' && isMountedFs)
  const pollingInterval = Number(env.VITE_POLL_INTERVAL || 300)

  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      watch: {
        usePolling,
        interval: pollingInterval,
      },
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
