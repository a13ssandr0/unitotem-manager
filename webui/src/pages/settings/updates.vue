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
                {{ layoutText }}
              </td>
            </tr>
            <tr v-if="status.ab">
              <td>Running slot</td>
              <td>{{ slotName(status.slot) }}<span v-if="status.trial"> (on trial, not confirmed yet)</span></td>
            </tr>
            <tr v-if="status.ab && status.other_version">
              <td>Other slot</td>
              <td>{{ slotName(status.other_slot) }}: {{ status.other_version }}<span v-if="status.armed"> (armed for next boot)</span></td>
            </tr>
          </tbody>
        </v-table>

        <v-alert v-if="status && status.last_result && status.last_result.result === 'rolled-back'"
                 type="warning" variant="tonal" class="mt-4">
          The last update was rolled back automatically<span v-if="status.last_result.reason">
          ({{ status.last_result.reason }})</span>.
        </v-alert>

        <div v-if="busy" class="mt-4 update-progress">
          <div class="mb-1">
            {{ progressTitle }}<span v-if="progress && progress.phase">: {{ phaseNames[progress.phase] ?? progress.phase }}</span>
            <span v-if="progress && progress.eta_s"> - about {{ progress.eta_s }} s left</span>
          </div>
          <v-progress-linear :model-value="progress?.percent || 0" :indeterminate="!progress || progress.percent == null"
                             height="14" color="primary" rounded>
            <small v-if="progress && progress.percent != null">{{ Math.round(progress.percent) }}%</small>
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
import {api, onApi, useApiState} from '@/api'

const status = useApiState('Settings/Update/status', {pick: 'status'})
const check = ref(null)
const checking = ref(false)
const message = ref(null)
const confirmAction = ref(null)
const confirmDialog = computed({
  get: () => confirmAction.value !== null,
  set: (v) => { if (!v) confirmAction.value = null },
})
const progress = computed(() => status.value?.op?.progress ?? null)
// The updater reports `layout`: 1 = a single root, 2 = two slots (A/B); `ab` says whether this system runs from a slot
const layoutText = computed(() => {
  const s = status.value
  if (!s) return ''
  if (s.ab) return 'A/B (rollback available)'
  if (!s.layout || s.layout === 1) return 'Single root, no A/B yet'
  return `Layout ${s.layout} (unknown to this version of the manager)`
})
const busy = computed(() => !!status.value?.op)
const phaseNames = {check: 'checking', download: 'downloading', write: 'writing', verify: 'verifying', arm: 'preparing the restart',
  reboot: 'restarting'}
const progressTitle = computed(() => {
  const op = progress.value?.op
  const titles = {fetch: 'Downloading the update', apply: 'Installing the update', rollback: 'Going back to the previous version',
    repair: `Repairing slot ${slotName(status.value?.other_slot)}`}
  return titles[op] ?? 'Working'
})

// Slot names are always shown in capitals (the server already sends them so; this is the second lock)
const slotName = slot => (slot ?? '').toUpperCase()

const errors = {
  12: 'Another update operation is already running.',
}

// Errors that are about the state of the download, not about the device: say what to do instead.
const friendlyErrors = {
  'slot-not-verified': 'The downloaded image did not pass the verification before activation. Download it again.',
  'nothing-fetched': 'Nothing has been downloaded yet. Download the update first.',
  'no-rollback': 'There is no previous version to go back to.',
}

function describeError(result) {
  return friendlyErrors[result.error_code] ?? result.error
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
  api.Settings.Update[command]()
}

function runCheck() {
  checking.value = true
  api.Settings.Update.check()
}

onApi('Settings/Update/check', data => {
  check.value = data.check
  checking.value = false
})

for (const command of ['fetch', 'cancel', 'apply', 'confirm', 'rollback']) {
  onApi('Settings/Update/' + command, data => {
    message.value = data.result.error
      ? {type: friendlyErrors[data.result.error_code] ? 'warning' : 'error', text: describeError(data.result)}
      : (data.result.returncode === 12 ? {type: 'warning', text: errors[12]} : null)
  })
}
</script>
