<template>
  <FloatingWindow v-model="open" :title="title" icon="mdi-monitor-eye" :width="900" :height="560">
    <div class="vnc-root">
      <div class="vnc-bar d-flex align-center ga-2 px-2">
        <v-select v-model="output" :items="outputItems" density="compact" variant="outlined" hide-details
                  class="vnc-output" label="Screen" :disabled="!available" @update:model-value="reconnect"/>
        <v-switch v-model="control" color="warning" density="compact" hide-details inset label="Control"
                  :disabled="!available" @update:model-value="reconnect"/>
        <v-spacer/>
        <span class="text-caption vnc-status" :class="'vnc-' + state">{{ statusText }}</span>
        <v-btn size="small" variant="text" icon="mdi-reload" title="Reconnect" :disabled="!available"
               @click="reconnect"/>
      </div>
      <div class="vnc-screen" @pointermove="activity" @pointerdown="activity" @keydown="activity" @wheel="activity">
        <div ref="host" class="vnc-host"></div>
        <div v-if="state !== 'connected'" class="vnc-overlay text-medium-emphasis">
          <v-progress-circular v-if="state === 'connecting'" indeterminate size="28"/>
          <div v-else class="text-center px-4">{{ message }}</div>
        </div>
      </div>
    </div>
  </FloatingWindow>
</template>

<script setup>
/**
 * The screen of the totem, in a floating window: noVNC over the /vnc websocket (see manager/plugins/unitotem_node/vnc.py).
 * A VNC server exists on the node only while a session of this window is connected. It is view-only unless the
 * administrator switches Control on, and that choice is made again every time (it is never remembered): changing it
 * starts a new session, because view-only is enforced by the server and not by this page.
 *
 * Like the other windows it closes only when the user closes it: if the session ends (idle, the server died, the
 * connection dropped) the window stays and says why, with the reload button to start again.
 */
import {computed, nextTick, onBeforeUnmount, ref, watch} from 'vue'
import FloatingWindow from '@/components/FloatingWindow.vue'
import {api} from '@/api'

const open = defineModel({type: Boolean, default: false})
const host = ref(null)
const output = ref('')
const control = ref(false)
const outputs = ref([])
const available = ref(true)
const state = ref('connecting')   // connecting | connected | ended
const message = ref('')

const ACTIVITY_INTERVAL = 5000    // ms between two "the user is here" messages: the server stops an abandoned session

let rfb = null
let socket = null
let generation = 0                // a stale connection (reconnect while connecting) must not touch the state
let lastActivity = 0

const outputItems = computed(() => [
  {title: 'Whole desktop', value: ''},
  ...outputs.value.map(o => ({title: `${o.name} (${o.width}x${o.height})`, value: o.name})),
])
const title = computed(() => {
  const what = output.value || 'whole desktop'
  return `Screen: ${what}${control.value ? ' (control)' : ''}`
})
const statusText = computed(() => {
  if (state.value === 'connecting') return 'Connecting'
  if (state.value === 'connected') return control.value ? 'Controlling' : 'View only'
  return 'Disconnected'
})

api.Vnc.outputs.on(data => {
  outputs.value = data.outputs
  available.value = data.available
  if (!data.available) end('The VNC server is not installed on this node (package tigervnc-scraping-server).')
})

function activity() {
  const now = Date.now()
  if (now - lastActivity < ACTIVITY_INTERVAL || socket?.readyState !== WebSocket.OPEN) return
  lastActivity = now
  socket.send('{"activity":1}')
}

function end(text) {
  state.value = 'ended'
  message.value = text
}

async function connect() {
  const mine = ++generation
  state.value = 'connecting'
  message.value = ''
  const params = new URLSearchParams()
  if (output.value) params.set('output', output.value)
  if (control.value) params.set('control', '1')
  // A one-time token (needs the session) is the first frame of the websocket, as for the terminal
  let token
  try {
    const reply = await fetch('/vnc/token', {method: 'POST', credentials: 'same-origin'})
    if (!reply.ok) throw new Error(reply.statusText)
    token = (await reply.json()).token
  } catch (e) {
    if (mine === generation) end(`Cannot open the screen: ${e.message}`)
    return
  }
  if (mine !== generation) return
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/vnc?${params}`)
  ws.addEventListener('open', () => ws.send(JSON.stringify({token})))   // before noVNC's own handler: it is added later
  socket = ws
  // The websocket is created here, not by noVNC, to be told why the server closed it (the reason is the only place
  // where "idle" and "refused" are told apart); noVNC accepts an existing channel.
  ws.addEventListener('close', e => {
    if (mine !== generation) return
    end(e.reason || 'The connection was lost')
  })
  const {default: RFB} = await import('@novnc/novnc')
  if (mine !== generation) return
  rfb = new RFB(host.value, ws, {})
  rfb.scaleViewport = true
  rfb.resizeSession = false
  rfb.clipViewport = false
  rfb.viewOnly = !control.value
  rfb.focusOnClick = control.value
  rfb.addEventListener('connect', () => { if (mine === generation) { state.value = 'connected'; lastActivity = 0; activity() } })
  rfb.addEventListener('securityfailure', () => { if (mine === generation) end('The VNC server refused the connection') })
}

function disconnect() {
  generation++
  try { rfb?.disconnect() } catch { /* already gone */ }
  rfb = null
  try { socket?.close() } catch { /* already gone */ }
  socket = null
  if (host.value) host.value.replaceChildren()
}

function reconnect() {
  if (!open.value) return
  disconnect()
  connect()
}

watch(open, async (isOpen) => {
  if (isOpen) {
    // Always starts as a plain look at the whole screen, whatever the previous session was
    output.value = ''
    control.value = false
    await nextTick()
    api.Vnc.outputs()
    connect()
  } else {
    disconnect()
  }
})

onBeforeUnmount(disconnect)
</script>

<style scoped>
.vnc-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
}
.vnc-bar {
  flex: none;
  height: 52px;
}
.vnc-output {
  max-width: 260px;
}
.vnc-screen {
  position: relative;
  flex: 1;
  min-height: 0;
  background: #000;
}
.vnc-host {
  width: 100%;
  height: 100%;
}
.vnc-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
}
.vnc-connected {
  color: rgb(var(--v-theme-success));
}
.vnc-ended {
  color: rgb(var(--v-theme-error));
}
</style>
