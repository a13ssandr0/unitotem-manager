<template>
  <Teleport to="body">
    <TransitionGroup name="toast" tag="div" class="notification-toasts">
      <div v-for="t in shown" :key="t.id" class="notification-toast elevation-8" :data-level="t.n.level" role="status"
           @click="activate(t.n, router, () => close(t.id))" @mouseenter="hold(t.id)" @mouseleave="release(t.id)">
        <v-icon :color="levelOf(t.n).color" class="mr-3">{{ levelOf(t.n).icon }}</v-icon>
        <div class="toast-text">
          <div class="text-subtitle-2 text-wrap">{{ t.n.title }}</div>
          <div v-if="t.n.text" class="text-body-2 text-medium-emphasis text-wrap">{{ t.n.text }}</div>
          <v-progress-linear v-if="t.n.progress != null" class="mt-1" height="6" rounded color="primary"
                             :indeterminate="t.n.progress < 0" :model-value="Math.max(t.n.progress, 0)"></v-progress-linear>
          <div v-if="t.n.actions.length" class="mt-1">
            <v-btn v-for="a in t.n.actions" :key="a.id" size="small" variant="text" color="primary"
                   @click.stop="runAction(t.n, a); close(t.id)">{{ a.label || a.id }}</v-btn>
          </div>
        </div>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<script setup>
/**
 * Pop-ups for notifications that arrive while the page is open (the freedesktop notification model: shown for `timeout` ms, 0 = until
 * clicked, critical ones never expire; pausing while the pointer is over one). A click does what a click on it in the bell does. What
 * was already there when the page loaded is not shown again, and the bell keeps everything either way.
 */
import {computed, onUnmounted, ref, watch} from 'vue'
import {useRouter} from 'vue-router'
import {api} from '@/api'
import {activate, runAction} from '@/notifications'

const MAX_SHOWN = 4
const router = useRouter()
const items = api.Notifications.list.use({pick: 'notifications', initial: null})
const toasts = ref([])             // {id, n}: n is refreshed from the list while it is shown
const timers = new Map()           // id -> {handle, left, since}
const seen = new Map()             // id -> {title, updated}
let baseline = false

const LEVELS = {
  info: {icon: 'mdi-information', color: 'info'},
  warning: {icon: 'mdi-alert', color: 'warning'},
  error: {icon: 'mdi-alert-circle', color: 'error'},
}
const levelOf = n => LEVELS[n.level] ?? LEVELS.info

const byId = computed(() => new Map((items.value ?? []).map(n => [n.id, n])))
const shown = computed(() => toasts.value.map(t => ({id: t.id, n: byId.value.get(t.id) ?? t.n})))

function close(id) {
  const timer = timers.get(id)
  if (timer) clearTimeout(timer.handle)
  timers.delete(id)
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function arm(id, ms) {
  const timer = {left: ms, since: Date.now(), handle: setTimeout(() => close(id), ms)}
  timers.set(id, timer)
}
function hold(id) {
  const timer = timers.get(id)
  if (!timer) return
  clearTimeout(timer.handle)
  timer.left -= Date.now() - timer.since
}
function release(id) {
  const timer = timers.get(id)
  if (timer) arm(id, Math.max(timer.left, 1500))
}

function show(n) {
  if (!toasts.value.some(t => t.id === n.id)) toasts.value = [...toasts.value, {id: n.id, n}].slice(-MAX_SHOWN)
  const timer = timers.get(n.id)
  if (timer) clearTimeout(timer.handle)
  timers.delete(n.id)
  if (n.timeout > 0) arm(n.id, n.timeout)          // 0: until it is clicked
}

watch(items, (list) => {
  if (!list) return
  const ids = new Set(list.map(n => n.id))
  for (const n of list) {
    const before = seen.get(n.id)
    // new, or changed in what it says (not a progress bar moving, and not something that was only marked read)
    if (baseline && !n.read && (!before || before.title !== n.title || (n.progress == null && n.updated > before.updated))) show(n)
    seen.set(n.id, {title: n.title, updated: n.updated})
  }
  baseline = true
  for (const id of [...seen.keys()]) if (!ids.has(id)) { seen.delete(id); close(id) }
  // read or dismissed elsewhere: the pop-up goes
  for (const t of toasts.value) if (byId.value.get(t.id)?.read) close(t.id)
}, {immediate: true})

onUnmounted(() => timers.forEach(t => clearTimeout(t.handle)))
</script>

<style scoped>
.notification-toasts {
  position: fixed;
  top: 72px;
  right: 16px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(380px, calc(100vw - 32px));
  pointer-events: none;
}
.notification-toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.toast-text { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.toast-enter-active, .toast-leave-active { transition: all 0.25s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(24px); }
</style>
