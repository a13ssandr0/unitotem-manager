<template>
  <v-card v-if="status && status.kind === 'pi' && status.supported" class="ma-4 mt-0" max-width="600" width="100%" title="Raspberry Pi">
    <v-card-text>
      <v-table density="compact">
        <tbody>
          <tr>
            <td class="row-name">Firmware</td>
            <td>
              {{ filesText }}<span v-if="files?.update === true"> &rarr; {{ files.available }}</span>
            </td>
            <td class="row-state text-right">
              <v-chip v-if="files?.update === true" size="x-small" color="primary">Update available</v-chip>
              <v-chip v-else-if="files?.update === false" size="x-small" color="green">Up to date</v-chip>
            </td>
          </tr>
          <tr v-if="eeprom">
            <td class="row-name">EEPROM</td>
            <td>
              {{ eeprom.installed || '-' }}<span v-if="eeprom.update === true && eeprom.available"> &rarr; {{ eeprom.available }}</span>
            </td>
            <td class="row-state text-right">
              <v-chip v-if="eeprom.pending" size="x-small" color="warning">Restart pending</v-chip>
              <v-chip v-else-if="eeprom.update === true" size="x-small" color="primary">Update available</v-chip>
              <v-chip v-else-if="eeprom.update === false" size="x-small" color="green">Up to date</v-chip>
            </td>
          </tr>
        </tbody>
      </v-table>

      <div v-if="blockers.length > 0" class="d-flex flex-wrap ga-1 mt-3">
        <v-chip v-for="blocker in blockers" :key="blocker" size="x-small" color="warning" variant="tonal">
          {{ blockerNames[blocker] ?? blocker }}
        </v-chip>
      </div>

      <div v-if="busy" class="mt-4 firmware-progress">
        <div class="mb-1">
          {{ opTitle }}<span v-if="phaseName">: {{ phaseName }}</span>
        </div>
        <v-progress-linear :model-value="status.percent || 0" :indeterminate="status.percent == null"
                           height="14" color="primary" rounded>
          <small v-if="status.percent != null">{{ Math.round(status.percent) }}%</small>
        </v-progress-linear>
      </div>

      <v-alert v-if="errorText" type="error" variant="tonal" class="mt-4">
        {{ errorText }}
      </v-alert>
    </v-card-text>
    <v-card-actions>
      <v-spacer></v-spacer>
      <v-btn
        v-if="status.reboot_required && !status.restart_pending"
        variant="text"
        color="red"
        prepend-icon="mdi-restart"
        :disabled="busy || !canRestart"
        @click="restartDialog = true"
      >Restart</v-btn>
      <v-btn
        variant="text"
        color="primary"
        :prepend-icon="checking || status.op === 'check' ? 'mdi-loading mdi-spin' : 'mdi-refresh'"
        :disabled="checking || busy"
        @click="runCheck"
      >Check</v-btn>
      <v-btn
        variant="text"
        color="primary"
        prepend-icon="mdi-download"
        :disabled="!canUpdate"
        @click="confirmDialog = true"
      >Update</v-btn>
    </v-card-actions>
    <v-dialog v-model="confirmDialog" max-width="420">
      <v-card :title="canRestart ? 'Update and restart?' : 'Update firmware?'">
        <v-card-text>
          <div>Do not power off during the update.</div>
          <div v-if="!canRestart">A restart is needed to finish.</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="confirmDialog = false">Cancel</v-btn>
          <v-btn variant="text" color="red" @click="applyUpdate">Update</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog v-model="restartDialog" max-width="420">
      <v-card title="Restart now?">
        <v-card-text>The device will restart now.</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="restartDialog = false">Cancel</v-btn>
          <v-btn variant="text" color="red" @click="restart">Restart</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<style scoped>
/* name and state take only what they need, the version takes the rest */
.row-name, .row-state { width: 1%; white-space: nowrap; }
</style>

<script setup>
import {computed, onUnmounted, ref, watch} from 'vue'
import {api, canCall, onOpen, useApiState} from '@/api'

// The failure of a command arrives as a window event (base.vue shows the message); this runs `callback` for the target's failures
function onApiError(target, callback) {
  const listener = e => { if (e.detail.target === target) callback(e.detail) }
  window.addEventListener('unitotem:api-error', listener)
  onUnmounted(() => window.removeEventListener('unitotem:api-error', listener))
}

// The server state is the only source: every change, the progress included, arrives as this pushed event
const status = useApiState('Settings/Firmware/status', {pick: 'status'})
const currentUser = api.Settings.Security.current_user.use()

const checking = ref(false)
const checkedBefore = ref(null)
const confirmDialog = ref(false)
const restartDialog = ref(false)

const files = computed(() => status.value?.files ?? null)
// A custom boot files set shows the package it came from
const filesText = computed(() => {
  const f = files.value
  if (f?.custom) return f.package ? `Custom (from ${f.package})` : 'Custom'
  return f?.installed || '-'
})
const eeprom = computed(() => status.value?.eeprom ?? null)
const blockers = computed(() => status.value?.blockers ?? [])
// The helper's failures are text, a failed operation is {error, error_code}: both are shown as the text
const errorText = computed(() => {
  const error = status.value?.error
  return typeof error === 'string' ? error : (error?.error ?? '')
})
const busy = computed(() => !!status.value?.op)
// The EEPROM is only applied when it is not staged already (`pending` is a restart that finishes it)
const applyFiles = computed(() => files.value?.update === true)
const applyEeprom = computed(() => eeprom.value?.update === true && !eeprom.value?.pending)
const canUpdate = computed(() => (applyFiles.value || applyEeprom.value) && !blockers.value.length && !busy.value)
const canRestart = computed(() => !!currentUser.value?.perms && canCall('Power/reboot', currentUser.value))

const blockerNames = {
  'under-voltage': 'Under-voltage',
  'pending-confirm': 'Trial boot pending',
  'not-writable': 'Boot partition read-only',
  busy: 'Busy',
  'no-power-info': 'No power info',
}
const phaseNames = {fetch: 'downloading', verify: 'verifying', stage: 'preparing', swap: 'activating', eeprom: 'EEPROM', done: 'done'}
const phaseName = computed(() => phaseNames[status.value?.phase] ?? status.value?.phase ?? '')
const opTitle = computed(() => (status.value?.op === 'check' ? 'Checking firmware' : 'Updating firmware'))

function runCheck() {
  checking.value = true
  checkedBefore.value = status.value?.checked_at ?? null
  api.Settings.Firmware.check()
}
// The check is done when the pushed status carries a new check time
watch(() => status.value?.checked_at, (at) => {
  if (checking.value && at !== checkedBefore.value) checking.value = false
})
onApiError('Settings/Firmware/check', () => { checking.value = false })
// An answer lost with the connection never comes: the check is not waited for after a reconnection
onOpen(() => { checking.value = false })

function applyUpdate() {
  confirmDialog.value = false
  api.Settings.Firmware.apply({files: applyFiles.value, eeprom: applyEeprom.value, restart: canRestart.value})
}

function restart() {
  restartDialog.value = false
  api.Power.reboot()
}
</script>
