<template>
  <div class="d-flex flex-column align-center">

    <!-- Hostname -->
    <v-card class="ma-4" max-width="1000" width="100%" title="Hostname">
      <v-card-text>
        <v-row align="center">
          <v-col cols="6">
            <v-text-field
              v-model="hostname"
              label="Hostname"
              variant="outlined"
              density="compact"
              hide-details
            ></v-text-field>
          </v-col>
          <v-col cols="auto">
            <v-btn
              @click="applyHostname"
              :disabled="!isHostnameChanged"
              color="primary"
              variant="text"
            >Apply</v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- Network configuration tabs -->
    <v-card class="ma-4 mt-0" max-width="1000" width="100%">
      <v-card-title class="d-flex align-center">
        Network
        <v-spacer></v-spacer>
        <!-- WiFi toggle -->
        <v-switch
          v-if="has_wireless"
          v-model="is_wireless_enabled"
          @update:model-value="toggleWifi"
          color="primary"
          hide-details
          density="compact"
          :label="'Wireless ' + (is_wireless_enabled ? 'on' : 'off')"
          class="mr-4"
        ></v-switch>
      </v-card-title>

      <v-tabs v-model="activeTab" color="primary">
        <v-tab value="overview">Overview</v-tab>
        <v-tab value="connections">Saved Connections</v-tab>
        <v-tab value="wifi" v-if="has_wireless">WiFi</v-tab>
      </v-tabs>
      <v-divider></v-divider>

      <v-tabs-window v-model="activeTab">

        <!-- ── Overview tab ──────────────────────────────────────────── -->
        <v-tabs-window-item value="overview">
          <v-list lines="two">
            <template v-if="devices.length === 0">
              <v-list-item>
                <v-progress-circular indeterminate size="20" class="mr-2"></v-progress-circular>
                Loading devices…
              </v-list-item>
            </template>
            <v-list-item v-for="dev in devices" :key="dev.path">
              <template v-slot:prepend>
                <v-icon :icon="devTypeIcon(dev.type)" size="32" class="mr-2"></v-icon>
              </template>
              <v-list-item-title class="d-flex align-center ga-2">
                {{ dev.interface }}
                <v-chip :color="devStateColor(dev.state)" size="x-small" label>
                  {{ devStateName(dev.state) }}
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle>
                <span v-if="dev.active_connection">{{ dev.active_connection.id }}</span>
                <span v-if="dev.ip4 && dev.ip4.addresses.length">
                  &nbsp;·&nbsp;
                  <span v-for="(a,i) in dev.ip4.addresses" :key="i">
                    {{ a.address }}/{{ a.prefix }}&nbsp;
                  </span>
                </span>
                <span v-if="dev.ip4 && dev.ip4.gateway">
                  &nbsp;GW {{ dev.ip4.gateway }}
                </span>
                <span v-else-if="!dev.active_connection" class="text-medium-emphasis">Not connected</span>
              </v-list-item-subtitle>
              <template v-slot:append>
                <v-btn
                  v-if="dev.active_connection"
                  size="small" variant="text" color="error"
                  @click="deactivateConn(dev.active_connection.path)"
                >Disconnect</v-btn>
                <v-btn
                  v-else
                  size="small" variant="text" color="primary"
                  @click="openConnectionPicker(dev)"
                >Connect</v-btn>
              </template>
            </v-list-item>
          </v-list>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" prepend-icon="mdi-refresh" @click="refreshDevices">Refresh</v-btn>
          </v-card-actions>
        </v-tabs-window-item>

        <!-- ── Saved Connections tab ─────────────────────────────────── -->
        <v-tabs-window-item value="connections">
          <v-list lines="one">
            <template v-if="connections.length === 0">
              <v-list-item class="text-medium-emphasis">No saved connections.</v-list-item>
            </template>
            <v-list-item v-for="conn in connections" :key="conn.path">
              <template v-slot:prepend>
                <v-icon :icon="connTypeIcon(conn.type)" class="mr-2"></v-icon>
              </template>
              <v-list-item-title class="d-flex align-center ga-2">
                {{ conn.id }}
                <v-chip v-if="isActive(conn.uuid)" color="success" size="x-small" label>Active</v-chip>
              </v-list-item-title>
              <v-list-item-subtitle>{{ conn.type }}</v-list-item-subtitle>
              <template v-slot:append>
                <v-btn
                  v-if="!isActive(conn.uuid)"
                  icon="mdi-play-circle-outline"
                  variant="text"
                  size="small"
                  color="primary"
                  title="Activate"
                  @click="activateConn(conn.path)"
                ></v-btn>
                <v-btn
                  v-else
                  icon="mdi-stop-circle-outline"
                  variant="text"
                  size="small"
                  color="warning"
                  title="Deactivate"
                  @click="deactivateByUuid(conn.uuid)"
                ></v-btn>
                <v-btn
                  icon="mdi-pencil"
                  variant="text"
                  size="small"
                  @click="openEditor(conn)"
                ></v-btn>
                <v-btn
                  icon="mdi-delete"
                  variant="text"
                  size="small"
                  color="error"
                  @click="confirmDelete(conn)"
                ></v-btn>
              </template>
            </v-list-item>
          </v-list>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" prepend-icon="mdi-refresh" @click="refreshConnections">Refresh</v-btn>
          </v-card-actions>
        </v-tabs-window-item>

        <!-- ── WiFi tab ──────────────────────────────────────────────── -->
        <v-tabs-window-item value="wifi" v-if="has_wireless">
          <v-progress-linear v-if="scanningWifi" indeterminate color="primary"></v-progress-linear>
          <div v-if="!is_wireless_enabled" class="d-flex align-center justify-center pa-8 text-medium-emphasis">
            Wireless is disabled.
          </div>
          <div v-else-if="wifiNetworks.length === 0 && !scanningWifi"
               class="d-flex align-center justify-center pa-8 text-medium-emphasis">
            No networks found. Try scanning.
          </div>
          <v-list v-else lines="one">
            <v-list-item v-for="net in wifiNetworks" :key="net.hw_address">
              <template v-slot:prepend>
                <v-icon :icon="getSignalIcon(net.strength, net.flags)" class="mr-2"></v-icon>
              </template>
              <v-list-item-title class="d-flex align-center ga-2">
                {{ net.ssid }}
                <v-chip size="x-small" label>{{ getBand(net.frequency) }}</v-chip>
                <!-- noinspection JSBitwiseOperatorUsage -->
                <v-icon v-if="net.flags & 1" icon="mdi-lock" size="small" color="grey"></v-icon>
              </v-list-item-title>
              <v-list-item-subtitle>{{ net.hw_address }}</v-list-item-subtitle>
              <template v-slot:append>
                <v-btn
                  size="small"
                  variant="tonal"
                  color="primary"
                  @click="openWifiConnect(net)"
                >Connect</v-btn>
              </template>
            </v-list-item>
          </v-list>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" prepend-icon="mdi-magnify" :loading="scanningWifi" @click="scanWifi">
              Scan
            </v-btn>
          </v-card-actions>
        </v-tabs-window-item>
      </v-tabs-window>
    </v-card>

    <!-- ── Connection editor dialog ────────────────────────────────────── -->
    <v-dialog v-model="editorOpen" max-width="540" scrollable>
      <v-card :title="editorConn ? 'Edit Connection – ' + editorConn.id : 'Edit Connection'">
        <v-divider></v-divider>
        <v-card-text>
          <v-text-field
            v-model="editorForm.id"
            label="Connection name"
            variant="outlined"
            density="compact"
            class="mb-3"
          ></v-text-field>
          <v-checkbox
            v-model="editorForm.autoconnect"
            label="Connect automatically"
            density="compact"
            hide-details
            class="mb-3"
          ></v-checkbox>

          <!-- WiFi-specific fields -->
          <template v-if="editorConn && editorConn.type === '802-11-wireless'">
            <v-text-field
              v-model="editorForm.ssid"
              label="SSID"
              variant="outlined"
              density="compact"
              class="mb-3"
            ></v-text-field>
            <v-text-field
              v-model="editorForm.password"
              label="Password (leave empty to keep current)"
              variant="outlined"
              density="compact"
              type="password"
              class="mb-3"
            ></v-text-field>
          </template>

          <!-- IP settings -->
          <div class="text-subtitle-2 mb-2">IPv4</div>
          <v-select
            v-model="editorForm.ip_method"
            :items="[{title:'Automatic (DHCP)', value:'auto'},{title:'Manual (Static)', value:'manual'},{title:'Link-local only', value:'link-local'},{title:'Disabled',value:'disabled'}]"
            label="Method"
            variant="outlined"
            density="compact"
            class="mb-3"
          ></v-select>
          <template v-if="editorForm.ip_method === 'manual'">
            <v-row dense>
              <v-col cols="8">
                <v-text-field
                  v-model="editorForm.ip_address"
                  label="IP Address"
                  variant="outlined"
                  density="compact"
                  placeholder="192.168.1.100"
                ></v-text-field>
              </v-col>
              <v-col cols="4">
                <v-text-field
                  v-model.number="editorForm.prefix_len"
                  label="Prefix"
                  variant="outlined"
                  density="compact"
                  type="number"
                  min="1"
                  max="32"
                ></v-text-field>
              </v-col>
            </v-row>
            <v-text-field
              v-model="editorForm.gateway"
              label="Gateway"
              variant="outlined"
              density="compact"
              placeholder="192.168.1.1"
              class="mb-2"
            ></v-text-field>
            <v-combobox
              v-model="editorForm.dns"
              label="DNS servers"
              variant="outlined"
              density="compact"
              multiple
              chips
              closable-chips
              placeholder="8.8.8.8"
              hint="Press Enter to add"
              persistent-hint
            ></v-combobox>
          </template>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="editorOpen = false">Cancel</v-btn>
          <v-btn color="primary" :loading="editorSaving" @click="saveEditor">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ── WiFi connect dialog ──────────────────────────────────────────── -->
    <v-dialog v-model="wifiDialogOpen" max-width="480" scrollable>
      <v-card :title="'Connect to ' + wifiForm.ssid">
        <v-divider></v-divider>
        <v-card-text>
          <v-text-field
            v-if="wifiForm.secured"
            v-model="wifiForm.password"
            label="Password"
            variant="outlined"
            density="compact"
            type="password"
            autofocus
            class="mb-3"
            @keyup.enter="connectWifi"
          ></v-text-field>
          <v-select
            v-model="wifiForm.device"
            :items="wifiDevices"
            item-title="interface"
            item-value="interface"
            label="Device"
            variant="outlined"
            density="compact"
            class="mb-3"
          ></v-select>

          <v-expand-transition>
            <div>
              <v-btn
                variant="text"
                size="small"
                prepend-icon="mdi-chevron-down"
                @click="wifiAdvanced = !wifiAdvanced"
                class="mb-2"
              >Advanced IP settings</v-btn>
              <div v-if="wifiAdvanced">
                <v-select
                  v-model="wifiForm.ip_method"
                  :items="[{title:'Automatic (DHCP)', value:'auto'},{title:'Manual (Static)', value:'manual'}]"
                  label="IPv4 Method"
                  variant="outlined"
                  density="compact"
                  class="mb-3"
                ></v-select>
                <template v-if="wifiForm.ip_method === 'manual'">
                  <v-row dense>
                    <v-col cols="8">
                      <v-text-field
                        v-model="wifiForm.ip_address"
                        label="IP Address"
                        variant="outlined"
                        density="compact"
                        placeholder="192.168.1.100"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="4">
                      <v-text-field
                        v-model.number="wifiForm.prefix_len"
                        label="Prefix"
                        variant="outlined"
                        density="compact"
                        type="number"
                        min="1"
                        max="32"
                      ></v-text-field>
                    </v-col>
                  </v-row>
                  <v-text-field
                    v-model="wifiForm.gateway"
                    label="Gateway"
                    variant="outlined"
                    density="compact"
                    placeholder="192.168.1.1"
                    class="mb-2"
                  ></v-text-field>
                  <v-combobox
                    v-model="wifiForm.dns"
                    label="DNS servers"
                    variant="outlined"
                    density="compact"
                    multiple
                    chips
                    closable-chips
                    placeholder="8.8.8.8"
                  ></v-combobox>
                </template>
              </div>
            </div>
          </v-expand-transition>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="wifiDialogOpen = false">Cancel</v-btn>
          <v-btn color="primary" :loading="wifiConnecting" @click="connectWifi">Connect</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ── Device connection picker dialog ─────────────────────────────── -->
    <v-dialog v-model="connPickerOpen" max-width="400">
      <v-card :title="'Connect ' + (connPickerDev ? connPickerDev.interface : '') + ' to…'">
        <v-list lines="one">
          <v-list-item
            v-for="conn in compatibleConnections"
            :key="conn.path"
            :title="conn.id"
            :subtitle="conn.type"
            @click="activateOnDev(conn.path, connPickerDev.path)"
          >
            <template v-slot:prepend>
              <v-icon :icon="connTypeIcon(conn.type)"></v-icon>
            </template>
          </v-list-item>
          <v-list-item v-if="compatibleConnections.length === 0" class="text-medium-emphasis">
            No compatible profiles found.
          </v-list-item>
        </v-list>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="connPickerOpen = false">Cancel</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ── Delete confirmation ─────────────────────────────────────────── -->
    <v-dialog v-model="deleteDialogOpen" max-width="420">
      <v-card title="Delete connection">
        <v-card-text>
          Delete <strong>{{ connToDelete ? connToDelete.id : '' }}</strong>? This cannot be undone.
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="deleteDialogOpen = false">Cancel</v-btn>
          <v-btn color="error" @click="doDelete">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Error snackbar -->
    <v-snackbar v-model="errorSnack" color="error" :timeout="4000">{{ errorMsg }}</v-snackbar>

  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'

