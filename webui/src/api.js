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
import {onUnmounted, ref, shallowRef} from 'vue'

const listeners = new Map() // target -> Set<callback>
const openListeners = new Set()
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

/** Called by the owner of the socket each time it opens, after the initial commands have been sent. */
export function notifyOpen() {
  openListeners.forEach(callback => callback())
}

/**
 * Runs `callback` now (if the socket is open) and every time it reopens: for state that cannot be asked
 * with a plain initial command, because the request depends on what the page is showing.
 */
export function onOpen(callback) {
  openListeners.add(callback)
  if (readyFn()) callback()
  onUnmounted(() => openListeners.delete(callback))
}

/** Commands to (re)send whenever the socket opens. */
export function initialCommands() {
  return [...initCommands.keys()]
}

let schema = null
/** The schema as a ref, for what depends on it in the UI (see `canCall`). */
export const apiSchema = shallowRef(null)

/**
 * Fetches the server's description of the API. It is only used to catch mistakes early (a path that does
 * not exist, an argument the function does not take) with a console error; calls are never blocked.
 */
export async function loadSchema() {
  try {
    const response = await fetch('/api/schema', {credentials: 'include'})
    if (response.ok) {
      schema = await response.json()
      apiSchema.value = schema
    }
  } catch (e) {
    console.warn('API schema not available', e)
  }
}

function check(target, args) {
  if (!schema) return
  const entry = schema[target]
  if (!entry) {
    console.error(`Unknown API function: ${target}`)
    return
  }
  const known = entry.params?.properties ?? {}
  for (const name of Object.keys(args)) {
    if (!(name in known)) console.error(`${target} takes no argument named "${name}"`)
  }
  for (const name of entry.params?.required ?? []) {
    if (!(name in args)) console.error(`${target} needs the argument "${name}"`)
  }
}

export function send(target, args) {
  const payload = args === undefined ? {} : {...args}
  check(target, payload)
  sendFn(target, payload)
}

function subscribe(target, callback, init) {
  if (!listeners.has(target)) listeners.set(target, new Set())
  listeners.get(target).add(callback)
  for (const command of init) {
    initCommands.set(command, (initCommands.get(command) ?? 0) + 1)
    if (readyFn()) send(command, {})
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
export function useApiState(target, {pick = null, init = [target], initial = null} = {}) {
  const state = ref(initial)
  onApi(target, data => {
    state.value = pick ? data[pick] : data
  }, {init})
  return state
}

function proxy(path) {
  const target = path.join('/')
  return new Proxy(function() {}, {
    get: (_, key) => {
      if (typeof key === 'symbol' || key === 'then') return undefined
      // `api.Settings.Update.status.on(cb)` / `.use()` listen to the messages of that function
      if (key === 'on') return (callback, {init = false} = {}) => onApi(target, callback, {init: init ? [target] : []})
      if (key === 'use') return ({pick = null, init = true, initial = null} = {}) => useApiState(target, {pick, init: init ? [target] : [], initial})
      return proxy([...path, key])
    },
    apply: (_, __, [args]) => send(target, args),
  })
}

/**
 * `api.Settings.Update.check()` === send('Settings/Update/check'). The tree is not declared anywhere: the
 * server describes it (GET /api/schema) and tools/gen-api-types.py turns that into api.d.ts for editors.
 * @type {import('./api').Api}
 */
export const api = proxy([])

/**
 * Whether a user may call an API function, from the permissions the server declares for it. Meant for
 * hiding what a user cannot use; the server checks every call anyway. While the schema is not loaded yet
 * everything is allowed, so the menu does not flicker.
 */
export function canCall(path, user) {
  const entry = apiSchema.value?.[path]
  if (!entry) return true
  if (entry.permissions === null) return true
  if (!user?.perms) return true // the user is not known yet
  const perms = user.perms
  return perms.includes('admin') || entry.permissions.some(p => perms.includes(p))
}
