<template>
  <v-container fluid class="pa-4">
    <h2 class="text-h5 mb-4">Viewers</h2>

    <!-- Assignment matrix: the playlist of each window. The name of a window opens its details -->
    <template v-if="windows.length">
      <v-table density="comfortable" class="assignment-table">
        <thead>
          <tr>
            <th class="playlist-col">Playlist</th>
            <th v-for="win in windows" :key="win.key" class="text-center viewer-col">
              <v-btn variant="text" size="small" class="text-none window-btn" :title="`${win.hostname}: ${win.screen_label}`"
                     @click="selectedKey = win.key">
                <span class="d-flex flex-column align-center">
                  <span class="font-weight-medium">{{ win.screen_label }}</span>
                  <span class="text-caption text-medium-emphasis">{{ win.hostname }}</span>
                  <span class="text-caption text-disabled">
                    {{ win.width }}x{{ win.height }}<template v-if="win.pane_count > 1"> · {{ win.pane_count }} panes</template>
                  </span>
                </span>
              </v-btn>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="playlist in playlists" :key="playlist.playlist_id">
            <td class="playlist-col">
              <div class="font-weight-medium">{{ playlist.name }}</div>
              <div class="text-caption text-medium-emphasis">{{ playlist.enabled_count }}/{{ playlist.asset_count }} assets enabled</div>
            </td>
            <td v-for="win in windows" :key="win.key" class="text-center">
              <v-checkbox-btn :model-value="isAssigned(playlist.playlist_id, win)" color="primary" :disabled="!canAssign"
                              :aria-label="`${playlist.name} on ${win.screen_label}`"
                              @update:model-value="v => assign(playlist.playlist_id, win, !!v)"></v-checkbox-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
      <v-alert v-if="!playlists.length" type="info" variant="tonal" density="compact" icon="mdi-playlist-remove" class="mt-4">
        No playlists. Create one in Scheduler.
      </v-alert>
    </template>

    <!-- Empty states -->
    <v-alert v-else-if="!viewerCount" type="info" variant="tonal" icon="mdi-monitor-off">No viewers connected.</v-alert>
    <v-alert v-else type="info" variant="tonal" icon="mdi-monitor-off">No windows on the connected viewers.</v-alert>

    <TickerSources v-if="canTicker" />

    <ViewerDetail v-model="detailOpen" :win="selected" :playlists="playlists" :outputs="selectedOutputs" />
  </v-container>
</template>

<script setup>
import {ref, computed} from 'vue'
import {api, canCall} from '@/api'
import ViewerDetail from '@/components/ViewerDetail.vue'
import {buildWindows} from '@/components/ViewerCore.js'

const viewersMap = ref({})    // instance_id → viewer info
const playlists = api.Scheduler.playlists.use({pick: 'playlists', initial: []})
const viewerCount = computed(() => Object.keys(viewersMap.value).length)
// one column per window (see buildWindows)
const windows = computed(() => buildWindows(viewersMap.value))

// The details of a window are open while it exists: the window is read from the state on every render
const selectedKey = ref(null)
const selected = computed(() => windows.value.find(w => w.key === selectedKey.value) ?? null)
const detailOpen = computed({get: () => !!selected.value, set: v => { if (!v) selectedKey.value = null }})

const currentUser = api.Settings.Security.current_user.use()
const canAssign = computed(() => canCall('Viewers/assign_playlist', currentUser.value))
const canTicker = computed(() => !!currentUser.value?.perms && canCall('Ticker/sources', currentUser.value)) // admin only (the RSS ticker)

const isAssigned = (playlistId, win) => win.assigned_playlist === playlistId
// one playlist per window: assigning another one replaces the current one
function assign(playlistId, win, on) {
  const ids = {viewer_id: win.viewer_id, window_id: win.window_id}
  if (on) api.Viewers.assign_playlist({...ids, playlist_id: playlistId})
  else api.Viewers.unassign_playlist(ids)
}

function updateWindow(msg, key, value) {
  const win = viewersMap.value[msg.viewer_id]?.windows?.find(w => w.window_id === msg.window_id)
  if (win) win[key] = value
}
api.Viewers.screensaver.on(({viewer_id, window_id, ...settings}) => updateWindow({viewer_id, window_id}, 'screensaver', settings))
api.Viewers.refresh.on(({viewer_id, window_id, ...settings}) => updateWindow({viewer_id, window_id}, 'refresh', settings))
// the keyboard of one window, pushed by the server after every change (and the reply to asking for it)
api.Viewers.keyboard.on(({viewer_id, window_id, ...settings}) => updateWindow({viewer_id, window_id}, 'keyboard', settings))
api.Viewers.language.on(({viewer_id, window_id, language}) => updateWindow({viewer_id, window_id}, 'language', language))
// the layout of one window, pushed by the server after every change (a refusal comes with `error` and no layout)
api.Viewers.layout.on(({viewer_id, window_id, layout}) => { if (layout) updateWindow({viewer_id, window_id}, 'layout', layout) })

const audioOutputs = ref({})   // viewer id -> outputs its windows can use
api.Viewers.audio_outputs.on(msg => {
  audioOutputs.value = {...audioOutputs.value, [msg.viewer_id]: msg.outputs}
})

// Requests the current state on (re)connect
api.Viewers.list.on(msg => {
  Object.keys(msg.viewers ?? {}).forEach(id => api.Viewers.audio_outputs({viewer_id: id}))
  viewersMap.value = msg.viewers ?? {}
}, {init: true})
</script>

<style scoped>
.assignment-table {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
}

.playlist-col {
  min-width: 180px;
  padding: 12px 16px !important;
}

.viewer-col {
  min-width: 120px;
  padding: 8px !important;
}

.window-btn {
  height: auto;
  padding: 4px 8px;
}
</style>
