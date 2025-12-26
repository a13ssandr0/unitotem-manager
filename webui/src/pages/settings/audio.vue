<template>
  <div class="d-flex justify-center">
    <v-card
      class="ma-4"
      max-width="600"
      width="100%"
      :title="String($route.name)"
    >
      <v-list>
        <v-radio-group v-model="audioDevices.default" @update:model-value="setDefaultDevice">
          <v-list-item
            v-for="device in audioDevices.devices"
            :key="device.name"
          >
            <div>
              <v-radio :label="device.description" :value="device.name" color="primary"/>
              <div
                class="d-flex align-center mt-2"
                style="padding-left: 32px"
              >
                <v-btn
                  :icon="device.muted ? 'mdi-volume-off' : 'mdi-volume-high'"
                  :color="device.muted ? 'red' : 'primary'"
                  variant="text"
                  @click="toggleMute(device)"
                />
                <v-slider
                  v-model="device.volume"
                  color="primary"
                  :disabled="device.muted"
                  class="ml-4 mr-8"
                  max="1"
                  min="0"
                  step="0.01"
                  style="min-width: 150px"
                  hide-details
                  @end="setVolume(device)"
                />
                <span
                  class="font-weight-medium me-4"
                  style="width: 3ch; text-align: right;"
                >{{ Math.round(device.volume*100) }}</span>
              </div>
            </div>
          </v-list-item>
        </v-radio-group>
      </v-list>
    </v-card>
  </div>
</template>

<script setup>
import {ref} from 'vue';

const audioDevices = ref({
  default: null,
  devices: []
});

const sendCommand = window.sendCommand;

const setDefaultDevice = (deviceName) => {
  sendCommand("Settings/Audio/default", {device: deviceName})
}

const setVolume = (device) => {
  sendCommand("Settings/Audio/volume", {device: device.name, volume: device.volume})
}

const toggleMute = (device) => {
  device.muted = !device.muted
  sendCommand("Settings/Audio/mute", {device: device.name, mute: device.muted})
}

onWSOpen = (e) => {
  sendCommand("Settings/Audio/devices")
}

if (isWSReady()) onWSOpen()

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Audio/devices":
      audioDevices.value = data;
      break;
  }
}

</script>


<style scoped>

</style>
