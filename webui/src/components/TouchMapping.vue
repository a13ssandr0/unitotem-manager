<template>
  <v-card class="ma-4" max-width="1000" width="100%" title="Touch screens">
    <v-card-text v-if="!available" class="text-medium-emphasis">
      This node has no display to ask: touch screens cannot be listed or mapped here.
    </v-card-text>
    <template v-else>
      <v-card-text v-if="!visible.length" class="text-medium-emphasis">
        No touch screen.
      </v-card-text>
      <v-list v-else lines="two">
        <v-list-item v-for="dev in visible" :key="dev.key">
          <template v-slot:prepend>
            <v-icon :color="dev.connected ? 'green' : 'grey'">
              {{ dev.connected ? 'mdi-gesture-tap' : 'mdi-power-plug-off' }}
            </v-icon>
          </template>
          <v-list-item-title>{{ dev.name }}</v-list-item-title>
          <v-list-item-subtitle>
            <span v-if="dev.vendor !== null && dev.product !== null">{{ hex(dev.vendor) }}:{{ hex(dev.product) }}</span>
            <span v-if="dev.serial"> serial {{ dev.serial }}</span>
            <span v-if="dev.output && !outputNames.includes(dev.output)"> mapped to {{ dev.output }}, which is not on</span>
          </v-list-item-subtitle>
          <template v-slot:append>
            <v-chip :color="status(dev).color" size="small" variant="tonal" class="me-2">{{ status(dev).text }}</v-chip>
            <v-select :model-value="dev.output || ''" :items="choices(dev)" label="Screen" density="compact"
                      variant="outlined" hide-details style="min-width: 220px" class="me-2"
                      @update:model-value="v => api.Settings.Display.Touch.set_output({key: dev.key, output: v || null})"/>
            <v-btn v-if="dev.connected" variant="text" size="small" @click="open(dev)">Touch doesn't match? Fix it</v-btn>
            <v-btn v-else icon="mdi-delete" variant="text" @click="api.Settings.Display.Touch.forget({key: dev.key})"/>
          </template>
        </v-list-item>
      </v-list>
    </template>

    <v-dialog :model-value="!!fixing" max-width="560" :fullscreen="$vuetify.display.xs" persistent>
      <v-card v-if="fixing" :title="`Fix the touch of ${fixing.name}`">
        <!-- choosing what to do -->
        <template v-if="!active">
          <v-card-text>
            <p class="text-medium-emphasis mb-4">The screen of the totem shows where to touch. Nothing changes until you say it works.</p>
            <v-card v-for="m in methods" :key="m.value" :variant="choice === m.value ? 'tonal' : 'outlined'"
                    :color="choice === m.value ? 'primary' : undefined" class="mb-3 pa-3" @click="choice = m.value">
              <div class="d-flex align-center ga-3">
                <v-icon size="large">{{ m.icon }}</v-icon>
                <div>
                  <div class="font-weight-medium">{{ m.title }} <v-chip v-if="m.recommended" size="x-small" color="primary" class="ms-1">recommended</v-chip></div>
                  <div class="text-body-2 text-medium-emphasis">{{ m.text }}</div>
                </div>
              </div>
            </v-card>
            <v-expansion-panels variant="accordion" class="mt-2">
              <v-expansion-panel title="Advanced: turn or mirror the panel by hand">
                <v-expansion-panel-text>
                  <p class="text-body-2 text-medium-emphasis mb-3">Only for a panel that is not glued to its screen and has another natural orientation.</p>
                  <div class="d-flex ga-3 flex-wrap">
                    <v-select :model-value="fixing.turn || 0" :items="turns" label="Panel turned" density="compact" variant="outlined" hide-details
                              style="min-width: 190px" @update:model-value="v => setOrientation(fixing, v, fixing.flip || 0)"/>
                    <v-select :model-value="fixing.flip || 0" :items="flips" label="Mirrored" density="compact" variant="outlined" hide-details
                              style="min-width: 160px" @update:model-value="v => setOrientation(fixing, fixing.turn || 0, v)"/>
                  </div>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </v-card-text>
          <v-card-actions>
            <v-spacer/>
            <v-btn @click="close">Close</v-btn>
            <v-btn v-if="choice === 'reset'" color="primary" variant="flat" @click="reset">Back to automatic</v-btn>
            <v-btn v-else color="primary" variant="flat" @click="start">Start</v-btn>
          </v-card-actions>
        </template>

        <!-- touching the targets -->
        <template v-else-if="setup.phase === 'running'">
          <v-card-text class="text-center">
            <div class="text-h6 mb-1">Step {{ setup.step + 1 }} of {{ setup.total }}</div>
            <div class="text-medium-emphasis mb-4">{{ instruction }}</div>
            <v-progress-linear :model-value="(setup.step / setup.total) * 100" height="8" rounded color="primary"/>
          </v-card-text>
          <v-card-actions><v-spacer/><v-btn @click="cancel">Cancel</v-btn></v-card-actions>
        </template>

        <!-- trying the result -->
        <template v-else-if="setup.phase === 'try'">
          <v-card-text class="text-center">
            <v-icon size="48" color="primary" class="mb-2">mdi-gesture-tap</v-icon>
            <div class="text-h6 mb-1">Does the dot follow your finger?</div>
            <div class="text-medium-emphasis">Touch the screen of the totem and look at the dot.</div>
          </v-card-text>
          <v-card-actions>
            <v-spacer/>
            <v-btn @click="again">No, try again</v-btn>
            <v-btn color="primary" variant="flat" @click="api.Settings.Display.Touch.confirm_setup()">Yes, keep it</v-btn>
          </v-card-actions>
        </template>

        <!-- it did not work -->
        <template v-else-if="setup.phase === 'error'">
          <v-card-text>
            <v-alert type="warning" variant="tonal">{{ setup.message }}</v-alert>
          </v-card-text>
          <v-card-actions>
            <v-spacer/>
            <v-btn @click="cancel">Close</v-btn>
            <v-btn color="primary" variant="flat" @click="again">Try again</v-btn>
          </v-card-actions>
        </template>
      </v-card>
    </v-dialog>
    <v-snackbar v-model="saved" color="success" timeout="3000">Saved. The touch is adjusted.</v-snackbar>
  </v-card>
