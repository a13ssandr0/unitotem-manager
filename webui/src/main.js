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
  },
})

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

const app = createApp(App)

app
  .use(vuetify)
  .use(router)

app.mount('#app')
