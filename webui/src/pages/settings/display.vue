<template>
  <div class="d-flex justify-center flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <v-card-text v-if="!outputs.length" class="text-medium-emphasis">No screen.</v-card-text>
      <template v-else>
        <div class="d-flex flex-wrap align-center ga-3 px-4 pt-2 pb-4">
          <v-btn-toggle :model-value="selectedName" mandatory divided color="primary" variant="outlined"
                        @update:model-value="v => picked = v">
            <v-btn v-for="o in outputs" :key="o.name" :value="o.name">{{ o.name }}</v-btn>
          </v-btn-toggle>
          <v-spacer></v-spacer>
          <v-btn variant="tonal" prepend-icon="mdi-monitor-eye" :disabled="!output || !canIdentify"
                 @click="identify">Identify</v-btn>
        </div>

        <v-alert v-if="output?.pending" type="warning" variant="tonal" density="compact" class="mx-4 mb-4">
          <div class="d-flex align-center flex-wrap ga-3">
            <span>Keep the new mode? Reverts in {{ remaining }} s</span>
            <v-spacer></v-spacer>
            <v-btn color="primary" variant="flat" :disabled="!canConfirm" @click="keep">Keep</v-btn>
          </div>
        </v-alert>

        <div class="d-flex flex-wrap align-center ga-4 px-4 pb-4">
          <v-select :model-value="currentRes" :items="resolutionItems" label="Resolution" density="compact"
                    variant="outlined" hide-details style="max-width: 200px" :disabled="modeLocked"
                    @update:model-value="setResolution"></v-select>
          <v-select :model-value="output?.current?.refresh_mhz ?? null" :items="refreshItems" label="Refresh rate"
                    density="compact" variant="outlined" hide-details style="max-width: 200px" :disabled="modeLocked"
                    @update:model-value="setRefresh"></v-select>
          <v-spacer></v-spacer>
          <v-btn variant="text" color="primary" :disabled="!output || !canReset" @click="reset">Reset</v-btn>
        </div>

        <div class="d-flex flex-wrap align-center ga-6 px-4 pb-4">
          <div class="d-flex align-center ga-3">
            <span class="text-body-2 text-medium-emphasis">Orientation</span>
            <v-btn-toggle :model-value="turn" mandatory divided variant="outlined" density="comfortable"
                          @update:model-value="setTurn">
              <v-btn v-for="t in turns" :key="t.value" :value="t.value" :title="t.title"
                     :disabled="!win || !canTurn">
                <v-icon :style="{transform: `rotate(${t.value * 90}deg)`}">mdi-monitor</v-icon>
              </v-btn>
            </v-btn-toggle>
          </div>
          <div class="d-flex align-center ga-3">
            <span class="text-body-2 text-medium-emphasis">Flip</span>
            <v-btn-toggle :model-value="flipMode" mandatory divided variant="outlined" density="comfortable"
                          @update:model-value="setFlip">
              <v-btn :value="0" title="No flip" :disabled="!win || !canFlip">No</v-btn>
              <v-btn :value="1" title="Left-right" :disabled="!win || !canFlip">
                <v-icon>mdi-reflect-horizontal</v-icon>
              </v-btn>
              <v-btn :value="2" title="Top-bottom" :disabled="!win || !canFlip">
                <v-icon>mdi-reflect-vertical</v-icon>
              </v-btn>
            </v-btn-toggle>
          </div>
        </div>
      </template>
    </v-card>

    <template v-if="selectedName">
      <ScreenControl :screen="selectedName" />
      <TouchMapping :output="selectedName" />
    </template>
  </div>
</template>

<script setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {api, canCall} from '@/api'
import ScreenControl from '@/components/ScreenControl.vue'
import TouchMapping from '@/components/TouchMapping.vue'

// The viewer of this node: its windows sit on this node's outputs, the only ones turned, mirrored or set here
const LOCAL_VIEWER = 'local-webview'

const user = api.Settings.Security.current_user.use()
const canSetMode = computed(() => canCall('Settings/Display/Outputs/set_mode', user.value))
const canConfirm = computed(() => canCall('Settings/Display/Outputs/confirm_mode', user.value))
const canReset = computed(() => canCall('Settings/Display/Outputs/reset_output', user.value))
const canIdentify = computed(() => canCall('Settings/Display/Screens/identify', user.value))
const canTurn = computed(() => canCall('Settings/Display/set_orientation', user.value))
const canFlip = computed(() => canCall('Settings/Display/set_flip', user.value))