// ── state ──────────────────────────────────────────────────────────────────

const hostname       = ref('')
const originalHostname = ref('')
const isHostnameChanged = computed(() => hostname.value !== originalHostname.value)

const activeTab      = ref('overview')
const devices        = ref([])
const connections    = ref([])
const activeConns    = ref([])

// WiFi
const has_wireless       = ref(false)
const is_wireless_enabled = ref(false)
const default_wireless_device = ref(null)
const wifiNetworks       = ref([])
const scanningWifi       = ref(false)

// Editor dialog
const editorOpen    = ref(false)
const editorConn    = ref(null)   // the connection metadata (id, type, path)
const editorSaving  = ref(false)
const editorForm    = reactive({
  id: '', autoconnect: true, ssid: '', password: '',
  ip_method: 'auto', ip_address: '', prefix_len: 24, gateway: '', dns: [],
})

// WiFi connect dialog
const wifiDialogOpen = ref(false)
const wifiConnecting = ref(false)
const wifiAdvanced   = ref(false)
const wifiForm       = reactive({
  ssid: '', password: '', secured: false, bssid: null,
  device: null, ip_method: 'auto', ip_address: '', prefix_len: 24, gateway: '', dns: [],
})

// Connection picker (for "Connect" button on overview)
const connPickerOpen = ref(false)
const connPickerDev  = ref(null)
const compatibleConnections = computed(() => {
  if (!connPickerDev.value) return []
  const devType = connPickerDev.value.type
  return connections.value.filter(c => {
    if (devType === 2) return c.type === '802-11-wireless' || c.type === '802-11-wireless-security'
    if (devType === 1) return c.type === '802-3-ethernet'
    return true
  })
})

