<template>
  <v-card class="ma-4" max-width="1000" width="100%" title="Touch screens">
    <v-card-text v-if="!available" class="text-medium-emphasis">
      This node has no X display to ask: touch screens cannot be listed or mapped here.
    </v-card-text>
    <template v-else>
      <v-card-text v-if="!devices.length" class="text-medium-emphasis">
        No touch screen is connected. When one is plugged in it appears here and can be assigned to a screen, so that
        touches land on the monitor it is glued to, also when that monitor is turned or mirrored: it follows by itself.
      </v-card-text>
      <v-list v-else lines="two">
        <v-list-item v-for="dev in devices" :key="dev.key">
          <template v-slot:prepend>
            <v-icon :color="dev.connected ? 'green' : 'grey'">
              {{ dev.connected ? 'mdi-gesture-tap' : 'mdi-power-plug-off' }}
            </v-icon>
          </template>
          <v-list-item-title>{{ dev.name }}</v-list-item-title>
          <v-list-item-subtitle>
            <span v-if="dev.vendor !== null && dev.product !== null">{{ hex(dev.vendor) }}:{{ hex(dev.product) }}</span>
            <span v-if="dev.serial"> serial {{ dev.serial }}</span>
            <span v-if="!dev.connected"> not connected</span>
            <span v-else-if="dev.output && !outputNames.includes(dev.output)"> mapped to {{ dev.output }}, which is not on</span>
          </v-list-item-subtitle>
          <template v-slot:append>
            <v-select :model-value="dev.output || ''" :items="choices(dev)" label="Screen" density="compact"
                      variant="outlined" hide-details style="min-width: 220px"
                      @update:model-value="v => api.Settings.Display.Touch.set_output({key: dev.key, output: v || null})"/>
            <v-select :model-value="dev.turn || 0" :items="turns" label="Panel turned" density="compact" variant="outlined" hide-details
                      class="ms-2" style="min-width: 150px" title="Only for a panel that is not glued to its screen and has another natural orientation"
                      @update:model-value="v => setOrientation(dev, v, dev.flip || 0)"/>
            <v-select :model-value="dev.flip || 0" :items="flips" label="Mirrored" density="compact" variant="outlined" hide-details
                      class="ms-2" style="min-width: 160px" @update:model-value="v => setOrientation(dev, dev.turn || 0, v)"/>
            <v-btn v-if="!dev.connected" icon="mdi-delete" variant="text" class="ms-2"
                   @click="api.Settings.Display.Touch.forget({key: dev.key})"/>
          </template>
        </v-list-item>
      </v-list>
    </template>
  </v-card>
</template>

<script setup>
import {computed, ref} from 'vue'
import {api} from '@/api'

const devices = ref([])
const outputs = ref([])
const available = ref(true)

const outputNames = computed(() => outputs.value.map(o => o.name))

const hex = (n) => n.toString(16).padStart(4, '0')

// A touch screen built into its monitor follows the screen's turn and mirror by itself ("Same as the screen"); these two are for a separate
// panel whose natural orientation differs from its screen's
const turns = [{title: 'Same as the screen', value: 0}, {title: '90° clockwise', value: 1}, {title: '180°', value: 2}, {title: '90° counter-clockwise', value: 3}]
const flips = [{title: 'No', value: 0}, {title: 'Left-right', value: 1}, {title: 'Top-bottom', value: 2}]
const setOrientation = (dev, turn, flip) => api.Settings.Display.Touch.set_orientation({key: dev.key, turn, flip})

function choices(dev) {
  const items = [{title: 'Automatic (whole desktop)', value: ''}]
  for (const o of outputs.value) {
    items.push({title: `${o.name} (${o.width}x${o.height}${o.rotation !== 'normal' ? ', ' + o.rotation : ''})`, value: o.name})
  }
  // A mapping to an output that is not on right now stays selectable, so it does not look as if it had been lost
  if (dev.output && !outputNames.value.includes(dev.output)) items.push({title: dev.output, value: dev.output})
  return items
}

// Pushed by the node whenever a touch screen or a monitor appears, goes away or rotates, not only at page load
api.Settings.Display.Touch.devices.on(data => {
  devices.value = data.devices
  outputs.value = data.outputs
  available.value = data.available
}, {init: true})
</script>
