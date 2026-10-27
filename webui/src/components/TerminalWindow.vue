<template>
  <FloatingWindow v-model="open" :title="title" icon="mdi-console" :width="760" :height="460">
    <div ref="host" class="terminal-host"></div>
  </FloatingWindow>
</template>

<script setup>
/**
 * A terminal in a floating window, over the /terminal websocket: the shell of the web user's own account, no second
 * login. A one-time token (POST /terminal/token, needs the session) is the first frame of the websocket. The connection
 * exists only while the window is open.
 */
import {nextTick, onBeforeUnmount, ref, watch} from 'vue'
import {Terminal} from '@xterm/xterm'
import {FitAddon} from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import FloatingWindow from '@/components/FloatingWindow.vue'

const open = defineModel({type: Boolean, default: false})
const host = ref(null)
// The title is whatever the shell asks for (user@host: directory), like any terminal emulator
const title = ref('Terminal')

let term = null
let fit = null
let socket = null
let observer = null

const encoder = new TextEncoder()

function sendSize() {
  if (socket?.readyState === WebSocket.OPEN && term) {
    socket.send(JSON.stringify({resize: [term.cols, term.rows]}))
  }
}

async function connect() {
  term = new Terminal({cursorBlink: true, fontSize: 14, convertEol: false, scrollback: 2000,
    theme: {background: '#000000'}})
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(host.value)
  term.onTitleChange(t => { title.value = t || 'Terminal' })
  title.value = 'Terminal'
  fit.fit()

  let token
  try {
    const reply = await fetch('/terminal/token', {method: 'POST', credentials: 'same-origin'})
    if (!reply.ok) throw new Error((await reply.json().catch(() => ({}))).detail || reply.statusText)
    token = (await reply.json()).token
  } catch (e) {
    term.write(`\x1b[2m[cannot open the terminal: ${e.message}]\x1b[0m\r\n`)
    return
  }
  if (!term) return          // the window was closed meanwhile

  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/terminal`)
  socket.binaryType = 'arraybuffer'
  socket.onopen = () => { socket.send(JSON.stringify({token})); sendSize(); term.focus() }
  socket.onmessage = (e) => term.write(typeof e.data === 'string' ? e.data : new Uint8Array(e.data))
  socket.onclose = (e) => {
    // The shell ended (logout, Ctrl+D, exit): there is nothing left to show, the window goes with it
    if (e.reason === 'Session ended') {
      open.value = false
      return
    }
    term?.write(`\r\n\x1b[2m[connection closed${e.reason ? ': ' + e.reason : ''}]\x1b[0m\r\n`)
  }
  term.onData(data => { if (socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data)) })
  term.onResize(sendSize)

  // The window can be resized by the user: refit the terminal to it
  observer = new ResizeObserver(() => {
    // Never fit to a box that is (nearly) empty: that shrinks the terminal to a couple of columns and the text is lost
    if (host.value && host.value.clientWidth > 100 && host.value.clientHeight > 50) {
      try { fit.fit() } catch { /* not laid out yet */ }
    }
  })
  observer.observe(host.value)
}

function disconnect() {
  observer?.disconnect()
  observer = null
  socket?.close()
  socket = null
  term?.dispose()
  term = null
}

watch(open, async (isOpen) => {
  if (isOpen) {
    await nextTick()
    connect()
  } else {
    disconnect()
  }
})

onBeforeUnmount(disconnect)
</script>

<style scoped>
.terminal-host {
  width: 100%;
  height: 100%;
  background: #000;
  padding: 4px;
  box-sizing: border-box;
}
</style>
