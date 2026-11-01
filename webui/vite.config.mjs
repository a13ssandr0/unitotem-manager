// Plugins
import Components from 'unplugin-vue-components/vite'
import Vue from '@vitejs/plugin-vue'
import Vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import Fonts from 'unplugin-fonts/vite'
import VueRouter from 'unplugin-vue-router/vite'

// Utilities
import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import { cpSync, rmSync } from 'node:fs'

// The data pdf.js needs to draw the fonts a PDF does not carry (the standard ones: Helvetica, Times, Courier...) and the character maps of
// Asian PDFs. Served from /assets/pdfjs/ so the page of a PDF looks the same whatever fonts the image has (pdf-view.js).
const pdfjsData = () => ({
  name: 'pdfjs-data',
  closeBundle() {
    const target = fileURLToPath(new URL('../manager/static/assets/pdfjs', import.meta.url))
    rmSync(target, { recursive: true, force: true })
    for (const dir of ['standard_fonts', 'cmaps']) {
      cpSync(fileURLToPath(new URL(`node_modules/pdfjs-dist/${dir}`, import.meta.url)), `${target}/${dir}`, { recursive: true })
    }
  },
})

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    pdfjsData(),
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
    // noVNC uses top-level await (WebCodecs feature detection), which needs a recent target; the browsers of the
    // administrators and CEF 147 all have it
    target: 'esnext',
    rollupOptions: {
      input: {
        main: './src/main.js',
        pdf: './src/pdf-view.js',
        keyboard: './src/keyboard.js',
        ticker: './src/ticker.js',
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
