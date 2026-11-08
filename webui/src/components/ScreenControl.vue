<template>
  <v-card class="ma-4" max-width="1000" width="100%" title="Monitor control">
    <template v-slot:append>
      <v-btn variant="text" color="primary" :disabled="probing"
             :prepend-icon="probing ? 'mdi-loading mdi-spin' : 'mdi-magnify-scan'" @click="api.Settings.Display.Screens.probe()">
        Probe monitors
      </v-btn>
    </template>
    <v-card-text v-if="!visible.length" class="text-medium-emphasis">
      No screen is known.
    </v-card-text>
    <v-expansion-panels variant="accordion" multiple>
      <v-expansion-panel v-for="screen in visible" :key="screen.name">
        <v-expansion-panel-title>
          <v-icon :color="screen.active && !screen.commanded_off ? 'green' : 'grey'" class="me-2">
            {{ screen.active && !screen.commanded_off ? 'mdi-monitor' : 'mdi-monitor-off' }}
          </v-icon>
          <strong>{{ screen.name }}</strong>
          <span v-if="screen.identity" class="ms-3 text-caption text-medium-emphasis">{{ screen.identity }}</span>
          <!-- a monitor switched off over DDC/CEC/serial keeps its link: the compositor still drives it, only what was commanded is known -->
          <v-chip class="ms-3" size="small" :color="screen.active && !screen.commanded_off ? 'green' : 'grey'">
            {{ screen.commanded_off ? `off (${screen.commanded_off.mechanism.toUpperCase()})`
               : screen.active ? 'on' : screen.connected ? 'off' : 'not connected' }}
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

          <div v-if="screen.ddc_features.length" class="d-flex align-center flex-wrap ga-4 mb-3">
            <v-select v-if="screen.ddc_features.includes('60') && Object.keys(screen.input_sources).length" label="Input source" density="compact"
                      variant="outlined" hide-details style="max-width: 260px" :items="inputItems(screen)"
                      @update:model-value="v => api.Settings.Display.Screens.set_input({name: screen.name, source: v})">
            </v-select>
            <v-slider v-if="screen.ddc_features.includes('62')" label="Volume" min="0" max="100" step="1" density="compact" hide-details style="min-width: 220px"
                      v-model="volume[screen.name]" thumb-label
                      @end="api.Settings.Display.Screens.set_volume({name: screen.name, volume: volume[screen.name] ?? 0})">
            </v-slider>
            <v-btn v-if="screen.ddc_features.includes('8D')" variant="text" icon="mdi-volume-off" title="Mute"
                   @click="api.Settings.Display.Screens.set_mute({name: screen.name, mute: true})"></v-btn>
            <v-btn v-if="screen.ddc_features.includes('8D')" variant="text" icon="mdi-volume-high" title="Unmute"
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

          <div v-if="usesWol(screen.name)" class="text-caption text-medium-emphasis mb-1">
            Wake-on-LAN, for a monitor with a network port of its own (it can only switch the monitor on):
          </div>
          <div v-if="usesWol(screen.name)" class="d-flex flex-wrap ga-4 mb-3">
            <v-text-field v-model="form[screen.name].wol_mac" label="MAC address" density="compact" variant="outlined"
                          hide-details style="max-width: 220px" placeholder="aa:bb:cc:dd:ee:ff"></v-text-field>
            <v-text-field v-model="form[screen.name].wol_broadcast" label="Broadcast address" density="compact"
                          variant="outlined" hide-details style="max-width: 200px"></v-text-field>
            <v-text-field v-model="form[screen.name].wol_interface" label="Interface (empty: all)" density="compact"
                          variant="outlined" hide-details style="max-width: 200px"></v-text-field>
            <v-text-field v-model.number="form[screen.name].wol_repeat" label="Repeat" type="number" density="compact"
                          variant="outlined" hide-details style="max-width: 100px"></v-text-field>
          </div>

          <div v-if="usesSerial(screen.name)" class="text-caption text-medium-emphasis mb-1">
            Serial port: the commands come from the monitor's manual; \r \n and \xNN are escapes.
          </div>
          <div v-if="usesSerial(screen.name)" class="d-flex flex-wrap ga-4 mb-3">
            <v-text-field v-model="form[screen.name].serial_port" label="Port" density="compact" variant="outlined"
                          hide-details style="max-width: 200px" placeholder="/dev/ttyUSB0"></v-text-field>
            <v-select v-model="form[screen.name].serial_baud" :items="bauds" label="Baud rate" density="compact"
                      variant="outlined" hide-details style="max-width: 130px"></v-select>
            <v-text-field v-model="form[screen.name].serial_on" label="Command to switch on" density="compact"
                          variant="outlined" hide-details style="max-width: 220px"></v-text-field>
            <v-text-field v-model="form[screen.name].serial_off" label="Command to switch off" density="compact"
                          variant="outlined" hide-details style="max-width: 220px"></v-text-field>
          </div>
          <div v-if="usesGpio(screen.name)" class="text-caption text-medium-emphasis mb-1">
            GPIO line: a relay in the monitor's supply (level) or a line that presses its power button (pulse). The line is set to "on" when UniTotem stops.
          </div>
          <div v-if="usesGpio(screen.name)" class="d-flex flex-wrap ga-4 mb-3">
            <v-select v-model="form[screen.name].gpio_chip" :items="gpioChips" label="Chip" density="compact" variant="outlined"
                      hide-details style="max-width: 260px" no-data-text="No GPIO chip on this machine"></v-select>
            <v-text-field v-model.number="form[screen.name].gpio_line" label="Line" type="number" min="0" density="compact"
                          variant="outlined" hide-details style="max-width: 110px"
                          :hint="gpioLineName(form[screen.name])" persistent-hint></v-text-field>
            <v-select v-model="form[screen.name].gpio_mode" :items="gpioModes" label="Mode" density="compact" variant="outlined"
                      hide-details style="max-width: 200px"></v-select>
            <v-text-field v-if="form[screen.name].gpio_mode === 'pulse'" v-model.number="form[screen.name].gpio_pulse_ms" label="Press (ms)"
                          type="number" density="compact" variant="outlined" hide-details style="max-width: 130px"></v-text-field>
            <v-switch v-model="form[screen.name].gpio_active_low" color="primary" density="compact" hide-details label="Active low"></v-switch>
          </div>
          <div v-if="(screen.cec_devices?.length ?? 0) > 1 && usesCec(screen.name)" class="text-caption text-medium-emphasis mb-1">
            CEC adapter: the HDMI port the TV is connected to.
          </div>
          <div v-if="(screen.cec_devices?.length ?? 0) > 1 && usesCec(screen.name)" class="d-flex flex-wrap ga-4 mb-3">
            <v-select v-model="form[screen.name].cec_device" :items="cecItems(screen)" label="CEC adapter" density="compact"
                      variant="outlined" hide-details style="max-width: 260px"></v-select>
          </div>
          <v-btn color="primary" variant="tonal" @click="save(screen)">Save</v-btn>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>

