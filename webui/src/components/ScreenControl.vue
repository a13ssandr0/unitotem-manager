<template>
  <v-card class="ma-4" max-width="1000" width="100%" title="Monitor control">
    <template v-slot:append>
      <v-btn variant="text" color="primary" :disabled="probing"
             :prepend-icon="probing ? 'mdi-loading mdi-spin' : 'mdi-magnify-scan'" @click="api.Settings.Display.Screens.probe()">
        Probe monitors
      </v-btn>
    </template>
    <v-card-text v-if="!screens.length" class="text-medium-emphasis">
      No screen is known. Screens appear here when the X server reports an output, or once configured below.
    </v-card-text>
    <v-expansion-panels variant="accordion" multiple>
      <v-expansion-panel v-for="screen in screens" :key="screen.name">
        <v-expansion-panel-title>
          <v-icon :color="screen.active ? 'green' : 'grey'" class="me-2">
            {{ screen.active ? 'mdi-monitor' : 'mdi-monitor-off' }}
          </v-icon>
          <strong>{{ screen.name }}</strong>
          <span v-if="screen.identity" class="ms-3 text-caption text-medium-emphasis">{{ screen.identity }}</span>
          <v-chip class="ms-3" size="small" :color="screen.active ? 'green' : 'grey'">
            {{ screen.active ? 'on' : screen.connected ? 'off' : 'not connected' }}
          </v-chip>
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <div class="d-flex align-center flex-wrap ga-2 mb-3">
            <v-btn color="green" variant="tonal" prepend-icon="mdi-power" :loading="busy[screen.name] === 'on'"
                   @click="setPower(screen, 'on')">Switch on</v-btn>
            <v-btn color="red" variant="tonal" prepend-icon="mdi-power-off" :loading="busy[screen.name] === 'off'"
                   @click="setPower(screen, 'off')">Switch off</v-btn>
            <v-select v-model="method[screen.name]" :items="methodItems(screen)" label="Use" density="compact"
                      variant="outlined" hide-details style="max-width: 200px"></v-select>
          </div>

          <div class="mb-3">
            <v-tooltip v-for="(info, mech) in screen.mechanisms" :key="mech" location="bottom" :disabled="info.available">
              <template v-slot:activator="{ props }">
                <v-chip v-bind="props" size="small" class="me-2" :color="info.available ? 'primary' : undefined"
                        :variant="info.available ? 'flat' : 'outlined'">
                  {{ mechanismNames[mech] }}
                </v-chip>
              </template>
              {{ info.reason }}
            </v-tooltip>
          </div>

          <div v-if="screen.mechanisms.ddc.available" class="d-flex align-center flex-wrap ga-4 mb-3">
            <v-select v-if="Object.keys(screen.input_sources).length" label="Input source" density="compact"
                      variant="outlined" hide-details style="max-width: 260px" :items="inputItems(screen)"
                      @update:model-value="v => api.Settings.Display.Screens.set_input({name: screen.name, source: v})">
            </v-select>
            <v-slider label="Volume" min="0" max="100" step="1" density="compact" hide-details style="min-width: 220px"
                      v-model="volume[screen.name]" thumb-label
                      @end="api.Settings.Display.Screens.set_volume({name: screen.name, volume: volume[screen.name] ?? 0})">
            </v-slider>
            <v-btn variant="text" icon="mdi-volume-off" title="Mute"
                   @click="api.Settings.Display.Screens.set_mute({name: screen.name, mute: true})"></v-btn>
            <v-btn variant="text" icon="mdi-volume-high" title="Unmute"
                   @click="api.Settings.Display.Screens.set_mute({name: screen.name, mute: false})"></v-btn>
          </div>

          <v-divider class="my-3"></v-divider>
          <div class="text-subtitle-2 mb-2">How this monitor is controlled</div>
          <div class="d-flex flex-wrap ga-4 mb-2">
            <v-select v-model="form[screen.name].on_mechanism" :items="mechanismChoices('on')" label="Switch on with"
                      density="compact" variant="outlined" hide-details style="max-width: 220px"></v-select>
            <v-select v-model="form[screen.name].off_mechanism" :items="mechanismChoices('off')" label="Switch off with"
                      density="compact" variant="outlined" hide-details style="max-width: 220px"></v-select>
            <v-switch v-model="form[screen.name].power_on_at_start" color="primary" density="compact" hide-details
                      label="Switch on when UniTotem starts"></v-switch>
          </div>

          <div class="text-caption text-medium-emphasis mb-1">
            Wake-on-LAN, for a monitor with a network port of its own (it can only switch the monitor on):
          </div>
          <div class="d-flex flex-wrap ga-4 mb-3">
            <v-text-field v-model="form[screen.name].wol_mac" label="MAC address" density="compact" variant="outlined"
                          hide-details style="max-width: 220px" placeholder="aa:bb:cc:dd:ee:ff"></v-text-field>
            <v-text-field v-model="form[screen.name].wol_broadcast" label="Broadcast address" density="compact"
                          variant="outlined" hide-details style="max-width: 200px"></v-text-field>
          </div>

          <div class="text-caption text-medium-emphasis mb-1">
            Serial port: the commands come from the monitor's manual; \r \n and \xNN are escapes.
          </div>
          <div class="d-flex flex-wrap ga-4 mb-3">
            <v-text-field v-model="form[screen.name].serial_port" label="Port" density="compact" variant="outlined"
                          hide-details style="max-width: 200px" placeholder="/dev/ttyUSB0"></v-text-field>
            <v-select v-model="form[screen.name].serial_baud" :items="bauds" label="Baud rate" density="compact"
                      variant="outlined" hide-details style="max-width: 130px"></v-select>
            <v-text-field v-model="form[screen.name].serial_on" label="Command to switch on" density="compact"
                          variant="outlined" hide-details style="max-width: 220px"></v-text-field>
            <v-text-field v-model="form[screen.name].serial_off" label="Command to switch off" density="compact"
                          variant="outlined" hide-details style="max-width: 220px"></v-text-field>
          </div>
          <v-btn color="primary" variant="tonal" @click="save(screen)">Save</v-btn>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>

