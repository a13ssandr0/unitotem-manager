<template>
  <div class="d-flex justify-center flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <v-card-text>
        <v-alert v-if="status && !status.supported" type="warning" variant="tonal" class="mb-4">
          This system image cannot be updated from here: the updater is not installed.
        </v-alert>
        <v-alert v-else-if="status && status.error" type="error" variant="tonal" class="mb-4">
          {{ status.error }}
        </v-alert>

        <v-table v-if="status && status.supported && !status.error" density="compact">
          <tbody>
            <tr>
              <td>Installed version</td>
              <td>{{ status.current_version }}</td>
            </tr>
            <tr>
              <td>Image layout</td>
              <td>
                {{ status.ab ? 'A/B (rollback available)' : 'Single root, no A/B yet' }}
              </td>
            </tr>
            <tr v-if="status.op">
              <td>Operation in progress</td>
              <td>{{ status.op }}</td>
            </tr>
          </tbody>
        </v-table>

        <v-alert v-if="check && check.error" type="error" variant="tonal" class="mt-4">
          Check failed: {{ check.error }}
          <span v-if="check.error_code" class="text-caption">({{ check.error_code }})</span>
        </v-alert>
        <v-alert v-else-if="check && check.available" type="info" variant="tonal" class="mt-4">
          Version {{ check.version }} ({{ check.channel }}) is available, {{ formatSize(check.size) }}.
          <div v-if="check.needs_layout_migration">
            This update changes the disk layout and needs a migration.
          </div>
        </v-alert>
        <v-alert v-else-if="check" type="success" variant="tonal" class="mt-4">
          The system is up to date.
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn
          variant="text"
          color="primary"
          :prepend-icon="checking ? 'mdi-loading mdi-spin' : 'mdi-refresh'"
          :disabled="checking || !status || !status.supported"
          @click="runCheck"
        >Check updates</v-btn>
      </v-card-actions>
    </v-card>
  </div>
</template>

<script setup>
import {ref} from 'vue'

const status = ref(null)
const check = ref(null)
const checking = ref(false)

function formatSize(bytes) {
  if (!bytes) return 'unknown size'
  const units = ['B', 'KiB', 'MiB', 'GiB']
  let i = 0
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024
    i++
  }
  return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`
}

function runCheck() {
  checking.value = true
  window.sendCommand('Settings/Update/check')
}

window.setInitCommands('Settings/Update/status')

onWSMessage = (data) => {
  switch (data.target) {
    case 'Settings/Update/status':
      status.value = data.status
      break
    case 'Settings/Update/check':
      check.value = data.check
      checking.value = false
      break
  }
}
</script>