</template>

<script setup>
import {computed, ref, watch} from 'vue'
import {api} from '@/api'

// output: the screen this section belongs to. A touch screen mapped to it is listed, and so is one on automatic (whole desktop)
const props = defineProps({output: {type: String, default: null}})
const devices = ref([])
const visible = computed(() => devices.value.filter(d => !d.output || d.output === props.output))
const outputs = ref([])
const available = ref(true)
const setup = ref({phase: 'idle'})      // the guided setup running now (pushed by the node, so every open page shows the same step)
const fixing = ref(null)                // the device whose dialog is open
const choice = ref('corners')
const saved = ref(false)

const outputNames = computed(() => outputs.value.map(o => o.name))
const active = computed(() => ['running', 'try', 'error'].includes(setup.value.phase))

const hex = (n) => n.toString(16).padStart(4, '0')

const methods = [
  {value: 'corners', icon: 'mdi-gesture-tap', title: 'Quick setup', recommended: true,
   text: 'Touch the four corners of the screen as they light up.'},
  {value: 'points', icon: 'mdi-crosshairs-gps', title: 'Precise calibration',
   text: 'For touch frames and whiteboards: five points, one after the other.'},
  {value: 'reset', icon: 'mdi-undo', title: 'Back to automatic',
   text: "The touch follows the screen's turn and mirror by itself."},
]
// A touch screen built into its monitor follows the screen's turn and mirror by itself ("Same as the screen"); these two are for a separate
// panel whose natural orientation differs from its screen's
const turns = [{title: 'Same as the screen', value: 0}, {title: '90° clockwise', value: 1}, {title: '180°', value: 2}, {title: '90° counter-clockwise', value: 3}]
const flips = [{title: 'No', value: 0}, {title: 'Left-right', value: 1}, {title: 'Top-bottom', value: 2}]
const setOrientation = (dev, turn, flip) => api.Settings.Display.Touch.set_orientation({key: dev.key, turn, flip})

const CORNERS = ['top-left', 'top-right', 'bottom-right', 'bottom-left']
const instruction = computed(() => {
  const s = setup.value
  if (s.method === 'points') return s.step === 4 ? 'The circle in the centre of the screen.' : `The circle in the ${CORNERS[s.step]}.`
  return `The ${CORNERS[s.step]} quarter of the screen.`
})

function status(dev) {
  if (!dev.connected) return {text: 'Not connected', color: 'grey'}
  if (dev.calibrated) return {text: 'Calibrated', color: 'primary'}
  if (dev.turn || dev.flip) return {text: 'Adjusted', color: 'primary'}
  return {text: 'Follows the screen', color: 'success'}
}

function choices(dev) {
  const items = [{title: 'Automatic (whole desktop)', value: ''}]
  for (const o of outputs.value) {
    items.push({title: `${o.name} (${o.width}x${o.height}${o.rotation !== 'normal' ? ', ' + o.rotation : ''})`, value: o.name})
  }
  // A mapping to an output that is not on right now stays selectable, so it does not look as if it had been lost
  if (dev.output && !outputNames.value.includes(dev.output)) items.push({title: dev.output, value: dev.output})
  return items
}

function open(dev) { fixing.value = dev; choice.value = 'corners' }
function close() { fixing.value = null }
const start = () => api.Settings.Display.Touch.start_setup({key: fixing.value.key, method: choice.value})
const cancel = () => { api.Settings.Display.Touch.cancel_setup(); close() }
const again = () => api.Settings.Display.Touch.start_setup({key: setup.value.key, method: setup.value.method})
const reset = () => { api.Settings.Display.Touch.reset({key: fixing.value.key}); close() }

// The dialog follows the setup, whoever started it: another browser opening it opens it here too, and the end closes it
watch(setup, (state, before) => {
  if (['running', 'try', 'error'].includes(state.phase) && !fixing.value) {
    fixing.value = devices.value.find(d => d.key === state.key) || null
  }
  if (state.phase === 'idle' && before && ['running', 'try', 'error'].includes(before.phase)) close()
  if (state.phase === 'saved') { close(); saved.value = true }
})
// The device being fixed keeps showing its current turn and mirror while the advanced selects change them
watch(devices, list => { if (fixing.value) fixing.value = list.find(d => d.key === fixing.value.key) || fixing.value })

// Pushed by the node whenever a touch screen or a monitor appears, goes away or rotates, not only at page load
api.Settings.Display.Touch.devices.on(data => {
  devices.value = data.devices
  outputs.value = data.outputs
  available.value = data.available
}, {init: true})
api.Settings.Display.Touch.setup.on(state => { setup.value = state }, {init: true})
</script>
