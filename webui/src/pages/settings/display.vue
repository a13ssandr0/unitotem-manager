<template>
  <div class="d-flex justify-center">
    <v-card
      class="ma-4"
      max-width="600"
      width="100%"
      title="Display"
    >
      <v-label class="d-block text-center">Click on a screen to change output device</v-label>
      <!--      <div class="row d-flex justify-content-center" id="screen_container">-->
      <!--        <svg viewBox="0 0 {{svg.w}} {{svg.h}}" height="200px" xmlns="http://www.w3.org/2000/svg" {{svg.dis}}>-->
      <!--          {{ svg.content|join('\n    ')|safe }}-->
      <!--          <rect id="window_bound_rect" fill="#00ff0055" height="0" width="0"></rect>-->
      <!--        </svg>-->
      <!--      </div>-->
      <v-spacer style="height: 200px"></v-spacer>
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
import {ref} from 'vue'

const orientation = ref(0)
const flip = ref(0)

const orientationButtons = ref([
  { rotation: 0 },
  { rotation: 90 },
  { rotation: 180 },
  { rotation: 270 },
])
</script>

<style scoped>
</style>