<script setup>
import {computed, reactive, ref, watch} from 'vue'
import {api} from '@/api'

// screen: the output name shown (one monitor per page section); null shows nothing
const props = defineProps({screen: {type: String, default: null}})
const screens = ref([])
const visible = computed(() => screens.value.filter(s => s.name === props.screen))
const probing = ref(false)
const busy = reactive({})
const method = reactive({})
const volume = reactive({})
const form = reactive({})
const bauds = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

const mechanismNames = {output: 'Display output', ddc: 'DDC/CI', cec: 'CEC', wol: 'Wake-on-LAN', serial: 'Serial', gpio: 'GPIO'}

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

// The settings of a mechanism are only shown when that mechanism can be used: chosen explicitly for switching on or
// off, or left on Automatic, which may pick it. With DDC/CI or CEC chosen for both, the Wake-on-LAN and serial fields
// would only be noise. What was typed in a hidden section is kept and still saved.
const chosen = (name) => [form[name]?.on_mechanism, form[name]?.off_mechanism]
const usesWol = (name) => chosen(name).some(m => m === 'auto' || m === 'wol')
const usesSerial = (name) => chosen(name).some(m => m === 'auto' || m === 'serial')
const usesGpio = (name) => chosen(name).some(m => m === 'auto' || m === 'gpio')
const usesCec = (name) => chosen(name).some(m => m === 'auto' || m === 'cec')