// Outputs of this node (Settings/Display/Outputs/outputs), pushed on every change
const outputs = ref([])
// The output picked on the page; falls back to the first one when it is gone
const picked = ref(null)
const selectedName = computed(() => outputs.value.some(o => o.name === picked.value) ? picked.value : (outputs.value[0]?.name ?? null))
const output = computed(() => outputs.value.find(o => o.name === selectedName.value) ?? null)

// Windows of the local viewer (Viewers/list): the window of an output is the one whose screen_name is its name
const windows = ref([])
const win = computed(() => windows.value.find(w => w.screen_name === selectedName.value) ?? null)
const turn = ref(0)        // quarter turns of the selected window
const flipMode = ref(0)    // 0 none, 1 left-right, 2 top-bottom

const turns = [
  {value: 0, title: '0°'},
  {value: 1, title: '90° clockwise'},
  {value: 2, title: '180°'},
  {value: 3, title: '90° counter-clockwise'},
]

const resolutionOf = m => `${m.width}x${m.height}`
const formatHz = mhz => `${Number((mhz / 1000).toFixed(2))} Hz`
const currentRes = computed(() => output.value?.current ? resolutionOf(output.value.current) : null)
// Locked when the output is off or has no mode, or when the user may not change modes: disabled, not hidden
const modeLocked = computed(() => !canSetMode.value || !output.value?.enabled || !output.value?.current)

const resolutionItems = computed(() => {
  const seen = new Map()
  for (const m of output.value?.modes ?? []) {
    if (!seen.has(resolutionOf(m))) seen.set(resolutionOf(m), {title: resolutionOf(m), value: resolutionOf(m), area: m.width * m.height})
  }
  return [...seen.values()].sort((a, b) => b.area - a.area)
})

const refreshItems = computed(() => {
  const rates = new Set()
  for (const m of output.value?.modes ?? []) {
    if (resolutionOf(m) === currentRes.value) rates.add(m.refresh_mhz)
  }
  return [...rates].sort((a, b) => b - a).map(mhz => ({title: formatHz(mhz), value: mhz}))
})

function setMode(mode) {
  api.Settings.Display.Outputs.set_mode({name: output.value.name, width: mode.width, height: mode.height, refresh_mhz: mode.refresh_mhz})
}

// A new resolution comes with its preferred refresh rate, or the highest one the output lists for it
function setResolution(value) {
  if (!value || value === currentRes.value) return
  const modes = output.value.modes.filter(m => resolutionOf(m) === value)
  if (!modes.length) return
  setMode(modes.find(m => m.preferred) ?? modes.reduce((a, b) => (b.refresh_mhz > a.refresh_mhz ? b : a)))
}

function setRefresh(mhz) {
  const cur = output.value?.current
  if (!cur || mhz === cur.refresh_mhz) return
  setMode({width: cur.width, height: cur.height, refresh_mhz: mhz})
}

// The server reverts a new mode after 15 s unless it is confirmed: the countdown follows revert_at (Unix seconds)
const now = ref(Date.now() / 1000)
let clock = null
onMounted(() => { clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000) })
onBeforeUnmount(() => clearInterval(clock))
const remaining = computed(() => Math.max(0, Math.ceil((output.value?.pending?.revert_at ?? 0) - now.value)))

function keep() {
  api.Settings.Display.Outputs.confirm_mode({name: output.value.name})
}

function reset() {
  api.Settings.Display.Outputs.reset_output({name: output.value.name})
}

function identify() {
  api.Settings.Display.Screens.identify({name: selectedName.value})
}

// Turn and mirror of the selected window: the replies carry the new value, the getters re-read it after a change
function readTurn() {
  if (!win.value) return
  const args = {viewer_id: LOCAL_VIEWER, window_id: win.value.window_id}
  api.Settings.Display.orientation(args)
  api.Settings.Display.flip(args)
}

function syncTurn() {
  turn.value = win.value?.orientation ?? 0
  flipMode.value = win.value?.flip ?? 0
  readTurn()
}

function setTurn(value) {
  if (!win.value || value === null) return
  api.Settings.Display.set_orientation({viewer_id: LOCAL_VIEWER, window_id: win.value.window_id, orientation: value})
}

function setFlip(value) {
  if (!win.value || value === null) return
  api.Settings.Display.set_flip({viewer_id: LOCAL_VIEWER, window_id: win.value.window_id, flip: value})
}

watch(selectedName, syncTurn)

api.Settings.Display.Outputs.outputs.on(data => {
  outputs.value = data.outputs
  readTurn()
}, {init: true})

api.Viewers.list.on(data => {
  windows.value = data.viewers?.[LOCAL_VIEWER]?.windows ?? []
  syncTurn()
}, {init: true})

api.Settings.Display.orientation.on(data => {
  turn.value = data.orientation
})

api.Settings.Display.flip.on(data => {
  flipMode.value = data.flip
})
</script>
