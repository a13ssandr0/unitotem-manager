/**
 * Client side of the websocket API.
 *
 * The protocol is deliberately id-less: a command is fire-and-forget, and whatever changes is pushed by
 * the server to every connected client (broadcast), including the one that asked. State therefore flows
 * in one direction only, server -> all clients, and two people looking at the same page always agree.
 *
 * - `api.Settings.Update.check({...})` sends the command `Settings/Update/check`; the path is whatever
 *   the server exports, no function has to be declared here.
 * - `useApiState(target)` is a ref holding the last message received for `target`, requested again after
 *   every reconnection.
 * - `onApi(target, callback)` runs a callback for each message of `target`.
 *
 * Both composables clean up after themselves when the component goes away, so several pages (or windows)
 * can listen at the same time, unlike the old single `window.onWSMessage`.
 */
import {onUnmounted, ref} from 'vue'

const listeners = new Map() // target -> Set<callback>
const initCommands = new Map() // command -> number of components that asked for it

let sendFn = () => {}
let readyFn = () => false

/** Called once by the component that owns the socket. */
export function attach(send, ready) {
  sendFn = send
  readyFn = ready
}

/** Called by the owner of the socket for every message received. */
export function dispatch(data) {
  listeners.get(data.target)?.forEach(callback => callback(data))
}

/** Commands to (re)send whenever the socket opens. */
export function initialCommands() {
  return [...initCommands.keys()]
}

export function send(target, args) {
  sendFn(target, args === undefined ? {} : {...args})
}

function subscribe(target, callback, init) {
  if (!listeners.has(target)) listeners.set(target, new Set())
  listeners.get(target).add(callback)
  for (const command of init) {
    initCommands.set(command, (initCommands.get(command) ?? 0) + 1)
    if (readyFn()) sendFn(command, {})
  }
  return () => {
    listeners.get(target)?.delete(callback)
    for (const command of init) {
      const left = (initCommands.get(command) ?? 1) - 1
      if (left <= 0) initCommands.delete(command)
      else initCommands.set(command, left)
    }
  }
}

/**
 * Runs `callback(data)` for each message of `target` while the calling component is mounted.
 * `init` lists the commands to send at mount time and after every reconnection (default: none).
 */
export function onApi(target, callback, {init = []} = {}) {
  onUnmounted(subscribe(target, callback, init))
}

/**
 * A ref with the last message of `target` (or of its `pick` field). The target is also the command that
 * asks for the current value, unless `init` says otherwise.
 */
export function useApiState(target, {pick = null, init = [target]} = {}) {
  const state = ref(null)
  onApi(target, data => {
    state.value = pick ? data[pick] : data
  }, {init})
  return state
}

function proxy(path) {
  return new Proxy(function() {}, {
    get: (_, key) => (typeof key === 'symbol' || key === 'then' ? undefined : proxy([...path, key])),
    apply: (_, __, [args]) => send(path.join('/'), args),
  })
}

/** `api.Settings.Update.check()` === send('Settings/Update/check'). */
export const api = proxy([])
