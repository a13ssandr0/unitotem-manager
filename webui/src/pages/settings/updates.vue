<template>
  <div class="d-flex justify-center flex-column align-center">
    <v-card
      class="ma-4"
      max-width="600"
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

        <v-alert v-else-if="status && !status.ab" type="warning" variant="tonal" class="mb-4">
          The updater did not report the two system slots, so updates are unavailable here.
        </v-alert>

        <v-table v-if="status && status.supported && !status.error && status.ab" density="compact">
          <tbody>
            <tr v-for="slot in slots" :key="slot.name">
              <td class="slot-name">Slot {{ slot.name }}</td>
              <td>
                {{ slot.version || 'no system installed' }}
                <v-chip v-if="slot.running" size="x-small" color="green" class="ml-2">running</v-chip>
                <v-chip v-if="slot.running && status.trial" size="x-small" color="warning" class="ml-1">on trial, not confirmed yet</v-chip>
                <v-chip v-if="!slot.running && status.armed" size="x-small" color="primary" class="ml-2">armed for next boot</v-chip>
                <v-chip v-else-if="slot.next && !slot.running" size="x-small" color="primary" class="ml-2">starts at next boot</v-chip>
              </td>
              <td class="slot-action text-right">
                <v-btn
                  v-if="slot.running"
                  variant="text"
                  color="red"
                  prepend-icon="mdi-content-copy"
                  :disabled="!canCopy"
                  @click="confirmAction = 'copy_to_other'"
                >Copy to slot {{ slotName(status.other_slot) }}</v-btn>
                <v-btn
                  v-else
                  variant="text"
                  color="red"
                  prepend-icon="mdi-swap-horizontal"
                  :disabled="!canSwitch"
                  @click="confirmAction = 'rollback'"
                >Restart on slot {{ slot.name }}</v-btn>
              </td>
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
          :disabled="checking || busy || !status || !status.supported"
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
          v-else-if="status?.pending_confirm"
          variant="text"
          color="green"
          prepend-icon="mdi-check"
          @click="send('confirm')"
        >Confirm this version</v-btn>
        <v-btn
          v-else-if="newerInOther"
          variant="text"
          color="red"
          prepend-icon="mdi-restart"
          :disabled="!canInstall"
          @click="confirmAction = 'apply'"
        >Install and restart</v-btn>
        <v-btn
          v-else
          variant="text"
          color="primary"
          prepend-icon="mdi-download"
          :disabled="!canDownload"
          @click="send('fetch')"
        >Download</v-btn>
      </v-card-actions>
    </v-card>
    <v-card v-if="status && status.ab && status.auto" class="ma-4 mt-0" max-width="600" width="100%" title="Automatic updates">
      <v-card-text>
        <div class="d-flex flex-wrap align-center ga-4">
          <v-select
            class="policy-mode"
            density="compact"
            hide-details
            label="When a new version is available"
            :items="policyModes"
            :model-value="policy.mode"
            @update:model-value="setPolicy({mode: $event})"
          ></v-select>
          <v-text-field
            v-if="policy.mode === 'scheduled'"
            class="policy-time"
            density="compact"
            hide-details
            type="time"
            label="Restart at"
            :model-value="policy.time"
            @update:model-value="v => v && setPolicy({time: v})"
          ></v-text-field>
        </div>
        <div v-if="autoText" class="text-caption mt-2">{{ autoText }}</div>
        <div v-if="status.staged" class="text-caption mt-1">
          An update is prepared: it starts at the next restart of the device.
        </div>
      </v-card-text>
    </v-card>
    <v-dialog v-model="confirmDialog" max-width="420">
      <v-card :title="dialogTitle">
        <v-card-text v-if="confirmAction === 'copy_to_other'">
          Slot {{ slotName(status?.other_slot) }} ({{ status?.other_version || 'empty' }}) will be replaced by a copy of
          slot {{ slotName(status?.slot) }} ({{ status?.current_version }}). Nothing can be rolled back to until the copy is done,
          and the previous version of slot {{ slotName(status?.other_slot) }} is lost. The device keeps running meanwhile.
        </v-card-text>
        <v-card-text v-else>
          The device will restart now{{ confirmAction === 'apply' ? ' into the new version'
            : ` into slot ${slotName(status?.other_slot)} (version ${status?.other_version})` }}
          and will be unavailable for a few minutes. Uploads are kept; settings are those of that slot.
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

