<template>
  <div class="d-flex justify-center">
    <v-card
      class="ma-4"
      max-width="600"
      width="100%"
      :title="String($route.name)"
    >
      <v-card-text v-if="status">
        <div class="text-h3 font-weight-medium text-center" aria-live="off">{{ clockTime }}</div>
        <div class="text-body-1 text-center text-medium-emphasis mt-1">{{ clockDate }}</div>
        <div class="text-body-2 text-center mt-2">
          {{ status.timezone }} ({{ formatOffset(status.utc_offset) }})
        </div>

        <v-autocomplete
          class="mt-6"
          :model-value="status.timezone"
          :items="status.timezones"
          :disabled="pending === 'timezone'"
          label="Time zone"
          variant="outlined"
          :clearable="false"
          @update:model-value="setTimezone"
        ></v-autocomplete>

        <div class="d-flex align-center flex-wrap ga-3">
          <v-switch
            :model-value="status.ntp"
            :disabled="!status.can_ntp || pending === 'ntp'"
            label="Set the clock automatically (NTP)"
            color="primary"
            hide-details
            @update:model-value="setNtp"
          ></v-switch>
          <v-chip v-if="!status.ntp" size="small" label variant="outlined">Automatic time off</v-chip>
          <v-chip v-else-if="status.synchronized" size="small" label color="success">Synchronised</v-chip>
          <v-chip v-else size="small" label color="warning">Not synchronised</v-chip>
        </div>
        <div v-if="!status.can_ntp" class="text-caption text-medium-emphasis">
          Network time is not available on this system
        </div>
      </v-card-text>
      <v-card-text v-else>Loading…</v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import {computed, onUnmounted, ref, watch} from 'vue'
import {api} from '@/api'

// The server is the only source of the zone and of the NTP state: the controls show what the status says, and
// a change is shown only when the server broadcasts it again.
const status = api.Settings.DateTime.status.use({initial: null})

// Which control has a call in flight ('timezone' or 'ntp'), so that it is disabled until the answer comes
const pending = ref(null)
let pendingTimer = null

function setPending(control) {
  pending.value = control
  clearTimeout(pendingTimer)
  // A refused call sends an error instead of a new status: the control is enabled again after a while
  pendingTimer = setTimeout(() => { pending.value = null }, 5000)
}

watch(status, () => {
  clearTimeout(pendingTimer)
  pending.value = null
})

const setTimezone = (zone) => {
  if (!zone || zone === status.value?.timezone) return
  setPending('timezone')
  api.Settings.DateTime.set_timezone({timezone: zone})
}

const setNtp = (enabled) => {
  setPending('ntp')
  api.Settings.DateTime.set_ntp({enabled: Boolean(enabled)})
}

// The clock is drawn in the browser from the current instant and the zone of the server
const now = ref(new Date())
const ticker = setInterval(() => { now.value = new Date() }, 1000)

function formatZoned(zone, options) {
  try {
    return new Intl.DateTimeFormat('en-GB', {timeZone: zone, ...options}).format(now.value)
  } catch {
    return '--'  // the zone name is not known to this browser
  }
}

const clockTime = computed(() => {
  const zone = status.value?.timezone
  if (!zone) return ''
  return formatZoned(zone, {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'})
})

const clockDate = computed(() => {
  const zone = status.value?.timezone
  if (!zone) return ''
  return formatZoned(zone, {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'})
})

// UTC offset from the server (seconds east of UTC), as UTC+HH:MM
function formatOffset(seconds) {
  const sign = seconds < 0 ? '-' : '+'
  const abs = Math.abs(seconds)
  const hours = String(Math.floor(abs / 3600)).padStart(2, '0')
  const minutes = String(Math.floor((abs % 3600) / 60)).padStart(2, '0')
  return `UTC${sign}${hours}:${minutes}`
}

onUnmounted(() => {
  clearInterval(ticker)
  clearTimeout(pendingTimer)
})
</script>
