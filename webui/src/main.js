/**
 * main.js
 *
 * Bootstraps Vuetify and other plugins then mounts the App`
 */


import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'
import {createVuetify} from 'vuetify'
import App from './App.vue'
import {createApp} from 'vue'
import 'unfonts.css'
import routes from "@/routes.js";
import {createRouter, createWebHistory} from "vue-router";


// https://vuetifyjs.com/en/introduction/why-vuetify/#feature-guides
const vuetify = createVuetify({
  theme: {
    defaultTheme: 'system',
    themes: {
      light: {
        colors: {
          primary: '#2962FF',
        },
      },
      dark: {
        colors: {
          primary: '#2962FF',
        },
      },
    },
  },
})

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

// A tab left open across an update keeps asking for page chunks whose names changed (they are content-hashed and the old
// ones are gone), and the click then fails with a 404 and nothing visible. When that happens the page is loaded again,
// once: a second failure within a few seconds is a real problem and must not turn into a reload loop.
const RELOAD_KEY = 'unitotem-chunk-reload'
function reloadForNewVersion(target) {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0)
    if (Date.now() - last < 10000) return false
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
  } catch { /* storage unavailable: reload anyway, once per call */ }
  window.location.assign(target || window.location.href)
  return true
}
// The chunk error can surface before the router reports it, so the page being navigated to is tracked here: that is
// where the reloaded page should land
let navigatingTo = null
router.beforeEach((to) => { navigatingTo = to.fullPath })
router.afterEach(() => { navigatingTo = null })
window.addEventListener('vite:preloadError', (event) => {
  if (reloadForNewVersion(navigatingTo)) event.preventDefault()
})
router.onError((error, to) => {
  if (/dynamically imported module|Importing a module script failed|Failed to fetch/i.test(String(error?.message))) {
    reloadForNewVersion(to?.fullPath)
  }
})

const app = createApp(App)

app
  .use(vuetify)
  .use(router)

app.provide('initialState', window.__INITIAL_STATE__ || {})

app.mount('#app')
