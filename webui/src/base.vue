<template>
  <v-app>
    <v-app-bar app clipped-left :class="connected ? 'app-bar-border' : 'app-bar-border-disconnected'">
      <v-app-bar-nav-icon @click.stop="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title class="app-bar-title">UniTotem @ {{ hostname }}</v-toolbar-title>
      <v-spacer></v-spacer>
      <v-btn v-if="logged_user.perms?.includes('admin')" icon title="Terminal" @click="terminalOpen = true">
        <v-icon>mdi-console</v-icon>
      </v-btn>
      <v-btn v-if="updateInfo?.available && allowed('Settings/Update/status')" icon to="/settings/updates"
             :title="`Update available: ${updateInfo.version ?? ''}`">
        <v-badge dot color="red"><v-icon>mdi-update</v-icon></v-badge>
      </v-btn>
      <v-btn icon @click="cycleTheme">
        <v-icon>{{ themeIcon }}</v-icon>
      </v-btn>
      <v-btn icon @click="rebootDialog = true">
        <v-icon>mdi-restart</v-icon>
      </v-btn>
      <v-btn icon @click="powerOffDialog = true">
        <v-icon>mdi-power</v-icon>
      </v-btn>

      <v-menu offset-y>
        <template v-slot:activator="{ props }">
          <v-btn v-bind="props" class="text-none app-bar-button">
            {{ logged_user.name }}
            <v-icon end>mdi-menu-down</v-icon>
          </v-btn>
        </template>
        <v-list>
          <v-list-item>
            <v-btn prepend-icon="mdi-logout" variant="text" :href="'/logout'">
              Logout
            </v-btn>
          </v-list-item>
        </v-list>
      </v-menu>
    </v-app-bar>

    <v-navigation-drawer v-model="drawer" app clipped>
      <v-list density="compact" color="primary">
        <v-list-item v-if="allowed('Scheduler/playlists')" to="/" exact prepend-icon="mdi-view-dashboard" title="Scheduler"></v-list-item>
        <v-list-item v-if="allowed('Viewers/list')" to="/viewers" prepend-icon="mdi-monitor-multiple" title="Viewers"></v-list-item>

        <v-divider class="my-2"></v-divider>
        <v-list-subheader v-if="visibleTabs.length">Settings</v-list-subheader>
        <v-list-item
          v-for="(tab, i) in visibleTabs"
          :key="i"
          :to="'/settings/' + tab.id"
          :prepend-icon="tab.icon"
          :title="tab.name"
          class="sub-item"
        >
          <template v-slot:append v-if="tab.id === 'updates' && updateInfo?.available">
            <v-badge dot color="red" inline></v-badge>
          </template>
        </v-list-item>
        <v-divider class="my-2"></v-divider>

        <v-list-item to="/info" prepend-icon="mdi-information" title="Info"></v-list-item>
      </v-list>

      <template v-slot:append>
        <div class="pa-2 text-caption">
          <v-divider class="mb-2"></v-divider>
          <div class="mt-2" v-if="disp_size">Display: {{ disp_size.width }}x{{ disp_size.height }}</div>
          <div class="mt-2" v-else>Display: disconnected</div>
          <div>
            <a href="https://github.com/a13ssandr0/unitotem" target="_blank" rel="noopener noreferrer"
               class="text-high-emphasis">
              <v-icon size="small">mdi-github</v-icon>
              <span>Unitotem</span>
            </a>
            {{ unitotem_version }} by a13ssandr0
          </div>
        </div>
      </template>
    </v-navigation-drawer>

    <v-main>
      <div class="main-content px-2 pt-2">
        <router-view v-if="routeAllowed"/>
      </div>
    </v-main>

    <TerminalWindow v-if="logged_user.perms?.includes('admin')" v-model="terminalOpen" />

    <v-dialog v-model="rebootDialog" max-width="fit-content">
      <v-card class="pa-2 pb-0 rounded-lg">
        <v-card-title class="text-h5">Are you sure you want to reboot?</v-card-title>
        <v-card-text>This will reboot the system.</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="rebootDialog = false">Cancel</v-btn>
          <v-btn color="red darken-1" text @click="rebootDialog = false; api.Power.reboot()">Reboot</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="powerOffDialog" max-width="fit-content">
      <v-card class="pa-2 pb-0 rounded-lg">
        <v-card-title class="text-h5">Are you sure you want to power off?</v-card-title>
        <v-card-text>This will power off the system.</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="powerOffDialog = false">Cancel</v-btn>
          <v-btn color="red darken-1" text @click="powerOffDialog = false; api.Power.poweroff()">Power Off</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="errorSnack" color="error" :timeout="8000" multi-line>
      {{ errorText }}
    </v-snackbar>

    <v-dialog :model-value="!connected" persistent max-width="400" class="reconnect-dialog">
      <v-card class="pa-4 d-flex align-center">
        <v-progress-circular indeterminate color="primary" size="64" class="mt-4"></v-progress-circular>
        <span class="text-h6 mt-6">Reconnecting...</span>
        <span class="mt-2">If the problem persists try reloading the page</span>
      </v-card>
    </v-dialog>
  </v-app>
</template>

<script setup>
import {onMounted, onUnmounted, ref, computed, watch} from 'vue'
import {useDisplay, useTheme} from 'vuetify'
import {useRoute, useRouter} from 'vue-router'
import TerminalWindow from '@/components/TerminalWindow.vue'
import {api, attach, canCall, dispatch, initialCommands, loadSchema, notifyOpen, send} from '@/api'

const theme = useTheme()
const router = useRouter()
const route = useRoute()

const themeMode = ref(localStorage.getItem('themeMode') || 'auto')

