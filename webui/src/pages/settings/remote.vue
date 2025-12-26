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
      </v-card-text>
      <v-card-actions v-if="mode === 'Client'">
        <v-spacer></v-spacer>
        <v-btn color="primary">Connect</v-btn>
      </v-card-actions>
    </v-card>

    <div v-if="mode === 'Server'" class="w-100" style="max-width: 1000px;">
      <div v-if="clients.length === 0" class="text-grey text-subtitle-1">
        Clients will appear here once connected
      </div>
      <div v-else class="client-grid">
        <v-card
          v-for="client in clients"
          :key="client.hostname"
        >
          <v-card-title>
            <span class="text-truncate">{{ client.hostname }}</span>
          </v-card-title>
          <v-card-subtitle class="d-flex justify-space-between align-center">
            {{ client.ip }}:{{ client.port }}
            <v-switch v-model="client.enabled" color="primary" hide-details></v-switch>
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
import { ref } from 'vue'

const mode = ref('Server')
const items = ref([
  'Server',
  'Client'
])
const serverIp = ref('')
const serverPort = ref('')

const clients = ref([
  { hostname: 'Client-1-long-hostname-that-should-be-truncated', ip: '192.168.1.10', port: 8080, enabled: true },
  { hostname: 'Client-2', ip: '192.168.1.11', port: 8080, enabled: false },
  { hostname: 'Client-3', ip: '192.168.1.12', port: 8080, enabled: true },
  { hostname: 'Client-4', ip: '192.168.1.13', port: 8080, enabled: true },
  { hostname: 'Client-5', ip: '192.168.1.14', port: 8080, enabled: false },
])

function manageClient(ip, port) {
  window.open(`https://${ip}:${port}/settings`, '_blank')
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