// Delete
const deleteDialogOpen = ref(false)
const connToDelete = ref(null)

// Error
const errorSnack = ref(false)
const errorMsg   = ref('')

// Convenience
const wifiDevices = computed(() => devices.value.filter(d => d.type === 2))
const sendCommand = window.sendCommand

// ── WS setup ───────────────────────────────────────────────────────────────

window.setInitCommands(
  'Settings/hostname',
  'Settings/NM/devices',
  'Settings/NM/connections',
  'Settings/NM/activeConnections',
  'Settings/has_wireless',
)

onWSMessage = (data) => {
  switch (data.target) {
    case 'Settings/hostname':
      hostname.value = data.hostname
      originalHostname.value = data.hostname
      break

    case 'Settings/NM/devices':
      if (data.error) { showError(data.error); break }
      if (data.devices !== undefined) devices.value = data.devices
      break

    case 'Settings/NM/connections':
    case 'Settings/NM/editConnection':
    case 'Settings/NM/deleteConnection':
      if (data.error) { showError(data.error); break }
      if (data.connections !== undefined) connections.value = data.connections
      if (data.target === 'Settings/NM/editConnection') {
        editorSaving.value = false
        editorOpen.value = false
      }
      break

    case 'Settings/NM/connectionDetails':
      if (data.error) { showError(data.error); break }
      if (data.details && editorOpen.value) {
        const d = data.details
        const ipv4 = d.ipv4 || {}
        editorForm.ip_method = ipv4.method || 'auto'
        if (Array.isArray(ipv4['address-data']) && ipv4['address-data'].length) {
          editorForm.ip_address = ipv4['address-data'][0].address || ''
          editorForm.prefix_len = ipv4['address-data'][0].prefix || 24
        }
        editorForm.gateway = ipv4.gateway || ''
        editorForm.dns     = ipv4.dns || []
        const wifi = d['802-11-wireless']
        if (wifi && wifi.ssid) {
          editorForm.ssid = typeof wifi.ssid === 'string'
            ? wifi.ssid
            : new TextDecoder().decode(Uint8Array.from(wifi.ssid))
        }
      }
      break

    case 'Settings/NM/activeConnections':
      if (data.active !== undefined) activeConns.value = data.active
      break

    // activate / deactivate / connectWifi all yield devices + active updates
    case 'Settings/NM/activate':
    case 'Settings/NM/deactivate':
    case 'Settings/NM/connectWifi':
      if (data.error) {
        showError(data.error)
        wifiConnecting.value = false
        break
      }
      if (data.devices !== undefined)     devices.value    = data.devices
      if (data.active !== undefined)      activeConns.value = data.active
      if (data.connections !== undefined) connections.value = data.connections
      if (data.target === 'Settings/NM/connectWifi' && data.connected_uuid !== undefined) {
        wifiConnecting.value = false
        wifiDialogOpen.value = false
        // refresh connections so the new profile appears
        sendCommand('Settings/NM/connections')
      }
      break

    case 'Settings/has_wireless':
      has_wireless.value = data.wireless
      if (data.wireless) {
        sendCommand('Settings/is_wireless_enabled')
        sendCommand('Settings/get_default_wlan_device')
      }
      break

    case 'Settings/is_wireless_enabled':
      is_wireless_enabled.value = data.enabled
      break

    case 'Settings/get_default_wlan_device':
      default_wireless_device.value = data.device
      if (!wifiForm.device) wifiForm.device = data.device
      break

    case 'Settings/get_wireless_networks':
      wifiNetworks.value = data.wifis ?? []
      scanningWifi.value = false
      break
  }
}