<script setup>
import {reactive, ref, watch} from 'vue'
import {api} from '@/api'

const screens = ref([])
const probing = ref(false)
const busy = reactive({})
const method = reactive({})
const volume = reactive({})
const form = reactive({})
const bauds = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

const mechanismNames = {x: 'Display output', ddc: 'DDC/CI', cec: 'CEC', wol: 'Wake-on-LAN', serial: 'Serial'}

const mechanismChoices = (action) => [
  {title: 'Automatic', value: 'auto'},
  ...Object.entries(mechanismNames)
    .filter(([key]) => !(action === 'off' && key === 'wol'))
    .map(([value, title]) => ({title, value})),
]

function methodItems(screen) {
  return [{title: 'Automatic', value: 'auto'},
    ...Object.entries(screen.mechanisms).filter(([, info]) => info.available)
      .map(([key]) => ({title: mechanismNames[key], value: key}))]
}

function inputItems(screen) {
  return Object.entries(screen.input_sources).map(([value, title]) => ({title: `${title} (0x${value})`, value: parseInt(value, 16)}))
}

function fillForm(screen) {
  const c = screen.config
  form[screen.name] = {
    on_mechanism: c.on_mechanism, off_mechanism: c.off_mechanism, power_on_at_start: c.power_on_at_start,
    wol_mac: c.wol?.mac ?? '', wol_broadcast: c.wol?.broadcast ?? '255.255.255.255',
    serial_port: c.serial?.port ?? '', serial_baud: c.serial?.baud ?? 9600,
    serial_on: c.serial?.on_command ?? '', serial_off: c.serial?.off_command ?? '',
  }
  method[screen.name] ??= 'auto'
}

api.Settings.Display.Screens.screens.on(data => {
  screens.value = data.screens
  probing.value = !!data.probing
  data.screens.forEach(s => {
    busy[s.name] = null
    // Do not overwrite what the user is typing: the form is only (re)filled for a screen not seen before
    if (!form[s.name]) fillForm(s)
  })
}, {init: true})

function setPower(screen, state) {
  busy[screen.name] = state
  api.Settings.Display.Screens.set_power({name: screen.name, state, mechanism: method[screen.name] ?? 'auto'})
  // An error comes back as a message to this client only and no `screens` follows: release the button
  setTimeout(() => { busy[screen.name] = null }, 30000)
}

function save(screen) {
  const f = form[screen.name]
  const args = {name: screen.name, on_mechanism: f.on_mechanism, off_mechanism: f.off_mechanism,
    power_on_at_start: f.power_on_at_start}
  if (f.wol_mac.trim()) args.wol = {mac: f.wol_mac.trim(), broadcast: f.wol_broadcast.trim() || '255.255.255.255'}
  else args.clear_wol = true
  if (f.serial_port.trim()) {
    args.serial = {port: f.serial_port.trim(), baud: f.serial_baud, on_command: f.serial_on, off_command: f.serial_off}
  } else args.clear_serial = true
  api.Settings.Display.Screens.configure(args)
}

watch(screens, () => {}, {deep: false})
</script>
