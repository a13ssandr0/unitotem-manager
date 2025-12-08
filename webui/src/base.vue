<template>
  <v-app>
    <v-app-bar app clipped-left class="app-bar-border">
      <v-app-bar-nav-icon @click.stop="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title class="app-bar-title">UniTotem @ {{ hostname }}</v-toolbar-title>
      <v-spacer></v-spacer>
      <v-btn
        @click="theme.cycle()"
        text="Cycle Themes"
      ></v-btn>
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
            <v-btn prepend-icon="mdi-logout" variant="text">
              Logout
            </v-btn>
          </v-list-item>
        </v-list>
      </v-menu>
    </v-app-bar>

    <v-navigation-drawer
      v-model="drawer"
      app
      clipped
    >
      <v-list density="compact" color="#2962FF">
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
          <div>
            <a href="https://github.com/a13ssandr0/unitotem" target="_blank" rel="noopener noreferrer"
               class="text-white">
              <v-icon size="small">mdi-github</v-icon>
              Unitotem
            </a> {{ ut_vers }} by a13ssandr0
          </div>
          <div class="mt-2">Display: <span id="display_bounds">{{ disp_size.width }}x{{ disp_size.height }}</span></div>
          <div>Used {{ disk_used }} of {{ disk_total }}</div>
          <div class="mt-2">
            <span id="status_disc" class="text-red fade-in-out">Disconnected</span>
            <span id="status_conn" class="text-green d-none">Connected</span>
          </div>
        </div>
      </template>
    </v-navigation-drawer>

    <v-main>
      <div class="main-content">
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
          <v-btn color="red darken-1" text @click="rebootDialog = false">Reboot</v-btn>
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
          <v-btn color="red darken-1" text @click="powerOffDialog = false">Power Off</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-app>
</template>

<script setup>
import {onMounted, ref} from 'vue'
import {useDisplay, useTheme} from 'vuetify'

const theme = useTheme()

const {lgAndUp} = useDisplay()
const drawer = ref(lgAndUp.value)
const rebootDialog = ref(false)
const powerOffDialog = ref(false)

const hostname = ref('PCALE')
const ut_vers = ref('1.0.0')
const logged_user = ref({name: 'user'})
const disp_size = ref({width: 1920, height: 1080})
const disk_used = ref('10GB')
const disk_total = ref('100GB')

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

onMounted(() => {
  document.title = hostname.value + ' - UniTotem Login'
})

</script>

<style>
html, body {
  overflow: hidden !important;
  height: 100vh;
  font-size: 14px;
}

.main-content {
  overflow-y: auto !important;
  height: calc(100vh - var(--v-layout-top)) !important;
}

::-webkit-scrollbar {
  height: 12px;
  width: 14px;
  background: transparent;
  z-index: 12;
  overflow: visible
}

::-webkit-scrollbar-thumb {
  width: 10px;
  background-color: #2962FF;
  border-radius: 10px;
  z-index: 12;
  border: 4px solid rgba(0, 0, 0, 0);
  background-clip: padding-box;
  margin: 4px;
  min-height: 32px;
  min-width: 32px
}

::-webkit-scrollbar-thumb:hover {
  background: #2962FF
}

.main-content > :deep(.v-main__wrap) {
  min-height: 100% !important;
}

.sub-item {
  padding-inline-start: 24px !important;
}

.fade-in-out {
  animation: fade-in-out 3s ease-in-out infinite;
}

@keyframes fade-in-out {
  0%, 100% {
    opacity: 0;
  }
  50% {
    opacity: 1;
  }
}

.app-bar-border {
  border-bottom: 2px solid #2962FF !important;
}

.app-bar-title {
  font-size: 1.8rem !important;
  line-height: 2rem !important;
}

.app-bar-button {
  font-size: 1.1rem !important;
}

.v-app-bar-nav-icon :deep(.v-icon) {
  font-size: 2rem !important;
}

.v-btn--icon .v-icon {
  font-size: 1.8rem !important;
}
</style>
