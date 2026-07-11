<template>
  <v-app>
    <v-app-bar app clipped-left :class="connected ? 'app-bar-border' : 'app-bar-border-disconnected'">
      <v-app-bar-nav-icon @click.stop="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title class="app-bar-title">UniTotem @ {{ hostname }}</v-toolbar-title>
      <v-spacer></v-spacer>
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
        <v-list-item to="/" exact prepend-icon="mdi-view-dashboard" title="Scheduler"></v-list-item>

        <v-divider class="my-2"></v-divider>
        <v-list-subheader>Settings</v-list-subheader>
        <v-list-item
          v-for="(tab, i) in tabs"
          :key="i"
          :to="'/settings/' + tab.id"
          :prepend-icon="tab.icon"
          :title="tab.name"
          class="sub-item"
        ></v-list-item>
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
        <router-view/>
      </div>
    </v-main>

    <v-dialog v-model="rebootDialog" max-width="fit-content">
      <v-card class="pa-2 pb-0 rounded-lg">
        <v-card-title class="text-h5">Are you sure you want to reboot?</v-card-title>
        <v-card-text>This will reboot the system.</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="rebootDialog = false">Cancel</v-btn>
          <v-btn color="red darken-1" text @click="rebootDialog = false; sendCommand('Settings/Power/reboot')">Reboot</v-btn>
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
          <v-btn color="red darken-1" text @click="powerOffDialog = false; sendCommand('Settings/Power/poweroff')">Power Off</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

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
import {useRouter} from 'vue-router'

const theme = useTheme()
const router = useRouter()

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
const logged_user = ref({name: 'user', permissions: []})
const disp_size = ref(null)
const connected = ref(false)
const init_commands = ref([]);

const tabs = ref([
  {id: 'audio', name: 'Audio', icon: 'mdi-speaker'},
  {id: 'display', name: 'Display', icon: 'mdi-monitor'},
  {id: 'remote', name: 'Remote control', icon: 'mdi-remote'},
  {id: 'users', name: 'Users', icon: 'mdi-account-key'},
  {id: 'timers', name: 'Timers', icon: 'mdi-alarm'},
  {id: 'network', name: 'Network', icon: 'mdi-ethernet'},
  {id: 'updates', name: 'Updates', icon: 'mdi-update'},
  {id: 'backup', name: 'Backup and restore', icon: 'mdi-history'},
])

let ws = null;

window.sendCommand = (target, args) => {
  if (args === undefined) args = {};
  args.target = target
  ws.send(JSON.stringify(args));
}

window.setInitCommands = (...commands) => {
  init_commands.value = commands;
  if (connected.value) {
    new Set(commands).forEach(cmd => {
      sendCommand(cmd)
    })
  }
}

window.onWSOpen = (e) => {}
window.onWSMessage = (data, e) => {}
window.onWSClose = (e) => {}
window.onWSError = (e) => {}

window.isWSReady = () => {
  return connected.value
}

function connectWs() {
  ws = new WebSocket(`/ws`)
  ws.onopen = (e) => {
    connected.value = true
    new Set([...init_commands.value,
      'Settings/hostname', 'Settings/Security/getUser', 'Settings/Display/getBounds'
    ]).forEach(cmd => sendCommand(cmd))
    window.onWSOpen(e)
  }
  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.target === 'Settings/hostname') {
      hostname.value = data.hostname;
      document.title = data.hostname + ' - UniTotem Manager';
    } else if (data.target === 'Settings/Security/getUser'){
      logged_user.value = data;
    } else if (data.target === 'Settings/Display/getBounds'){
      disp_size.value = (data.width && data.height) ? data : null;
    }
    if (data.hasOwnProperty('error')) {
      // messageModal.find('.modal-title').text('Error');
      // messageModal.find('.modal-body h6').text(data.target + ' returned ' + data.error);
      // messageModal.find('.modal-body code').text(data.extra);
      // new bootstrap.Modal(messageModal).show();
      return;
    }
    window.onWSMessage(data, e)
  };
  ws.onclose = (e) => {
    connected.value = false
    window.onWSClose(e)
    if (e.reason === 'Not Authenticated') {
      window.location.href = '/login'
      return
    }
    setTimeout(connectWs, 3000)
  }
  ws.onerror = (e) => {
    connected.value = false
    console.error('WebSocket error:', e)
    window.onWSError(e)
  }
}

onMounted(() => {
  connectWs()
})

onUnmounted(() => {
  mediaQuery.removeEventListener('change', handleSystemThemeChange)
  if (ws) ws.close()
})

</script>

