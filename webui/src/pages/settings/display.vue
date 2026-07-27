<template>
  <div class="d-flex justify-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <v-label class="d-block text-center">Click on a screen to change output device</v-label>
      <div class="row d-flex justify-center pa-4" id="screen_container">
        <svg :disabled="svg.disabled" :viewBox="svg.viewbox" height="200px" xmlns="http://www.w3.org/2000/svg">
          <template v-for="disp in displays">
            <rect
              :x="disp.x"
              :y="disp.y"
              :width="disp.width"
              :height="disp.height"
              :fill="screenBGColor"/>

            <rect
              :x="disp.x+disp.margin"
              :y="disp.y+disp.margin"
              :width="disp.width-2*disp.margin"
              :height="disp.height-2*disp.margin"
              :fill="screenFGColor"/>

            <text
              :x="disp.x+disp.width/2"
              :y="disp.y+disp.height*2/5">{{ disp.name }}
            </text>
            <text
              :x="disp.x+disp.width/2"
              :y="disp.y+disp.height*3/5">
              ({{ disp.width }}x{{ disp.height }})
            </text>
            <rect
              :x="disp.x"
              :y="disp.y"
              :width="disp.width"
              :height="disp.height"
              fill="#00000000"
              @click="sendCommand('Settings/Display/setBounds', {viewer_id: currentViewerId, window_id: 0, x: disp.x, y: disp.y, width: disp.width, height: disp.height})"/>
          </template>
          <text x="500" y="500" v-if="svg.disabled" :fill="textColor">Disconnected</text>
          <rect id="window_bound_rect"
                :x="window_bound_rect.x"
                :y="window_bound_rect.y"
                :width="window_bound_rect.width"
                :height="window_bound_rect.height"
                fill="#00ff0055"></rect>
        </svg>
      </div>
      <div class="d-flex justify-center align-center mb-4">
        <v-label class="text-center mr-2">Orientation:</v-label>
        <v-btn-toggle v-model="orientation" mandatory>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {viewer_id: currentViewerId, window_id: 0, orientation: 0})">
            <v-icon :style="{ transform: 'rotate(0deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {viewer_id: currentViewerId, window_id: 0, orientation: 1})">
            <v-icon :style="{ transform: 'rotate(90deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {viewer_id: currentViewerId, window_id: 0, orientation: 2})">
            <v-icon :style="{ transform: 'rotate(180deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {viewer_id: currentViewerId, window_id: 0, orientation: 3})">
            <v-icon :style="{ transform: 'rotate(270deg)' }">mdi-monitor</v-icon>
          </v-btn>
        </v-btn-toggle>
      </div>
      <div class="d-flex justify-center align-center mb-4">
        <v-label class="text-center mr-2">Flip:</v-label>
        <v-btn-toggle v-model="flip" mandatory>
          <v-btn @click="sendCommand('Settings/Display/setFlip', {viewer_id: currentViewerId, window_id: 0, flip: 0})">
            No
          </v-btn>

          <v-btn @click="sendCommand('Settings/Display/setFlip', {viewer_id: currentViewerId, window_id: 0, flip: 1})">
            <v-icon>mdi-reflect-horizontal</v-icon>
          </v-btn>

          <v-btn @click="sendCommand('Settings/Display/setFlip', {viewer_id: currentViewerId, window_id: 0, flip: 2})">
            <v-icon>mdi-reflect-vertical</v-icon>
          </v-btn>
        </v-btn-toggle>
      </div>
      <v-spacer style="height: 20px"></v-spacer>
      <div class="d-flex justify-center align-center mb-4">
        <div>
          <h2>Graphics Feature Status</h2>
          <ul class="ms-10">
            <li v-for="(value, key) in gpu" :key="key">
              {{ gpuFeatureLabels[key] || key }}:
              <span :class="(gpuStatusLabels[value] || {class: 'text-red'}).class">
                {{ (gpuStatusLabels[value] || {label: 'Unknown'}).label }}
              </span>
            </li>
          </ul>
        </div>
      </div>
    </v-card>
  </div>
</template>

<script setup>
import {computed, ref, watch} from 'vue'
import {useTheme} from 'vuetify'

const theme = useTheme()

const textColor = computed(() => theme.global.current.value.colors['on-background'])
const screenBGColor = computed(() => theme.global.current.value.colors['primary'])
const screenFGColor = computed(() => theme.global.current.value.colors['secondary'])