// ── actions ────────────────────────────────────────────────────────────────

const applyHostname = () => {
  sendCommand('Settings/setHostname', { hostname: hostname.value })
}

const refreshDevices = () => {
  sendCommand('Settings/NM/devices')
  sendCommand('Settings/NM/activeConnections')
}

const refreshConnections = () => {
  sendCommand('Settings/NM/connections')
  sendCommand('Settings/NM/activeConnections')
}

const isActive = (uuid) => activeConns.value.some(a => a.uuid === uuid)

const deactivateConn = (activePath) => {
  sendCommand('Settings/NM/deactivate', { active_path: activePath })
}

const deactivateByUuid = (uuid) => {
  const ac = activeConns.value.find(a => a.uuid === uuid)
  if (ac) deactivateConn(ac.path)
}

const activateConn = (connPath) => {
  // Pick first matching device (user can use connection picker for more control)
  const conn = connections.value.find(c => c.path === connPath)
  if (!conn) return
  // Find a suitable device
  let devPath = '/'
  if (conn.type === '802-11-wireless') {
    const d = devices.value.find(d => d.type === 2)
    if (d) devPath = d.path
  } else if (conn.type === '802-3-ethernet') {
    const d = devices.value.find(d => d.type === 1)
    if (d) devPath = d.path
  }
  sendCommand('Settings/NM/activate', { conn_path: connPath, dev_path: devPath })
}

