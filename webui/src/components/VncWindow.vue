<template>
  <FloatingWindow v-model="open" :title="title" icon="mdi-monitor-eye" :width="900" :height="560">
    <template #titlebar>
      <v-menu location="bottom end">
        <template #activator="{props}">
          <button v-bind="props" type="button" class="vnc-pick" title="Which screen to show" :disabled="!available">
            <v-icon>mdi-monitor</v-icon><span class="text-truncate">{{ outputLabel }}</span><v-icon>mdi-menu-down</v-icon>
          </button>
        </template>
        <v-list density="compact" :selected="[output]">
          <v-list-item v-for="item in outputItems" :key="item.value" :value="item.value" :title="item.title"
                       @click="chooseOutput(item.value)"></v-list-item>
        </v-list>
      </v-menu>
      <button type="button" class="vnc-btn vnc-control" :class="{on: control}" :disabled="!available"
              :title="control ? 'Control is ON: click to go back to view only' : 'View only: click to take control'"
              @click="toggleControl">
        <v-icon>{{ control ? 'mdi-cursor-default-click' : 'mdi-eye' }}</v-icon>
      </button>
      <button type="button" class="vnc-btn" title="Reconnect" :disabled="!available" @click="reconnect">
        <v-icon>mdi-reload</v-icon>
      </button>
    </template>
    <div class="vnc-root">
      <div class="vnc-screen" :class="{viewonly: !control}" @pointermove="activity" @pointerdown="activity" @keydown="activity" @wheel="activity">
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
const outputLabel = computed(() => outputItems.value.find(i => i.value === output.value)?.title ?? output.value)
const title = computed(() => `Screen · ${statusText.value}`)
function chooseOutput(name) {
  if (name === output.value) return
  output.value = name
  reconnect()
}
function toggleControl() {
  control.value = !control.value
  reconnect()
}
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
.vnc-btn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0;
}
.vnc-btn:hover { background: rgba(var(--v-theme-on-surface), 0.12); }
.vnc-btn .v-icon { font-size: 16px !important; width: 16px; height: 16px; }
.vnc-pick {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  max-width: 220px;
  height: 24px;
  padding: 0 4px 0 6px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: inherit;
  font-size: 12px;
  cursor: pointer;
}
.vnc-pick:hover { background: rgba(var(--v-theme-on-surface), 0.12); }
.vnc-pick .v-icon { font-size: 16px !important; width: 16px; height: 16px; }
.vnc-control.on { background: rgb(var(--v-theme-warning)); color: rgb(var(--v-theme-on-warning)); }
.vnc-btn:disabled, .vnc-pick:disabled { opacity: 0.4; cursor: default; }
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
/* View only: noVNC draws the remote pointer itself and hides the local one, which is nothing at all when the server sends no
   cursor image: the pointer must stay visible over the screen */
.vnc-screen.viewonly :deep(canvas) {
  cursor: default !important;
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