// Ported from Chromium's own content/browser/resources/gpu/info_view.ts
// (appendFeatureInfo_'s featureLabelMap/statusMap) so labels/colors match
// chrome://gpu exactly instead of being guessed at.
const gpuFeatureLabels = {
  '2d_canvas': 'Canvas',
  'gpu_compositing': 'Compositing',
  'webgl': 'WebGL',
  'multisampling': 'WebGL multisampling',
  'texture_sharing': 'Texture Sharing',
  'video_decode': 'Video Decode',
  'rasterization': 'Rasterization',
  'opengl': 'OpenGL',
  'metal': 'Metal',
  'vulkan': 'Vulkan',
  'multiple_raster_threads': 'Multiple Raster Threads',
  'native_gpu_memory_buffers': 'Native GpuMemoryBuffers',
  'protected_video_decode': 'Hardware Protected Video Decode',
  'surface_control': 'Surface Control',
  'vpx_decode': 'VPx Video Decode',
  'canvas_oop_rasterization': 'Canvas out-of-process rasterization',
  'raw_draw': 'Raw Draw',
  'video_encode': 'Video Encode',
  'direct_rendering_display_compositor': 'Direct Rendering Display Compositor',
  'webgpu': 'WebGPU',
  'skia_graphite': 'Skia Graphite',
  'webnn': 'WebNN',
  'trees_in_viz': 'TreesInViz',
  'webgpu_on_vk_via_gl_interop': 'WebGPU interop',
}
const gpuStatusLabels = {
  'disabled_software': {label: 'Software only. Hardware acceleration disabled', class: 'text-yellow'},
  'disabled_off': {label: 'Disabled', class: 'text-red'},
  'disabled_off_ok': {label: 'Disabled', class: 'text-yellow'},
  'unavailable_software': {label: 'Software only, hardware acceleration unavailable', class: 'text-yellow'},
  'unavailable_off': {label: 'Unavailable', class: 'text-red'},
  'unavailable_off_ok': {label: 'Unavailable', class: 'text-yellow'},
  'enabled_readback': {label: 'Hardware accelerated but at reduced performance', class: 'text-yellow'},
  'enabled_force': {label: 'Hardware accelerated on all pages', class: 'text-green'},
  'enabled': {label: 'Hardware accelerated', class: 'text-green'},
  'enabled_on': {label: 'Enabled', class: 'text-green'},
  'enabled_force_on': {label: 'Force enabled', class: 'text-green'},
}

const gpu = ref({})
const displays = ref([])
const orientation = ref(0)
const flip = ref(0)
const currentViewerId = ref(null)

const window_bound_rect = ref({})

const svg = ref({viewbox: "0 0 1000 1000", disabled: true})

watch(displays, (_displays) => {
    if (!_displays.length) {
      svg.value = {viewbox: '0 0 1000 1000', disabled: true}
    } else {
      let w = 0, h = 0;
      for (const disp of _displays) {
        disp.margin = Math.min(disp.width, disp.height) * 0.025;
        w = Math.max(w, disp.x + disp.width)
        h = Math.max(h, disp.y + disp.height)
      }
      svg.value = {viewbox: `0 0 ${w} ${h}`, disabled: false}
    }
  }
)

const sendCommand = window.sendCommand;
// getDisplays/getBounds/getOrientation/getFlip all require a viewer_id -
// resolve it first (defaulting to the first known viewer, usually the
// local one) before fetching anything that depends on it.
window.setInitCommands("Settings/Display/getGPUFeatureStats", "Viewers/list")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Display/getGPUFeatureStats":
      gpu.value = data.features;
      break;
    case "Viewers/list":
      if (!currentViewerId.value) {
        const ids = Object.keys(data.viewers || {});
        if (ids.length) {
          currentViewerId.value = ids.includes('local-webview') ? 'local-webview' : ids[0];
          sendCommand('Settings/Display/getDisplays', {viewer_id: currentViewerId.value});
          sendCommand('Settings/Display/getBounds', {viewer_id: currentViewerId.value, window_id: 0});
          sendCommand('Settings/Display/getOrientation', {viewer_id: currentViewerId.value, window_id: 0});
          sendCommand('Settings/Display/getFlip', {viewer_id: currentViewerId.value, window_id: 0});
        }
      }
      break;
    case "Settings/Display/getDisplays":
      displays.value = data.displays;
      break;
    case "Settings/Display/getBounds":
      window_bound_rect.value = data;
      break;
    case "Settings/Display/getOrientation":
      orientation.value = data.orientation;
      break;
    case "Settings/Display/getFlip":
      flip.value = data.flip;
      break;
  }
}
</script>

<style scoped>
svg rect {
  rx: 50px;
}

svg text {
  font-size: 150px;
  text-anchor: middle;
  dominant-baseline: middle;
}
</style>
