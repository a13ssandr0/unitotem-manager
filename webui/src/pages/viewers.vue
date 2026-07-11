<template>
  <v-container fluid class="pa-4">
    <v-row>
      <v-col>
        <h2 class="text-h5 mb-4">Viewer Assignment Matrix</h2>
        <p class="text-body-2 text-medium-emphasis mb-6">
          Assign each connected viewer to a playlist. Each row is a playlist,
          each column is a viewer. Select the cell to assign.
        </p>
      </v-col>
    </v-row>

    <!-- Assignment matrix -->
    <v-row v-if="viewers.length && playlists.length">
      <v-col>
        <v-table density="comfortable" class="assignment-table">
          <thead>
            <tr>
              <th class="playlist-col">Playlist</th>
              <th v-for="viewer in viewers" :key="viewer.instance_id" class="text-center viewer-col">
                <div class="d-flex flex-column align-center ga-1">
                  <v-icon size="small">mdi-monitor</v-icon>
                  <span class="text-caption font-weight-medium">{{ viewer.hostname }}</span>
                  <v-chip size="x-small" :color="viewer.connected ? 'success' : 'error'" variant="flat">
                    {{ viewer.connected ? 'online' : 'offline' }}
                  </v-chip>
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
              <td v-for="viewer in viewers" :key="viewer.instance_id" class="text-center">
                <v-btn
                  :icon="isAssigned(playlist.playlist_id, viewer.instance_id) ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline'"
                  :color="isAssigned(playlist.playlist_id, viewer.instance_id) ? 'primary' : undefined"
                  variant="text"
                  size="small"
                  :loading="pending === `${playlist.playlist_id}:${viewer.instance_id}`"
                  @click="toggle(playlist.playlist_id, viewer.instance_id)"
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
const assignments = ref({})   // instance_id → playlist_id
const pending = ref(null)

const viewers = computed(() =>
  Object.entries(viewersMap.value).map(([id, info]) => ({
    instance_id: id,
    ...info,
    connected: true,
  }))
)

function isAssigned(playlistId, viewerId) {
  return assignments.value[viewerId] === playlistId
}

function playlistName(playlistId) {
  return playlists.value.find(p => p.playlist_id === playlistId)?.name ?? playlistId
}

async function toggle(playlistId, viewerId) {
  const key = `${playlistId}:${viewerId}`
  pending.value = key
  try {
    if (isAssigned(playlistId, viewerId)) {
      sendCommand('Viewers/unassign', { viewer_id: viewerId })
    } else {
      sendCommand('Viewers/assign', { viewer_id: viewerId, playlist_id: playlistId })
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
