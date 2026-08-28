import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'

/* dev 模式：renderer 相对路径 /api、/upload、/chat 转发到 Flask 后端（固定 HERB_DEV_PORT，默认 5175） */
const DEV_PROXY_TARGET = 'http://127.0.0.1:' + (process.env.HERB_DEV_PORT || '5175')

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    plugins: [vue()],
    server: {
      proxy: {
        '/api': { target: DEV_PROXY_TARGET, changeOrigin: true },
        '/upload': { target: DEV_PROXY_TARGET, changeOrigin: true },
        '/chat': { target: DEV_PROXY_TARGET, changeOrigin: true }
      }
    }
  }
})
