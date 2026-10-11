// Plugins
import Components from 'unplugin-vue-components/vite'
import Vue from '@vitejs/plugin-vue'
import Vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import Fonts from 'unplugin-fonts/vite'
import VueRouter from 'unplugin-vue-router/vite'

// Utilities
import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    VueRouter(),
    Vue({
      template: { transformAssetUrls },
    }),
    // https://github.com/vuetifyjs/vuetify-loader/tree/master/packages/vite-plugin#readme
    Vuetify({
      autoImport: true,
      styles: {
        configFile: 'src/settings.scss',
      },
    }),
    Components(),
    Fonts({
      fontsource: {
        families: [
          {
            name: 'Roboto',
            weights: [100, 300, 400, 500, 700, 900],
            styles: ['normal', 'italic'],
          },
        ],
      },
    }),
  ],
  optimizeDeps: {
    exclude: [
      'vuetify',
      'vue-router',
      'unplugin-vue-router/runtime',
      'unplugin-vue-router/data-loaders',
      'unplugin-vue-router/data-loaders/basic',
    ],
  },
  define: { 'process.env': {} },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('src', import.meta.url)),
    },
    extensions: [
      '.js',
      '.json',
      '.jsx',
      '.mjs',
      '.ts',
      '.tsx',
      '.vue',
    ],
  },
  build: {
    outDir: '../manager/static',
    assetsDir: 'assets',
    manifest: 'manifest.json',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: './src/main.js',
        pdf: './src/pdf-view.js',
        // altre entry point per pagine specifiche
      }
    }
  },
  server: {
    port: 3000,
    // Development: open http://localhost:3000 and everything that is not a Vite module is proxied to
    // a running backend (default: local; set UNITOTEM_BACKEND=https://<vm-ip> to target the test VM).
    // The marker header makes the backend render pages that load the sources from this server.
    // HTML pages and the websocket must go through the backend (the page state is rendered by Jinja).
    proxy: {
      '/': {
        target: process.env.UNITOTEM_BACKEND || 'https://localhost',
        secure: false,
        ws: true,
        changeOrigin: true,
        headers: { 'X-Unitotem-Vite-Dev': '1' },
        bypass: (req) => {
          const url = req.url || ''
          if (/^\/(src|node_modules|@|__vite|\.vite)/.test(url) || /[?&]import(&|$)/.test(url)) return url
        },
      },
    },
  },
})
