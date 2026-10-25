<template>
  <v-card v-if="canCall('Ticker/sources', currentUser)" variant="outlined" class="mt-6">
    <v-card-title class="d-flex align-center ga-2">
      <v-icon>mdi-rss</v-icon> News sources (RSS / Atom)
      <v-spacer></v-spacer>
      <v-btn size="small" variant="text" prepend-icon="mdi-refresh" :disabled="!sources.length" @click="api.Ticker.fetch_now({})">Fetch all now</v-btn>
    </v-card-title>
    <v-card-text>
      <p class="text-body-2 text-medium-emphasis mb-4">
        Every source is fetched at its own interval and all of them flow into one list, newest first, that the tickers of
        the windows draw from. Turn a window's ticker on from its column above.
      </p>
      <v-table v-if="sources.length" density="comfortable">
        <thead>
          <tr><th>Source</th><th>Every</th><th>State</th><th class="text-right"></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in sources" :key="s.id">
            <td>
              <div class="d-flex align-center ga-2">
                <v-switch :model-value="s.enabled" density="compact" hide-details color="primary"
                          @update:model-value="v => api.Ticker.enable_source({id: s.id, enabled: !!v})"></v-switch>
                <div style="min-width: 0">
                  <div class="font-weight-medium">{{ s.label }}</div>
                  <div class="text-caption text-medium-emphasis url">{{ s.url }}</div>
                </div>
              </div>
            </td>
            <td>
              <v-select :items="intervals" :model-value="s.interval" density="compact" variant="outlined" hide-details min-width="130"
                        @update:model-value="v => api.Ticker.edit_source({id: s.id, interval: v})"></v-select>
            </td>
            <td>
              <v-progress-circular v-if="s.fetching" indeterminate size="18" width="2"></v-progress-circular>
              <v-chip v-else-if="s.error" size="small" color="warning" variant="tonal" prepend-icon="mdi-alert">{{ s.error }}</v-chip>
              <span v-else-if="s.last_ok" class="text-caption">{{ s.items }} headlines, updated {{ ago(s.last_ok) }}</span>
              <span v-else class="text-caption text-medium-emphasis">{{ s.enabled ? 'not fetched yet' : 'off' }}</span>
              <div v-if="s.error && s.last_ok" class="text-caption text-medium-emphasis">last good fetch {{ ago(s.last_ok) }}</div>
            </td>
            <td class="text-right text-no-wrap">
              <v-btn icon="mdi-refresh" size="small" variant="text" title="Fetch now" :disabled="!s.enabled" @click="api.Ticker.fetch_now({id: s.id})"></v-btn>
              <v-btn icon="mdi-pencil" size="small" variant="text" title="Edit" @click="startEdit(s)"></v-btn>
              <v-btn icon="mdi-delete" size="small" variant="text" title="Delete" @click="confirmDelete = s"></v-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
      <v-alert v-else type="info" variant="tonal" density="compact">No sources yet. Add one below, for example a news site's RSS address.</v-alert>

      <v-form class="d-flex ga-2 mt-4 flex-wrap align-start" @submit.prevent="add">
        <v-text-field v-model="form.url" label="Feed address" placeholder="https://example.org/rss.xml" density="compact" variant="outlined"
                      hide-details="auto" min-width="280" style="flex: 3 1 280px"></v-text-field>
        <v-text-field v-model="form.name" label="Name (optional)" density="compact" variant="outlined" hide-details style="flex: 1 1 140px"></v-text-field>
        <v-select v-model="form.interval" :items="intervals" label="Update every" density="compact" variant="outlined" hide-details style="flex: 1 1 140px"></v-select>
        <v-btn type="submit" color="primary" variant="flat" prepend-icon="mdi-plus" :disabled="!form.url.trim()">Add</v-btn>
      </v-form>

      <v-expansion-panels class="mt-4" variant="accordion" @update:model-value="v => v === 0 && api.Ticker.preview()">
        <v-expansion-panel title="The merged list">
          <v-expansion-panel-text>
            <div v-if="!merged.length" class="text-caption text-medium-emphasis">No headlines yet.</div>
            <div v-else class="text-caption mb-2">{{ merged.length }} headlines (at most 500), newest first</div>
            <v-table v-if="merged.length" density="compact" class="merged">
              <tbody>
                <tr v-for="(it, i) in merged.slice(0, 100)" :key="i">
                  <td class="text-no-wrap text-caption">{{ when(it.time) }}</td>
                  <td class="text-caption text-medium-emphasis">{{ it.source }}</td>
                  <td>{{ it.title }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </v-card-text>

    <v-dialog :model-value="!!editing" max-width="520" @update:model-value="v => { if (!v) editing = null }">
      <v-card v-if="editing" title="Edit source">
        <v-card-text class="d-flex flex-column ga-3">
          <v-text-field v-model="editing.url" label="Feed address" density="compact" variant="outlined" hide-details></v-text-field>
          <v-text-field v-model="editing.name" label="Name" density="compact" variant="outlined" hide-details></v-text-field>
          <v-select v-model="editing.interval" :items="intervals" label="Update every" density="compact" variant="outlined" hide-details></v-select>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="editing = null">Cancel</v-btn>
          <v-btn color="primary" variant="flat" @click="saveEdit">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!confirmDelete" max-width="420" @update:model-value="v => { if (!v) confirmDelete = null }">
      <v-card v-if="confirmDelete" title="Delete source">
        <v-card-text>Remove <b>{{ confirmDelete.label }}</b> and its headlines?</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="confirmDelete = null">Cancel</v-btn>
          <v-btn color="red" variant="flat" @click="remove">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import {ref} from 'vue'
import {api, canCall} from '@/api'

const currentUser = api.Settings.Security.current_user.use()
const sources = api.Ticker.sources.use({pick: 'sources', initial: []})
const merged = api.Ticker.preview.use({pick: 'items', init: false, initial: []})
// the list changes with the sources: an open panel gets the new one without being asked again
api.Ticker.sources.on(() => { if (merged.value.length) api.Ticker.preview() })

const intervals = [
  {title: '1 minute', value: 60}, {title: '5 minutes', value: 300}, {title: '15 minutes', value: 900}, {title: '30 minutes', value: 1800},
  {title: '1 hour', value: 3600}, {title: '3 hours', value: 10800}, {title: '6 hours', value: 21600}, {title: '12 hours', value: 43200},
  {title: '1 day', value: 86400},
]
const form = ref({url: '', name: '', interval: 900})
const editing = ref(null)
const confirmDelete = ref(null)

function add() {
  api.Ticker.add_source({url: form.value.url.trim(), name: form.value.name.trim(), interval: form.value.interval})
  form.value = {url: '', name: '', interval: form.value.interval}
}
function startEdit(s) { editing.value = {id: s.id, url: s.url, name: s.name, interval: s.interval} }
function saveEdit() {
  api.Ticker.edit_source({...editing.value, url: editing.value.url.trim()})
  editing.value = null
}
function remove() {
  api.Ticker.delete_source({id: confirmDelete.value.id})
  confirmDelete.value = null
}

function ago(epoch) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - epoch))
  if (s < 90) return 'a moment ago'
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`
  return `${Math.round(s / 86400)} days ago`
}
const when = epoch => new Date(epoch * 1000).toLocaleString([], {dateStyle: 'short', timeStyle: 'short'})
</script>

<style scoped>
.url { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.merged { max-height: 360px; overflow: auto; }
</style>
