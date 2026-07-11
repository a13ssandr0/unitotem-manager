<template>
  <div class="d-flex flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <v-card-text>
        <v-select
          v-model="mode"
          :items="items"
          label="Mode"
          variant="outlined"
          @change="setMode"
        ></v-select>

        <v-row v-if="mode === 'Client'">
          <v-col cols="8">
            <v-text-field
              v-model="serverIp"
              label="Server IP"
              variant="outlined"
            ></v-text-field>
          </v-col>
          <v-col cols="4">
            <v-text-field
              v-model="serverPort"
              label="Server port"
              variant="outlined"
            ></v-text-field>
          </v-col>
        </v-row>

        <div class="d-flex align-center">
          <v-icon
            :icon="keyStatusIcon"
            :color="keyStatusColor"
            :class="{'mdi-spin': keyStatus === 'generating'}"
            class="mr-2"
          ></v-icon>
          <span class="text-subtitle-2">{{ keyStatusText }}</span>
        </div>
      </v-card-text>
      <v-card-actions v-if="mode === 'Client'">
        <v-spacer></v-spacer>
        <v-btn color="primary" @click="connectToServer">Connect</v-btn>
      </v-card-actions>
    </v-card>

    <div v-if="mode === 'Server'" class="w-100" style="max-width: 1000px;">
      <div v-if="clients.length === 0" class="text-grey text-subtitle-1">
        Clients will appear here once connected
      </div>
      <div v-else class="client-grid">
        <v-card v-for="client in clients" :key="client.hostname">
          <v-card-title>
            <span class="text-truncate">{{ client.hostname }}</span>
          </v-card-title>
          <v-card-subtitle class="d-flex justify-space-between align-center">
            {{ client.ip }}:{{ client.port }}
<!--            <v-switch v-model="client.enabled" color="primary" hide-details></v-switch>-->
          </v-card-subtitle>
          <v-card-actions>
            <v-btn color="red">Disconnect</v-btn>
            <v-spacer></v-spacer>
            <v-btn color="primary" @click="manageClient(client.ip, client.port)">Manage</v-btn>
          </v-card-actions>
        </v-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const mode = ref('Server')
const items = ref(['Server', 'Client'])
const serverIp = ref('')
const serverPort = ref('')

const clients = ref([])

// RSA signing key: generated in background by the manager right after startup
const keyStatus = ref(null)
const keyStatusIcon = computed(() => ({
  generating: 'mdi-loading',
  ready: 'mdi-check-circle',
  missing: 'mdi-key-alert-outline',
}[keyStatus.value] || 'mdi-help-circle-outline'))
const keyStatusColor = computed(() => ({
  generating: 'primary',
  ready: 'green',
  missing: 'orange',
}[keyStatus.value] || 'grey'))
const keyStatusText = computed(() => ({
  generating: 'Generating RSA signing key… (this can take a few minutes)',
  ready: 'RSA signing key ready',
  missing: 'RSA signing key not generated yet',
}[keyStatus.value] || 'Checking RSA signing key…'))

function manageClient(ip, port) {
  window.open(`https://${ip}:${port}/settings`, '_blank')
}

function setMode(){
  if (mode.value === 'Server') {
    serverIp.value = ''
    serverPort.value = ''
    sendCommand('Settings/Remote/setMode', {remote_server:null});
  }
}

function connectToServer(){
  sendCommand('Settings/Remote/setMode', {
    remote_server: serverIp.value,
    remote_port: serverPort.value,
  })
}

const sendCommand = window.sendCommand;
window.setInitCommands("Settings/Remote/getMode", "Settings/Remote/getKeyStatus")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Remote/getMode":
      mode.value = data.remote_server ? 'Client' : 'Server';
      serverIp.value = data.remote_server || '';
      serverPort.value = data.remote_port || '';
      clients.value = data.remote_clients;
      break;
    case "Settings/Remote/getKeyStatus":
      keyStatus.value = data.status;
      break;
  }
}

</script>

<style scoped>
.client-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
}

.client-grid :deep(.v-selection-control) {
  min-height: 0 !important;
}
</style>