const activateOnDev = (connPath, devPath) => {
  connPickerOpen.value = false
  sendCommand('Settings/NM/activate', { conn_path: connPath, dev_path: devPath })
}

const openConnectionPicker = (dev) => {
  connPickerDev.value = dev
  connPickerOpen.value = true
}

// ── editor ─────────────────────────────────────────────────────────────────

const openEditor = (conn) => {
  editorConn.value = conn
  // Reset form then fill from what we know
  Object.assign(editorForm, {
    id: conn.id, autoconnect: conn.autoconnect,
    ssid: '', password: '',
    ip_method: 'auto', ip_address: '', prefix_len: 24, gateway: '', dns: [],
  })
  // Fetch full details to populate IP fields
  sendCommand('Settings/NM/connectionDetails', { path: conn.path })
  editorOpen.value = true
}

const saveEditor = () => {
  if (!editorConn.value) return
  editorSaving.value = true
  sendCommand('Settings/NM/editConnection', {
    conn_path   : editorConn.value.path,
    conn_id     : editorForm.id,
    autoconnect : editorForm.autoconnect,
    ip_method   : editorForm.ip_method,
    ip_address  : editorForm.ip_address || null,
    prefix_len  : editorForm.prefix_len,
    gateway     : editorForm.gateway || null,
    dns         : editorForm.dns,
    ssid        : editorForm.ssid || null,
    password    : editorForm.password || null,
  })
  editorSaving.value = false
}

