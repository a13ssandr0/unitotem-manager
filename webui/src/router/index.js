/**
 * router/index.ts
 *
 * Automatic routes for `./src/pages/*.vue`
 */

// Composables
import { createRouter, createWebHistory } from 'vue-router'

let user_allowed_scheduler = true;

const routes = [
  {
    path: '/',
    component: () => import('@/layouts/default/base.vue'),
    children: [
      {
        path: '',
        name: 'Home',
        component: () =>
          user_allowed_scheduler?
            import('@/pages/scheduler.vue'):import('@/pages/empty_page.vue'),
      },
    ],
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/login.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

export default router
