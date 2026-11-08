<!--
  Keeping a window in shape: the reload of its web page (after an idle time and/or at given times) and the developer tools of
  the page, which need the node plugin and an administrator.
-->
<template>
  <div>
    <div class="text-subtitle-2 mb-2">Page refresh</div>
    <div v-if="win.screensaver.enabled" class="text-caption text-medium-emphasis mb-2">Not used on an interactive screen</div>
    <div class="d-flex flex-wrap ga-3 mb-6">
      <v-text-field :model-value="win.refresh.refresh_after_idle" label="After idle (s)" type="number" min="10"
                    placeholder="never" density="compact" variant="outlined" hide-details min-width="160"
                    :disabled="refreshLocked || win.screensaver.enabled"
                    @change="e => setRefresh({refresh_after_idle: e.target.value ? Number(e.target.value) : null})"></v-text-field>
      <v-textarea :model-value="win.refresh.refresh_schedules.join('\n')" label="At times" rows="2" auto-grow
                  placeholder="0 6 * * *" density="compact" variant="outlined" min-width="240"
                  hint="minute hour day month weekday, one per line" persistent-hint
                  :disabled="refreshLocked || win.screensaver.enabled"
                  @change="e => setRefresh({refresh_schedules: schedulesFromText(e.target.value)})"></v-textarea>
    </div>

    <div class="text-subtitle-2 mb-2">Developer tools</div>
    <v-btn variant="tonal" prepend-icon="mdi-open-in-new" :loading="devtoolsBusy" :disabled="!canDevtools" @click="openDevtools">Open</v-btn>

    <v-snackbar :model-value="!!devtoolsError" color="error" :timeout="8000" @update:model-value="devtoolsError = ''">{{ devtoolsError }}</v-snackbar>
  </div>
</template>

<script setup>
// The refresh is only sent with the values the window has; the reload follows the new settings after a moment
import {computed, ref} from 'vue'
import {api, canCall} from '@/api'
import {schedulesFromText} from './ViewerCore.js'

const props = defineProps({
  win: {type: Object, required: true},
})

const currentUser = api.Settings.Security.current_user.use()
const refreshLocked = computed(() => !canCall('Viewers/set_refresh', currentUser.value))
// The developer tools of a page: administrators only, and only where the node plugin that serves them is loaded
const canDevtools = computed(() => canCall('Viewers/set_keyboard', currentUser.value) && Boolean(window.__INITIAL_STATE__.plugins?.includes('node')))

function setRefresh(patch) {
  const {refresh_after_idle, refresh_schedules} = {...props.win.refresh, ...patch}
  const ids = {viewer_id: props.win.viewer_id, window_id: props.win.window_id}
  api.Viewers.set_refresh({...ids, refresh_after_idle, refresh_schedules})
  setTimeout(() => api.Viewers.refresh(ids), 500)
}

const devtoolsBusy = ref(false)
const devtoolsError = ref('')
async function openDevtools() {
  devtoolsBusy.value = true
  // The tab is opened by the click itself (a popup opened after a request is blocked), then pointed at the tools
  const tab = window.open('about:blank', '_blank')
  try {
    const reply = await fetch('/devtools/open', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({window_id: props.win.window_id}),
    })
    if (!reply.ok) throw new Error((await reply.json().catch(() => ({}))).detail || reply.statusText)
    const {path} = await reply.json()
    if (tab) tab.location.href = path.replace('{host}', location.host)
  } catch (e) {
    tab?.close()
    devtoolsError.value = `Cannot open the developer tools: ${e.message}`
  } finally {
    devtoolsBusy.value = false
  }
}
</script>
