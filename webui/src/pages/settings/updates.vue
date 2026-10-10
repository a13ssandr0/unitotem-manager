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
            <tr v-if="status.ab">
              <td>Running slot</td>
              <td>{{ status.slot }}<span v-if="status.trial"> (on trial, not confirmed yet)</span></td>
            </tr>
            <tr v-if="status.ab && status.other_version">
              <td>Other slot</td>
              <td>{{ status.other_slot }}: {{ status.other_version }}<span v-if="status.armed"> (armed for next boot)</span></td>
            </tr>
          </tbody>
        </v-table>

        <v-alert v-if="status && status.last_result && status.last_result.result === 'rolled-back'"
                 type="warning" variant="tonal" class="mt-4">
          The last update was rolled back automatically<span v-if="status.last_result.reason">
          ({{ status.last_result.reason }})</span>.
        </v-alert>

        <div v-if="progress" class="mt-4">
          <div class="mb-1">
            {{ progress.op }}: {{ progress.phase }}
            <span v-if="progress.eta_s"> - about {{ progress.eta_s }} s left</span>
          </div>
          <v-progress-linear :model-value="progress.percent || 0" height="14" color="primary" rounded>
            <small>{{ Math.round(progress.percent || 0) }}%</small>
          </v-progress-linear>
          <div class="text-caption mt-1">
            While the new image is being written there is no rollback available.
          </div>
        </div>

        <v-alert v-if="message" :type="message.type" variant="tonal" class="mt-4">{{ message.text }}</v-alert>

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
        <v-btn
          v-if="busy"
          variant="text"
          color="warning"
          prepend-icon="mdi-cancel"
          @click="send('cancel')"
        >Cancel</v-btn>
        <v-btn
          v-else-if="check && check.available"
          variant="text"
          color="primary"
          prepend-icon="mdi-download"
          :disabled="!status.ab"
          @click="send('fetch')"
        >Download</v-btn>
        <v-btn
          v-if="status && status.ab && !busy && status.other_version && !status.armed"
          variant="text"
          color="red"
          prepend-icon="mdi-restart"
          @click="confirmAction = 'apply'"
        >Install and restart</v-btn>
        <v-btn
          v-if="status && status.pending_confirm"
          variant="text"
          color="green"
          prepend-icon="mdi-check"
          @click="send('confirm')"
        >Confirm this version</v-btn>
        <v-btn
          v-if="status && status.rollback_available"
          variant="text"
          color="red"
          prepend-icon="mdi-undo"
          @click="confirmAction = 'rollback'"
        >Roll back</v-btn>
      </v-card-actions>
    </v-card>
    <v-dialog v-model="confirmDialog" max-width="420">
      <v-card :title="confirmAction === 'apply' ? 'Install and restart?' : 'Roll back?'">
        <v-card-text>
          The device will restart now{{ confirmAction === 'apply' ? ' into the new version' : ' into the previous version' }}
          and will be unavailable for a few minutes. Your data is kept.
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="confirmAction = null">Cancel</v-btn>
          <v-btn variant="text" color="red" @click="send(confirmAction); confirmAction = null">Continue</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import {computed, ref} from 'vue'

const status = ref(null)
const check = ref(null)
const checking = ref(false)
const message = ref(null)
const confirmAction = ref(null)
const confirmDialog = computed({
  get: () => confirmAction.value !== null,
  set: (v) => { if (!v) confirmAction.value = null },
})
const progress = computed(() => status.value?.op?.progress ?? null)
const busy = computed(() => !!status.value?.op)

const errors = {
  12: 'Another update operation is already running.',
}

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

function send(command) {
  message.value = null
  window.sendCommand('Settings/Update/' + command)
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
    case 'Settings/Update/fetch':
    case 'Settings/Update/cancel':
    case 'Settings/Update/apply':
    case 'Settings/Update/confirm':
    case 'Settings/Update/rollback':
      message.value = data.result.error
        ? {type: 'error', text: data.result.error}
        : (data.result.returncode === 12 ? {type: 'warning', text: errors[12]} : null)
      break
    case 'Settings/Update/check':
      check.value = data.check
      checking.value = false
      break
  }
}
</script>
