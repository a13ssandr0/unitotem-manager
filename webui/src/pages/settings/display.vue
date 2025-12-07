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
        <svg :viewBox="`0 0 ${svg.w} ${svg.h}`" height="200px" xmlns="http://www.w3.org/2000/svg"
             :disabled="svg.disabled">
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
              name="screen" fill="#00000000"
              @click="window_bound_rect = disp.bounds"/>
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
      <!--      <v-spacer style="height: 200px"></v-spacer>-->
      <div class="d-flex justify-center align-center mb-4">
        <v-label class="text-center mr-2">Orientation:</v-label>
        <v-btn-toggle v-model="orientation" mandatory>
          <v-btn v-for="(btn, index) in orientationButtons" :key="index">
            <v-icon :style="{ transform: `rotate(${btn.rotation}deg)` }">mdi-monitor</v-icon>
          </v-btn>
        </v-btn-toggle>
      </div>
      <div class="d-flex justify-center align-center mb-4">
        <v-label class="text-center mr-2">Flip:</v-label>
        <v-btn-toggle v-model="flip" mandatory>
          <v-btn>
            No
          </v-btn>

          <v-btn>
            <v-icon>mdi-reflect-horizontal</v-icon>
          </v-btn>

          <v-btn>
            <v-icon>mdi-reflect-vertical</v-icon>
          </v-btn>
        </v-btn-toggle>
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import {computed, ref} from 'vue'
import {useTheme} from 'vuetify'

const theme = useTheme()

const textColor = computed(() => theme.global.current.value.colors['on-background'])
const screenBGColor = computed(() => theme.global.current.value.colors['primary'])
const screenFGColor = computed(() => theme.global.current.value.colors['secondary'])

const displays = ref([
  {
    bounds: {x: 0, y: 0, width: 1080, height: 1920},
    label: 'Screen1',
    margin: 0, rotation: 90, scaleFactor: 1,
  },
  {
    bounds: {x: 1080, y: 0, width: 3840, height: 2160},
    label: 'Screen2',
    margin: 0, rotation: 0, scaleFactor: 1,
  },
  {
    bounds: {x: 1080 + 3840, y: 0, width: 3840, height: 2160},
    label: 'Screen3',
    margin: 0, rotation: 180, scaleFactor: 1,
  },
])

const window_bound_rect = ref(displays.value[1].bounds)

const svg = ref({
  content: [],
  w: displays.value.length ? 0 : 1000,
  h: displays.value.length ? 0 : 1000,
  disabled: !displays.value.length,
})

for (const disp of displays.value) {
  disp.margin = Math.min(disp.bounds.width, disp.bounds.height) * 0.025;
  svg.value.w = Math.max(svg.value.w, disp.bounds.x + disp.bounds.width)
  svg.value.h = Math.max(svg.value.h, disp.bounds.y + disp.bounds.height)
}


const orientation = ref(0)
const flip = ref(0)

const orientationButtons = ref([
  {rotation: 0},
  {rotation: 90},
  {rotation: 180},
  {rotation: 270},
])
</script>

<style scoped>
rect {
  rx: 50;
}

svg text {
  font-size: 150px;
  text-anchor: middle;
  dominant-baseline: middle;
}
</style>