// ── delete ─────────────────────────────────────────────────────────────────

const confirmDelete = (conn) => {
  connToDelete.value = conn
  deleteDialogOpen.value = true
}

const doDelete = () => {
  if (!connToDelete.value) return
  sendCommand('Settings/NM/deleteConnection', { conn_path: connToDelete.value.path })
  deleteDialogOpen.value = false
  connToDelete.value = null
}

// ── WiFi ───────────────────────────────────────────────────────────────────

const toggleWifi = (val) => {
  sendCommand('Settings/set_wireless_enabled', { enabled: val })
}

const scanWifi = () => {
  scanningWifi.value = true
  sendCommand('Settings/get_wireless_networks')
}

const openWifiConnect = (net) => {
  // noinspection JSBitwiseOperatorUsage
  const secured = (net.flags & 1) !== 0
  Object.assign(wifiForm, {
    ssid: net.ssid, password: '', secured,
    bssid: net.hw_address,
    device: default_wireless_device.value || (wifiDevices.value[0]?.interface ?? null),
    ip_method: 'auto', ip_address: '', prefix_len: 24, gateway: '', dns: [],
  })
  wifiAdvanced.value = false
  wifiDialogOpen.value = true
}

const connectWifi = () => {
  if (!wifiForm.ssid) return
  wifiConnecting.value = true
  sendCommand('Settings/NM/connectWifi', {
    ssid      : wifiForm.ssid,
    device    : wifiForm.device,
    password  : wifiForm.password || null,
    bssid     : wifiForm.bssid,
    ip_method : wifiForm.ip_method,
    ip_address: wifiForm.ip_method === 'manual' ? (wifiForm.ip_address || null) : null,
    prefix_len: wifiForm.prefix_len,
    gateway   : wifiForm.ip_method === 'manual' ? (wifiForm.gateway || null) : null,
    dns       : wifiForm.ip_method === 'manual' ? wifiForm.dns : null,
  })
}

// auto-scan when entering the WiFi tab
watch(activeTab, (val) => {
  if (val === 'wifi' && is_wireless_enabled.value) scanWifi()
})

// ── display helpers ────────────────────────────────────────────────────────

const devTypeIcon = (type) => {
  if (type === 2) return 'mdi-wifi'
  if (type === 1) return 'mdi-ethernet'
  return 'mdi-network-outline'
}

const connTypeIcon = (type) => {
  if (type === '802-11-wireless') return 'mdi-wifi'
  if (type === '802-3-ethernet') return 'mdi-ethernet'
  return 'mdi-network-outline'
}

// NM_DEVICE_STATE values
const devStateName = (state) => {
  if (state === 100) return 'Connected'
  if (state === 30)  return 'Disconnected'
  if (state === 20)  return 'Unavailable'
  if (state === 10)  return 'Unmanaged'
  if (state === 120) return 'Failed'
  if (state >= 40 && state < 100) return 'Connecting…'
  return 'Unknown'
}

const devStateColor = (state) => {
  if (state === 100) return 'success'
  if (state === 120) return 'error'
  if (state >= 40 && state < 100) return 'warning'
  return 'default'
}

const getSignalIcon = (strength, flags) => {
  let icon = 'mdi-wifi-strength-'
  if (strength > 75) icon += '4'
  else if (strength > 50) icon += '3'
  else if (strength > 25) icon += '2'
  else icon += '1'
  // noinspection JSBitwiseOperatorUsage
  if (flags & 1) icon += '-lock'
  return icon
}

const getBand = (frequency) => {
  if (frequency >= 5925) return '6 GHz'
  if (frequency >= 5000) return '5 GHz'
  if (frequency >= 2400) return '2.4 GHz'
  return `${frequency} MHz`
}

const showError = (msg) => {
  errorMsg.value = msg
  errorSnack.value = true
}
</script>