<style scoped>
/* name and action take only what they need, the version and its chips take the rest */
.slot-name, .slot-action { width: 1%; white-space: nowrap; }
.policy-mode { max-width: 340px; min-width: 240px; }
.policy-time { max-width: 140px; }
</style>

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
// The two slots, each with the version it holds: `current_version` is the running one, `other_version` the other
// (empty while it holds no system); `default_slot` is the one the next boot starts
const slots = computed(() => {
  const s = status.value
  if (!s?.ab) return []
  const cur = slotName(s.slot), other = slotName(s.other_slot)
  return [
    {name: cur, version: s.current_version, running: true, next: slotName(s.default_slot) === cur},
    {name: other, version: s.other_version, running: false, next: slotName(s.default_slot) === other},
  ].sort((x, y) => x.name.localeCompare(y.name))
})
// "Install and restart" only makes sense while there is something left to install: the other slot holds a version NEWER than
// the running one (a downloaded update). Otherwise the button that switches slot is enough. Same ordering as the updater's vkey.
function versionParts(v) {
  return String(v ?? '').replace(/-/g, '.').split('.').map(x => (/^\d+$/.test(x) ? Number(x) : x))
}
function isNewer(a, b) {
  const x = versionParts(a), y = versionParts(b)
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] === y[i]) continue
    if (x[i] === undefined) return false
    if (y[i] === undefined) return true
    if (typeof x[i] === typeof y[i]) return x[i] > y[i]
    return typeof x[i] === 'string'
  }
  return false
}
const newerInOther = computed(() => !!status.value?.other_version && isNewer(status.value.other_version, status.value.current_version))
const dialogTitle = computed(() => {
  const other = slotName(status.value?.other_slot)
  if (confirmAction.value === 'apply') return 'Install and restart?'
  if (confirmAction.value === 'copy_to_other') return `Replace slot ${other} with a copy of slot ${slotName(status.value?.slot)}?`
  return `Restart on slot ${other}?`
})
const policyModes = [
  {title: 'Do nothing', value: 'off'},
  {title: 'Download only', value: 'download'},
  {title: 'Download and prepare (starts at the next restart)', value: 'stage'},
  {title: 'Download and restart at once', value: 'reboot'},
  {title: 'Download and restart at a set time', value: 'scheduled'},
]
const policy = computed(() => ({mode: 'off', time: '04:00', ...(status.value?.auto?.policy ?? {})}))
function setPolicy(change) {
  const next = {...policy.value, ...change}
  api.Settings.Update.set_policy({mode: next.mode, time: next.time})
}
// What the automatic run did last, in words (only the states worth telling)
const autoTexts = {
  'up-to-date': 'The system is up to date.',
  downloaded: 'A new version was downloaded.',
  staged: 'A new version is prepared and starts at the next restart.',
  'waiting-for-schedule': 'A new version is prepared and will start at the scheduled time.',
  rebooting: 'Restarting into the new version.',
  'check-failed': 'The last automatic check failed, it will be retried.',
  'fetch-failed': 'The last automatic download failed, it will be retried.',
  'needs-manual': 'The new version needs a manual installation.',
}
const autoText = computed(() => autoTexts[status.value?.auto?.state?.action] ?? '')
const busy = computed(() => !!status.value?.op)
const canDownload = computed(() => !!status.value?.ab && !!check.value?.available)
const canInstall = computed(() => !!status.value?.ab && !busy.value && !status.value.armed && newerInOther.value)
const canCopy = computed(() => !!status.value?.ab && !busy.value && !status.value.pending_confirm)
const canSwitch = computed(() => !!status.value?.ab && !busy.value && !!status.value.other_version)
const phaseNames = {check: 'checking', download: 'downloading', write: 'writing', verify: 'verifying', arm: 'preparing the restart',
  reboot: 'restarting'}
const damaged = ['label', 'fsck', 'mount']
const progressTitle = computed(() => {
  const op = progress.value?.op
  const titles = {fetch: 'Downloading the update', apply: 'Installing the update', rollback: 'Going back to the previous version',
    repair: status.value?.copy_from ? `Copying slot ${slotName(status.value.copy_from)} to slot ${slotName(status.value.other_slot)}`
      : `Rebuilding slot ${slotName(status.value?.other_slot)}` + (damaged.includes(progress.value?.cause) ||
      progress.value?.cause?.startsWith('bootfile:') ? ' after damage' : progress.value?.cause === 'empty' ? ' (incomplete system)' : '')}
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

for (const command of ['fetch', 'cancel', 'apply', 'confirm', 'rollback', 'copy_to_other', 'set_policy']) {
  onApi('Settings/Update/' + command, data => {
    message.value = data.result.error
      ? {type: friendlyErrors[data.result.error_code] ? 'warning' : 'error', text: describeError(data.result)}
      : (data.result.returncode === 12 ? {type: 'warning', text: errors[12]} : null)
  })
}
</script>
