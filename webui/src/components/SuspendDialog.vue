<template>
  <v-dialog :model-value="modelValue" max-width="460" @update:model-value="emit('update:modelValue', $event)">
    <v-card class="pa-2 pb-0 rounded-lg">
      <v-card-title class="text-h5">Suspend now</v-card-title>
      <v-card-text>
        <v-alert v-if="support && !support.available" type="warning" variant="tonal" class="mb-4"
                 title="This node cannot suspend and wake itself up">{{ support.reason }}</v-alert>
        <p class="mb-4">
          The node goes to sleep right away and wakes up by itself at the time below. It does not answer
          (nor show anything) until then.
        </p>
        <!-- The date is read as the node's own local time, whatever the time zone of this browser -->
        <v-text-field v-model="wakeAt" type="datetime-local" label="Wake up at (node local time)" variant="outlined"
                      :hint="`Between ${minSleep} seconds and ${maxDays} days from now`" persistent-hint
                      :disabled="!support?.available"></v-text-field>
        <v-alert v-if="support?.last_refusal" type="info" variant="text" density="compact" class="mt-2">
          Last refusal: {{ support.last_refusal.reason }}
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn text @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn color="red darken-1" text :disabled="!support?.available || !wakeAt" @click="suspend">Suspend</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import {computed, ref, watch} from 'vue'
import {api} from '@/api'

const props = defineProps({modelValue: Boolean})
const emit = defineEmits(['update:modelValue'])

const support = api.Power.suspend_support.use({init: false})
const minSleep = computed(() => support.value?.min_sleep ?? 120)
const maxDays = computed(() => Math.floor((support.value?.max_sleep ?? 604800) / 86400))

const pad = (n) => String(n).padStart(2, '0')
// A starting point: 07:00 tomorrow (shown as typed, never converted: the server decides what it means)
function tomorrowMorning() {
  const d = new Date(Date.now() + 24 * 3600 * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T07:00`
}
const wakeAt = ref(tomorrowMorning())

watch(() => props.modelValue, open => {
  if (open) api.Power.suspend_support()      // asked every time: the answer depends on the machine's state
})

function suspend() {
  emit('update:modelValue', false)
  api.Power.suspend({wake_at: wakeAt.value})
}
</script>