const themeIcon = computed(() => {
  if (themeMode.value === 'auto') return 'mdi-brightness-auto'
  return themeMode.value === 'dark' ? 'mdi-weather-night' : 'mdi-weather-sunny'
})

function cycleTheme() {
  const modes = ['light', 'dark', 'auto']
  themeMode.value = modes[(modes.indexOf(themeMode.value) + 1) % modes.length]
}

function applyTheme() {
  localStorage.setItem('themeMode', themeMode.value)
  if (themeMode.value === 'auto') {
    theme.change(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  } else {
    theme.change(themeMode.value)
  }
}

watch(themeMode, applyTheme)

const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
function handleSystemThemeChange(e) {
  if (themeMode.value === 'auto') {
    theme.change(e.matches ? 'dark' : 'light')
  }
}
mediaQuery.addEventListener('change', handleSystemThemeChange)

applyTheme()

const {lgAndUp} = useDisplay()
const drawer = ref(lgAndUp.value)
const rebootDialog = ref(false)
const powerOffDialog = ref(false)

const hostname = ref(window.__INITIAL_STATE__.hostname)
const unitotem_version = ref(window.__INITIAL_STATE__.ut_vers)
const logged_user = ref({name: 'user', perms: null})
const disp_size = ref(null)
const connected = ref(false)
const errorSnack = ref(false)
const errorText = ref('')

// `needs` is the API function that decides whether a user can use the page (its permissions come from the server)
const tabs = ref([
  {id: 'audio', name: 'Audio', icon: 'mdi-speaker', needs: 'Settings/Audio/devices'},
  {id: 'display', name: 'Display', icon: 'mdi-monitor', needs: 'Settings/Display/displays'},
  {id: 'remote', name: 'Remote control', icon: 'mdi-remote', needs: 'Settings/Remote/mode'},
  {id: 'users', name: 'Users', icon: 'mdi-account-key', needs: 'Settings/Security/users'},
  {id: 'timers', name: 'Timers', icon: 'mdi-alarm', needs: 'Settings/Cron/jobs'},
  {id: 'network', name: 'Network', icon: 'mdi-ethernet', needs: 'Settings/Network/devices'},
  {id: 'updates', name: 'Updates', icon: 'mdi-update', needs: 'Settings/Update/status'},
  {id: 'backup', name: 'Backup and restore', icon: 'mdi-history', needs: 'Settings/Security/users'},
])

const terminalOpen = ref(false)
const updateInfo = api.Settings.Update.availability.use({pick: 'availability'})

const allowed = (needs) => canCall(needs, logged_user.value)
const visibleTabs = computed(() => tabs.value.filter(tab => allowed(tab.needs)))

// The first page the user may open, for when they land on one they may not (a bookmark, a typed address)
const pagePermissions = computed(() => [
  ['/', 'Scheduler/playlists'],
  ['/viewers', 'Viewers/list'],
  ...tabs.value.map(tab => ['/settings/' + tab.id, tab.needs]),
])
// Pages are not mounted before the user is known and allowed to use them: a page asks for its data as soon as
// it appears, and a refusal for something the user was never meant to open would show up as an error
const routeAllowed = computed(() => {
  if (!logged_user.value.perms) return false
  const page = pagePermissions.value.find(([path]) => path === route.path)
  return !page || allowed(page[1])
})

watch([() => route.path, logged_user, pagePermissions], () => {
  const page = pagePermissions.value.find(([path]) => path === route.path)
  if (page && !allowed(page[1])) {
    const first = pagePermissions.value.find(([, needs]) => allowed(needs))
    router.replace(first ? first[0] : '/info')
  }
})

let ws = null;

attach((target, args) => ws.send(JSON.stringify({...args, target})), () => connected.value)

api.Settings.Network.hostname.on(data => {
  hostname.value = data.hostname
  document.title = data.hostname + ' - UniTotem Manager'
}, {init: true})

api.Settings.Security.current_user.on(data => {
  logged_user.value = data
}, {init: true})

api.Settings.Display.bounds.on(data => {
  disp_size.value = (data.width && data.height) ? data : null
})

function connectWs() {
  ws = new WebSocket(`/ws`)
  ws.onopen = () => {
    connected.value = true
    initialCommands().forEach(cmd => send(cmd))
    // getBounds requires a viewer_id; the navbar shows this device's own
    // local screen, not an arbitrary/remote one.
    api.Settings.Display.bounds({viewer_id: 'local-webview', window_id: 0})
    notifyOpen()
  }
  ws.onmessage = e => {
    const data = JSON.parse(e.data)
    if (Object.hasOwn(data, 'error')) {
      // Whoever sent the failing command gets this, nobody else
      console.error(data.target + ' returned ' + data.error, data.extra ?? '')
      // for an unexpected exception the server sends the traceback as `extra`: its last line says what happened
      const detail = data.extra ? String(data.extra).trim().split('\n').at(-1) : ''
      errorText.value = detail && detail !== data.error ? `${data.error}: ${detail}` : String(data.error)
      errorSnack.value = true
      return
    }
    dispatch(data)
  }
  ws.onclose = (e) => {
    connected.value = false
    if (e.reason === 'Not Authenticated') {
      window.location.href = '/login'
      return
    }
    setTimeout(connectWs, 3000)
  }
  ws.onerror = (e) => {
    connected.value = false
    console.error('WebSocket error:', e)
  }
}

onMounted(() => {
  loadSchema()
  connectWs()
})

onUnmounted(() => {
  mediaQuery.removeEventListener('change', handleSystemThemeChange)
  if (ws) ws.close()
})

</script>

