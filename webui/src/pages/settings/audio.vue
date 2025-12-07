<script setup lang="ts">
import {ref} from 'vue';

const audioDevices = ref({
  default: 1,
  devices:
    [
      {id: 1, name: 'Altoparlante esterno 1', volume: 70, muted: false},
      {id: 2, name: 'Altoparlante esterno 2', volume: 50, muted: true},
      {id: 3, name: 'Altoparlante interno', volume: 100, muted: false},
    ]
});

</script>

<template>
  <div class="d-flex justify-center">
    <v-card
      class="ma-4"
      max-width="600"
      width="100%"
      :title="String($route.name)"
    >
      <v-list>
        <v-radio-group v-model="audioDevices.default">
          <v-list-item
            v-for="device in audioDevices.devices"
            :key="device.id"
          >
            <div>
              <v-radio :label="device.name" :value="device.id" color="primary"/>
              <div
                class="d-flex align-center mt-2"
                style="padding-left: 32px"
              >
                <v-btn
                  :icon="device.muted ? 'mdi-volume-off' : 'mdi-volume-high'"
                  :color="device.muted ? 'red' : 'primary'"
                  variant="text"
                  @click="device.muted = !device.muted"
                />
                <v-slider
                  v-model="device.volume"
                  color="primary"
                  :disabled="device.muted"
                  class="ml-4 mr-8"
                  max="100"
                  min="0"
                  step="1"
                  style="min-width: 150px"
                  hide-details
                />
                <span
                  class="font-weight-medium me-4"
                  style="width: 3ch; text-align: right;"
                >{{ device.volume }}</span>
              </div>
            </div>
          </v-list-item>
        </v-radio-group>
      </v-list>
    </v-card>
  </div>
</template>

<style scoped>

</style>
