<template>
  <div class="d-flex flex-column align-center">
    <!-- this node -->
    <v-card class="ma-4" max-width="1000" width="100%" :title="String($route.name)">
      <v-card-text>
        <div class="d-flex flex-wrap align-center ga-4">
          <v-btn-toggle
            :model-value="state.role"
            mandatory
            color="primary"
            variant="outlined"
            density="comfortable"
            :disabled="!state.role"
            @update:model-value="setRole"
          >
            <v-btn value="server">Server</v-btn>
            <v-btn value="client">Client</v-btn>
          </v-btn-toggle>
          <div class="text-body-2">
            <template v-if="state.role === 'server'">
              Controls the screens of the clients paired with it. Alone, it is a standalone node.
            </template>
            <template v-else>Its screens are controlled by a server.</template>
          </div>
        </div>

        <v-alert v-if="state.notice" type="warning" variant="tonal" class="mt-4" closable>{{ state.notice }}</v-alert>

        <div class="mt-4 text-caption text-medium-emphasis">
          This node: <b>{{ state.name }}</b> — certificate fingerprint (SHA-256), to compare when pairing:
        </div>
        <code class="fingerprint" data-test="own-fingerprint">{{ state.fingerprint }}</code>
        <div v-if="state.role === 'server' && state.link_port" class="text-caption text-medium-emphasis mt-1">
          Clients connect to port {{ state.link_port }} (mutual TLS: only paired certificates are accepted).
        </div>
        <div v-if="state.role === 'server' && state.link_port === null" class="text-caption text-error mt-1">
          The listener for clients is not running (port in use?): see the log.
        </div>
      </v-card-text>
    </v-card>

    <!-- server: the clients -->
    <template v-if="state.role === 'server'">
      <v-card class="ma-4" max-width="1000" width="100%" title="Clients">
        <v-card-text>
          <div v-if="!state.clients.length" class="text-grey">
            No client yet. Put a node in client mode and adopt it from the list below, or enter its address.
          </div>
          <div class="client-grid">
            <v-card v-for="client in state.clients" :key="client.id" variant="outlined">
              <v-card-title class="d-flex align-center ga-2">
                <span class="text-truncate">{{ client.name }}</span>
                <v-chip size="small" :color="!client.enabled ? 'grey' : client.connected ? 'green' : 'orange'">
                  {{ !client.enabled ? 'disabled' : client.connected ? 'connected' : 'offline' }}
                </v-chip>
              </v-card-title>
              <v-card-subtitle>{{ client.ip }}:{{ client.port }}</v-card-subtitle>
              <v-card-text class="pb-0">
                <code class="fingerprint small">{{ client.fingerprint }}</code>
                <v-switch
                  :model-value="client.enabled"
                  color="primary"
                  hide-details
                  density="compact"
                  label="Enabled"
                  @update:model-value="v => api.Settings.Remote.set_client_enabled({client_id: client.id, enabled: !!v})"
                ></v-switch>
              </v-card-text>
              <v-card-actions>
                <v-btn color="red" @click="askUnpair(client)">Unpair</v-btn>
                <v-spacer></v-spacer>
                <v-btn color="primary" :disabled="!client.ip" @click="manageClient(client)">Open</v-btn>
              </v-card-actions>
            </v-card>
          </div>
        </v-card-text>
      </v-card>

      <v-card v-if="state.requests.length" class="ma-4" max-width="1000" width="100%" title="Pairing requests">
        <v-card-text>
          <v-list>
            <v-list-item v-for="r in state.requests" :key="r.id" :title="r.name" :subtitle="r.ip">
              <template #append>
                <code class="fingerprint small mr-4">{{ r.fingerprint }}</code>
                <v-btn color="primary" class="mr-2" @click="api.Settings.Remote.approve_request({client_id: r.id})">Approve</v-btn>
                <v-btn @click="api.Settings.Remote.reject_request({client_id: r.id})">Reject</v-btn>
              </template>
            </v-list-item>
          </v-list>
          <div class="text-caption">Approve only if the fingerprint is the one shown on the client's own page.</div>
        </v-card-text>
      </v-card>

      <v-card class="ma-4" max-width="1000" width="100%" title="Add a client">
        <v-card-text>
          <div class="text-subtitle-2 mb-2">Found on the network</div>
          <div v-if="!state.discovery" class="text-grey">Discovery is not available on this node (python3-zeroconf missing).</div>
          <div v-else-if="!state.discovered.length" class="text-grey">
            Nothing found. A node shows up here while it is in client mode and not paired.
          </div>
          <v-list v-else>
            <v-list-item v-for="d in state.discovered" :key="d.id" :title="d.name" :subtitle="`${d.host}:${d.port}`">
              <template #append>
                <v-btn color="primary" :loading="probing === d.host + ':' + d.port" @click="probe(d.host, d.port)">Adopt</v-btn>
              </template>
            </v-list-item>
          </v-list>

          <div class="text-subtitle-2 mt-4 mb-2">By address</div>
          <v-row dense>
            <v-col cols="8"><v-text-field v-model="manualHost" label="Client IP" variant="outlined" density="comfortable" hide-details></v-text-field></v-col>
            <v-col cols="2"><v-text-field v-model="manualPort" label="Port" placeholder="443" variant="outlined" density="comfortable" hide-details></v-text-field></v-col>
            <v-col cols="2"><v-btn block height="48" color="primary" :disabled="!manualHost" @click="probe(manualHost, manualPort || 443)">Look</v-btn></v-col>
          </v-row>
        </v-card-text>
      </v-card>
    </template>

    <!-- client -->
    <template v-else>
      <v-card v-if="state.server" class="ma-4" max-width="1000" width="100%" title="Server">
        <v-card-text>
          <div class="d-flex align-center ga-2">
            <b>{{ state.server.name }}</b> {{ state.server.ip }}:{{ state.server.port }}
            <v-chip size="small" :color="serverColor">{{ state.server.state }}</v-chip>
            <v-chip v-if="state.migrating" size="small" color="primary">moving content…</v-chip>
          </div>
          <div v-if="state.server.error" class="text-caption text-error">{{ state.server.error }}</div>
          <code class="fingerprint small">{{ state.server.fingerprint }}</code>
        </v-card-text>
        <v-card-actions>
          <v-btn color="red" :disabled="state.server.state !== 'connected'" @click="leaveDialog = true">Leave</v-btn>
          <v-btn @click="forgetDialog = true">Forget the server</v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" @click="openServer">Open</v-btn>
        </v-card-actions>
      </v-card>

      <v-card v-else class="ma-4" max-width="1000" width="100%" title="Waiting for a server">
        <v-card-text>
          <template v-if="state.pairing">
            <v-alert type="info" variant="tonal">
              Waiting for the administrator of {{ state.pairing.name }} ({{ state.pairing.ip }}) to approve.
              The server must show this node's fingerprint (above); the server's is:
              <code class="fingerprint small">{{ state.pairing.fingerprint }}</code>
            </v-alert>
          </template>
          <template v-else>
            This node announces itself on the network: a server can adopt it from its Remote page. Or ask a server by address.
          </template>
          <v-row dense class="mt-2">
            <v-col cols="8"><v-text-field v-model="serverIp" label="Server IP" variant="outlined" density="comfortable" hide-details></v-text-field></v-col>
            <v-col cols="2"><v-text-field v-model="serverPort" label="Port" placeholder="443" variant="outlined" density="comfortable" hide-details></v-text-field></v-col>
            <v-col cols="2"><v-btn block height="48" color="primary" :disabled="!serverIp" @click="requestPairing">Ask</v-btn></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions v-if="state.pairing">
          <v-btn @click="api.Settings.Remote.cancel_pairing()">Cancel the request</v-btn>
        </v-card-actions>
      </v-card>
    </template>

    <!-- adoption: the user confirms what the device presents -->
    <v-dialog v-model="adoptDialog" max-width="560">
      <v-card v-if="probed" title="Adopt this device?">
        <v-card-text>
          <div><b>{{ probed.name }}</b> ({{ probed.host }}:{{ probed.port }})</div>
          <div v-if="!probed.adoptable" class="text-error mt-2">
            It cannot be adopted: it must be in client mode and not paired already.
          </div>
          <template v-else>
            <div class="mt-2">The device presents this certificate. Check that it is exactly what its own Remote page shows:</div>
            <code class="fingerprint" data-test="probe-fingerprint">{{ probed.fingerprint }}</code>
            <div class="text-caption mt-2">
              If it is different, somebody may be in the way: do not continue. Its playlists and uploads will move to this server.
            </div>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="adoptDialog = false">Cancel</v-btn>
          <v-btn color="primary" :disabled="!probed.adoptable" @click="adopt">It is the same, adopt</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="unpairDialog" max-width="520">
      <v-card v-if="toUnpair" :title="`Unpair ${toUnpair.name}?`">
        <v-card-text>
          What its screens were showing goes back to it; what only it used is removed from this server.
          <div v-if="!toUnpair.connected" class="text-warning mt-2">
            It is offline: its content cannot be moved to it. You can forget it anyway: the content then stays here.
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="unpairDialog = false">Cancel</v-btn>
          <v-btn v-if="!toUnpair.connected" color="red" @click="unpair(true)">Forget it</v-btn>
          <v-btn v-else color="red" @click="unpair(false)">Unpair</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="leaveDialog" max-width="480">
      <v-card title="Leave the server?">
        <v-card-text>The playlists and uploads this node's screens show will come back to it. It becomes a standalone node.</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="leaveDialog = false">Cancel</v-btn>
          <v-btn color="red" @click="leave">Leave</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="forgetDialog" max-width="480">
      <v-card title="Forget the server?">
        <v-card-text>
          Nothing is moved: this node keeps only what it has locally and the server keeps this node as an offline client
          until somebody unpairs it there.
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="forgetDialog = false">Cancel</v-btn>
          <v-btn color="red" @click="forget">Forget</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '@/api'

