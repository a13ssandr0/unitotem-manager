const routes = [
  {
    path: '/',
    component: () => import('@/base.vue'),
    children: [
      {
        path: '',
        name: 'Home',
        component: () => import('@/pages/scheduler.vue'),
      },
      {
        path: '/settings',
        name: 'Settings',
        children: [
          {
            path: 'audio',
            name: 'Audio',
            component: () => import('@/pages/settings/audio.vue'),
          },
          {
            path: 'display',
            name: 'Display',
            component: () => import('@/pages/settings/display.vue'),
          },
          {
            path: 'remote',
            name: 'Remote control',
            component: () => import('@/pages/settings/remote.vue'),
          },
          {
            path: 'users',
            name: 'Users and permissions',
            component: () => import('@/pages/settings/users.vue'),
          },
          {
            path: 'timers',
            name: 'Timers',
            component: () => import('@/pages/settings/timers.vue'),
          },
          {
            path: 'network',
            name: 'Network',
            component: () => import('@/pages/settings/network.vue'),
          },
          {
            path: 'updates',
            name: 'Updates',
            component: () => import('@/pages/settings/updates.vue'),
          },
          {
            path: 'backup',
            name: 'Backup and restore',
            component: () => import('@/pages/settings/backup.vue'),
          },
        ]
      },
      {
        path: '/info',
        name: 'Info',
        component: () => import('@/pages/info.vue'),
      },
      // Catch-all route for 404 pages
      {
        path: '/:pathMatch(.*)*',
        name: 'NotFound',
        component: () => import('@/pages/NotFound.vue'),
      },
    ],
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/login.vue'),
  },
]

export default routes
