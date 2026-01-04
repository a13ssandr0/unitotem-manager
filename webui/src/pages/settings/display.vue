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
              :x="disp.bounds.x"
              :y="disp.bounds.y"
              :width="disp.bounds.width"
              :height="disp.bounds.height"
              :fill="screenBGColor"/>

            <rect v-if="disp.rotation === 0"
                  :x="disp.bounds.x+disp.margin"
                  :y="disp.bounds.y+disp.margin"
                  :width="disp.bounds.width-2*disp.margin"
                  :height="disp.bounds.height-5*disp.margin"
                  :fill="screenFGColor"/>

            <rect v-else-if="disp.rotation === 90"
                  :x="disp.bounds.x+disp.margin"
                  :y="disp.bounds.y+disp.margin"
                  :width="disp.bounds.width-5*disp.margin"
                  :height="disp.bounds.height-2*disp.margin"
                  :fill="screenFGColor"/>

            <rect v-else-if="disp.rotation === 180"
                  :x="disp.bounds.x+disp.margin"
                  :y="disp.bounds.y+4*disp.margin"
                  :width="disp.bounds.width-2*disp.margin"
                  :height="disp.bounds.height-5*disp.margin"
                  :fill="screenFGColor"/>

            <rect v-else
                  :x="disp.bounds.x+4*disp.margin"
                  :y="disp.bounds.y+disp.margin"
                  :width="disp.bounds.width-5*disp.margin"
                  :height="disp.bounds.height-2*disp.margin"
                  :fill="screenFGColor"/>

            <text
              :x="disp.bounds.x+disp.bounds.width/2"
              :y="disp.bounds.y+disp.bounds.height*2/5">{{ disp.label }}
            </text>
            <text
              :x="disp.bounds.x+disp.bounds.width/2"
              :y="disp.bounds.y+disp.bounds.height*3/5">
              ({{
                Math.round(disp.bounds.width * disp.scaleFactor)
              }}x{{ Math.round(disp.bounds.height * disp.scaleFactor) }})
            </text>
            <rect
              :x="disp.bounds.x"
              :y="disp.bounds.y"
              :width="disp.bounds.width"
              :height="disp.bounds.height"
              fill="#00000000"
              @click="sendCommand('Settings/Display/setBounds', disp.bounds)"/>
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
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {orientation: 0})">
            <v-icon :style="{ transform: 'rotate(0deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {orientation: 1})">
            <v-icon :style="{ transform: 'rotate(90deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {orientation: 2})">
            <v-icon :style="{ transform: 'rotate(180deg)' }">mdi-monitor</v-icon>
          </v-btn>
          <v-btn @click="sendCommand('Settings/Display/setOrientation', {orientation: 3})">
            <v-icon :style="{ transform: 'rotate(270deg)' }">mdi-monitor</v-icon>
          </v-btn>
        </v-btn-toggle>
      </div>
      <div class="d-flex justify-center align-center mb-4">
        <v-label class="text-center mr-2">Flip:</v-label>
        <v-btn-toggle v-model="flip" mandatory>
          <v-btn @click="sendCommand('Settings/Display/setFlip', {flip: 0})">
            No
          </v-btn>

          <v-btn @click="sendCommand('Settings/Display/setFlip', {flip: 1})">
            <v-icon>mdi-reflect-horizontal</v-icon>
          </v-btn>

          <v-btn @click="sendCommand('Settings/Display/setFlip', {flip: 2})">
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
              {{ key }}:
              <span v-if="value === 'enabled'" class="text-green">Hardware accelerated</span>
              <span v-else-if="value === 'enabled_on'" class="text-green">Enabled</span>
              <span v-else-if="value === 'disabled_off_ok'" class="text-yellow">Disabled</span>
              <span v-else-if="value === 'disabled_off'" class="text-red">Disabled</span>
              <span v-else-if="value === 'disabled_software'" class="text-yellow">Software only. Hardware acceleration disabled</span>
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

const gpu = ref({})
const displays = ref([])
const orientation = ref(0)
const flip = ref(0)

const window_bound_rect = ref({})

const svg = ref({viewbox: "0 0 1000 1000", disabled: true})

watch(displays, (_displays) => {
    if (!_displays.length) {
      svg.value = {viewbox: '0 0 1000 1000', disabled: true}
    } else {
      let w = 0, h = 0;
      for (const disp of _displays) {
        disp.margin = Math.min(disp.bounds.width, disp.bounds.height) * 0.025;
        w = Math.max(w, disp.bounds.x + disp.bounds.width)
        h = Math.max(h, disp.bounds.y + disp.bounds.height)
      }
      svg.value = {viewbox: `0 0 ${w} ${h}`, disabled: false}
    }
  }
)

const sendCommand = window.sendCommand;
window.setInitCommands(
  "Settings/Display/getGPUFeatureStats",
  "Settings/Display/getDisplays", "Settings/Display/getBounds",
  "Settings/Display/getOrientation", "Settings/Display/getFlip")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Display/getGPUFeatureStats":
      gpu.value = data.features;
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