const state = ref({
  role: null, name: '', fingerprint: '', link_port: null, discovery: true,
  clients: [], discovered: [], requests: [], server: null, pairing: null, notice: null, migrating: false,
})

const manualHost = ref('')
const manualPort = ref('')
const serverIp = ref('')
const serverPort = ref('')

const probed = ref(null)
const probing = ref('')
const adoptDialog = ref(false)
const unpairDialog = ref(false)
const toUnpair = ref(null)
const leaveDialog = ref(false)
const forgetDialog = ref(false)

const serverColor = computed(() => ({
  connected: 'green', connecting: 'orange', rejected: 'red', unreachable: 'orange',
}[state.value.server?.state] || 'grey'))

function setRole(role) {
  if (role === state.value.role) return
  if (role === 'client') api.Settings.Remote.become_client()
  else api.Settings.Remote.become_server()
}

function probe(host, port) {
  probing.value = `${host}:${port}`
  api.Settings.Remote.probe({host, port: Number(port) || 443})
}

function adopt() {
  adoptDialog.value = false
  api.Settings.Remote.adopt({host: probed.value.host, port: probed.value.port, fingerprint: probed.value.fingerprint})
}

function askUnpair(client) {
  toUnpair.value = client
  unpairDialog.value = true
}

function unpair(force) {
  unpairDialog.value = false
  api.Settings.Remote.unpair_client({client_id: toUnpair.value.id, force})
}

function leave() {
  leaveDialog.value = false
  api.Settings.Remote.leave_server()
}

function forget() {
  forgetDialog.value = false
  api.Settings.Remote.forget_server()
}

function requestPairing() {
  api.Settings.Remote.request_pairing({server_ip: serverIp.value, server_port: Number(serverPort.value) || 443})
}

function manageClient(client) {
  window.open(`https://${client.ip}:${client.port}/settings`, '_blank', 'noopener')
}

function openServer() {
  window.open(`https://${state.value.server.ip}:${state.value.server.port}/`, '_blank', 'noopener')
}

api.Settings.Remote.mode.on(data => {
  state.value = data
}, {init: true})

api.Settings.Remote.probe.on(data => {
  probing.value = ''
  if (data.error) return        // the page's error handler shows it
  probed.value = data
  adoptDialog.value = true
})
</script>

<style scoped>
.client-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.fingerprint {
  display: block;
  word-break: break-all;
  font-size: 0.95rem;
  padding: 6px 8px;
  margin: 4px 0;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.fingerprint.small {
  font-size: 0.72rem;
}
</style>