function cecItems(screen) {
  return [{title: 'Automatic (wired to this output)', value: 'auto'},
    ...screen.cec_devices.map(p => ({title: p, value: p}))]
}

const gpioModes = [{title: 'Level (relay on the supply)', value: 'level'}, {title: 'Pulse (power button)', value: 'pulse'}]
const gpioChips = ref([])
const gpioChipsInfo = ref([])
// Pushed by the node when asked: the chips of this machine and the name of each line, so the installer sees what a number is
api.Settings.Display.Screens.gpio_lines.on(data => {
  gpioChipsInfo.value = data.chips
  gpioChips.value = data.chips.filter(c => c.name).map(c => ({title: `${c.name} (${c.label})`, value: c.name}))
})
if (api.Settings.Display.Screens.gpio_lines) api.Settings.Display.Screens.gpio_lines()
function gpioLineName(f) {
  const chip = gpioChipsInfo.value.find(c => c.name === f?.gpio_chip)
  const line = chip?.lines.find(l => l.offset === f.gpio_line)
  if (!line) return ''
  return [line.name, line.used_by && `used by ${line.used_by}`].filter(Boolean).join(', ')
}

function fillForm(screen) {
  const c = screen.config
  form[screen.name] = {
    on_mechanism: c.on_mechanism, off_mechanism: c.off_mechanism, power_on_at_start: c.power_on_at_start,
    wol_mac: c.wol?.mac ?? '', wol_broadcast: c.wol?.broadcast ?? '255.255.255.255',
    wol_interface: c.wol?.interface ?? '', wol_repeat: c.wol?.repeat ?? 10,
    serial_port: c.serial?.port ?? '', serial_baud: c.serial?.baud ?? 9600,
    serial_on: c.serial?.on_command ?? '', serial_off: c.serial?.off_command ?? '',
    gpio_chip: c.gpio?.chip ?? 'gpiochip0', gpio_line: c.gpio?.line ?? null, gpio_mode: c.gpio?.mode ?? 'level',
    gpio_active_low: c.gpio?.active_low ?? false, gpio_pulse_ms: c.gpio?.pulse_ms ?? 500,
    cec_device: c.cec_device ?? 'auto',
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
  if (f.wol_mac.trim()) args.wol = {mac: f.wol_mac.trim(), broadcast: f.wol_broadcast.trim() || '255.255.255.255',
    interface: f.wol_interface.trim() || null, repeat: f.wol_repeat || 10}
  else args.clear_wol = true
  if (f.serial_port.trim()) {
    args.serial = {port: f.serial_port.trim(), baud: f.serial_baud, on_command: f.serial_on, off_command: f.serial_off}
  } else args.clear_serial = true
  if (f.gpio_line !== null && f.gpio_line !== '') {
    args.gpio = {chip: f.gpio_chip, line: f.gpio_line, mode: f.gpio_mode, active_low: f.gpio_active_low, pulse_ms: f.gpio_pulse_ms || 500}
  } else args.clear_gpio = true
  if (f.cec_device && f.cec_device !== 'auto') args.cec_device = f.cec_device
  else args.clear_cec = true
  api.Settings.Display.Screens.configure(args)
}

watch(screens, () => {}, {deep: false})
</script>
