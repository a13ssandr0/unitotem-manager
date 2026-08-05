<template>
  <v-container fluid class="pa-4">
    <v-row>
      <v-col>
        <h2 class="text-h5 mb-4">Viewer Assignment Matrix</h2>
        <p class="text-body-2 text-medium-emphasis mb-6">
          Assign each connected viewer to a playlist. Each row is a playlist,
          each column is a window (one screen = one window). The same playlist may drive several windows.
        </p>
      </v-col>
    </v-row>

    <!-- Assignment matrix -->
    <v-row v-if="windows.length && playlists.length">
      <v-col>
        <v-table density="comfortable" class="assignment-table">
          <thead>
            <tr>
              <th class="playlist-col">Playlist</th>
              <th v-for="win in windows" :key="win.key" class="text-center viewer-col">
                <div class="d-flex flex-column align-center ga-1">
                  <v-icon size="small">mdi-monitor</v-icon>
                  <span class="text-caption font-weight-medium">{{ win.hostname }}</span>
                  <span class="text-caption text-medium-emphasis">{{ win.screen_label }}</span>
                  <span class="text-caption text-disabled">{{ win.width }}x{{ win.height }}</span>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="playlist in playlists" :key="playlist.playlist_id">
              <td class="playlist-col">
                <div class="d-flex align-center ga-2">
                  <v-icon size="small" color="primary">mdi-playlist-play</v-icon>
                  <div>
                    <div class="font-weight-medium">{{ playlist.name }}</div>
                    <div class="text-caption text-medium-emphasis">
                      {{ playlist.enabled_count }}/{{ playlist.asset_count }} assets enabled
                    </div>
                  </div>
                </div>
              </td>
              <td v-for="win in windows" :key="win.key" class="text-center">
                <v-btn
                  :icon="isAssigned(playlist.playlist_id, win) ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline'"
                  :color="isAssigned(playlist.playlist_id, win) ? 'primary' : undefined"
                  variant="text"
                  size="small"
                  :loading="pending === `${playlist.playlist_id}:${win.key}`"
                  @click="toggle(playlist.playlist_id, win)"
                />
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-col>
    </v-row>

    <!-- Empty states -->
    <v-row v-else-if="!viewers.length">
      <v-col>
        <v-alert type="info" variant="tonal" icon="mdi-monitor-off">
          No viewers connected. Start the UniTotem viewer application on a display device.
        </v-alert>
      </v-col>
    </v-row>

    <v-row v-else-if="!playlists.length">
      <v-col>
        <v-alert type="info" variant="tonal" icon="mdi-playlist-remove">
          No playlists available. Create a playlist in the Scheduler page.
        </v-alert>
      </v-col>
    </v-row>

    <!-- Viewer detail cards -->
    <v-row v-if="viewers.length" class="mt-4">
      <v-col v-for="viewer in viewers" :key="viewer.instance_id" cols="12" sm="6" lg="4">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center ga-2">
            <v-icon>mdi-monitor</v-icon>
            {{ viewer.hostname }}
          </v-card-title>
          <v-card-text>
            <v-list density="compact">
              <v-list-item>
                <template #prepend><v-icon size="small">mdi-ip-network</v-icon></template>
                <v-list-item-title class="text-caption">{{ viewer.ip }}</v-list-item-title>
              </v-list-item>
              <v-list-item v-if="viewer.assigned_playlist">
                <template #prepend><v-icon size="small">mdi-playlist-play</v-icon></template>
                <v-list-item-title class="text-caption">
                  {{ playlistName(viewer.assigned_playlist) }}
                </v-list-item-title>
              </v-list-item>
            </v-list>

            <!-- Screen info -->
            <div v-if="viewer.screens?.length" class="mt-2">
              <div class="text-caption font-weight-medium mb-1">Screens</div>
              <v-chip
                v-for="(screen, i) in viewer.screens" :key="i"
                size="x-small" class="mr-1 mb-1" variant="outlined"
              >
                {{ screen.name }} {{ screen.width }}×{{ screen.height }}
              </v-chip>
            </div>

            <!-- Windows -->
            <div v-if="viewer.windows?.length" class="mt-2">
              <div class="text-caption font-weight-medium mb-1">Windows</div>
              <v-chip
                v-for="win in viewer.windows" :key="win.window_id"
                size="x-small" class="mr-1 mb-1" variant="outlined"
              >
                #{{ win.window_id }} {{ win.width }}×{{ win.height }}
              </v-chip>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, computed } from 'vue'

const sendCommand = window.sendCommand;

const viewersMap = ref({})    // instance_id → viewer info
const playlists = ref([])
// One entry per window: {webview_id, window_id, playlist_id}
const assignments = ref([])
const pending = ref(null)

const viewers = computed(() =>
  Object.entries(viewersMap.value).map(([id, info]) => ({
    instance_id: id,
    ...info,
    connected: true,
  }))
)

// One column per window, grouped by host: a screen is a window, and each one
// gets its own playlist. The screen name comes from the viewer, which is the
// only place that knows which output a window sits on.
const windows = computed(() =>
  viewers.value.flatMap(viewer =>
    (viewer.windows ?? []).map(win => ({
      key: `${viewer.instance_id}:${win.window_id}`,
      viewer_id: viewer.instance_id,
      window_id: win.window_id,
      hostname: viewer.hostname,
      screen_label: win.screen_name ?? `window ${win.window_id}`,
      width: win.width,
      height: win.height,
      assigned_playlist: win.assigned_playlist,
    }))
  )
)

function isAssigned(playlistId, win) {
  return win.assigned_playlist === playlistId
}

function playlistName(playlistId) {
  return playlists.value.find(p => p.playlist_id === playlistId)?.name ?? playlistId
}

async function toggle(playlistId, win) {
  pending.value = `${playlistId}:${win.key}`
  try {
    if (isAssigned(playlistId, win)) {
      sendCommand('Viewers/unassign', { viewer_id: win.viewer_id, window_id: win.window_id })
    } else {
      sendCommand('Viewers/assign',
                  { viewer_id: win.viewer_id, window_id: win.window_id, playlist_id: playlistId })
    }
  } finally {
    pending.value = null
  }
}

// Request current state on (re)connect
window.setInitCommands('Viewers/list')

onWSMessage = (msg) => {
  if (msg.target === 'Viewers/list') {
    viewersMap.value = msg.viewers ?? {}
    assignments.value = msg.assignments ?? {}
  }
  if (msg.target === 'Scheduler/playlists' || msg.playlists) {
    playlists.value = msg.playlists ?? []
  }
}
</script>

<style scoped>
.assignment-table {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
}

.playlist-col {
  min-width: 220px;
  padding: 12px 16px !important;
}

.viewer-col {
  min-width: 120px;
  padding: 12px 8px !important;
}
</style>
