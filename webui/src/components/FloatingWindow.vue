<template>
  <Teleport to="body">
    <div v-if="modelValue" ref="win" class="floating-window elevation-12" :class="{maximized, minimized}" :style="style" @mousedown="raise">
      <div class="floating-title d-flex align-center" @pointerdown="startDrag" @dblclick="toggleMaximized">
        <v-icon v-if="icon" class="floating-title-icon">{{ icon }}</v-icon>
        <span class="floating-title-text text-subtitle-2 text-truncate">{{ title }}</span>
        <v-spacer></v-spacer>
        <button type="button" class="fw-btn" title="Minimise" @click.stop="minimize" @dblclick.stop>
          <v-icon>mdi-window-minimize</v-icon>
        </button>
        <button type="button" class="fw-btn" :title="maximized ? 'Restore' : 'Maximise'" @click.stop="toggleMaximized" @dblclick.stop>
          <v-icon>{{ maximized ? 'mdi-window-restore' : 'mdi-window-maximize' }}</v-icon>
        </button>
        <button type="button" class="fw-btn fw-btn-close" title="Close" @click.stop="$emit('update:modelValue', false)" @dblclick.stop>
          <v-icon>mdi-window-close</v-icon>
        </button>
      </div>
      <div class="floating-body">
        <slot></slot>
      </div>
      <template v-if="!maximized">
        <div v-for="dir in HANDLES" :key="dir" class="resize-handle" :class="'resize-' + dir" :data-dir="dir"
             @pointerdown.stop.prevent="startResize($event, dir)"></div>
      </template>
    </div>
  </Teleport>
</template>

<script setup>
/**
 * A window that floats over the page, not a dialog: it does not dim or block what is behind it, can be dragged by
 * its title bar, resized from any edge or corner, maximised, minimised to the taskbar and closed. Several can be open at once and the
 * one clicked comes to the front. It is the building block of the desktop-style interface (see TODO).
 */
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {newWindowId, windows} from '@/windows'

const props = defineProps({
  modelValue: Boolean,
  title: String,
  icon: String,
  width: {type: Number, default: 640},
  height: {type: Number, default: 420},
})
defineEmits(['update:modelValue'])

let nextZ = (window.__floatingZ ??= 1500)
const z = ref(nextZ)
const x = ref(Math.max(8, (innerWidth - props.width) / 2 + (nextZ % 7) * 18))
const y = ref(Math.max(8, 72 + (nextZ % 5) * 18))
const win = ref(null)

// Position and size are state of their own: the user moves and resizes the window, so nothing is rebuilt from the
// initial values.
const w = ref(props.width)
const h = ref(props.height)

const maximized = ref(false)
let restoreTo = null
const area = ref({x: 0, y: 0, w: innerWidth, h: innerHeight})

// Maximised means filling the page below the app bar, so the navigation stays reachable
function measureArea() {
  const top = document.querySelector('.v-app-bar')?.getBoundingClientRect().bottom ?? 0
  area.value = {x: 0, y: top, w: innerWidth, h: innerHeight - top}
}

function toggleMaximized() {
  if (maximized.value) {
    maximized.value = false
    if (restoreTo) ({x: x.value, y: y.value, w: w.value, h: h.value} = restoreTo)
  } else {
    restoreTo = {x: x.value, y: y.value, w: w.value, h: h.value}
    measureArea()
    maximized.value = true
  }
  raise()
}

const style = computed(() => maximized.value ? {
  left: area.value.x + 'px', top: area.value.y + 'px', zIndex: z.value,
  width: area.value.w + 'px', height: area.value.h + 'px',
} : {
  left: x.value + 'px', top: y.value + 'px', zIndex: z.value,
  width: w.value + 'px', height: h.value + 'px',
})

window.addEventListener('resize', onPageResize)
function onPageResize() {
  if (maximized.value) measureArea()
}

const MIN_W = 280
const MIN_H = 120
const HANDLES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']

// Registered with the taskbar while open. Minimising hides the window (it keeps its size and its content, a terminal
// keeps its session) and the taskbar button brings it back.
const id = newWindowId()
const minimized = computed(() => windows[id]?.minimized ?? false)

function raise() {
  z.value = ++window.__floatingZ
  if (windows[id]) windows[id].z = z.value
}

function minimize() {
  if (windows[id]) windows[id].minimized = true
}

watch(() => props.modelValue, (open) => {
  if (open) {
    windows[id] = {title: props.title, icon: props.icon, minimized: false, z: z.value, raise}
  } else {
    delete windows[id]
  }
}, {immediate: true})
watch(() => [props.title, props.icon], ([title, icon]) => {
  if (windows[id]) Object.assign(windows[id], {title, icon})
})

let resizing = null
function startResize(e, dir) {
  raise()
  resizing = {dir, px: e.clientX, py: e.clientY, x: x.value, y: y.value, w: w.value, h: h.value}
  window.addEventListener('pointermove', onResize)
  window.addEventListener('pointerup', stopResize, {once: true})
}
function onResize(e) {
  if (!resizing) return
  const r = resizing
  const dx = e.clientX - r.px
  const dy = e.clientY - r.py
  let nx = r.x, ny = r.y, nw = r.w, nh = r.h
  if (r.dir.includes('e')) nw = Math.max(MIN_W, r.w + dx)
  if (r.dir.includes('s')) nh = Math.max(MIN_H, r.h + dy)
  // from the west and north edges the window grows towards the pointer, so its origin moves with it
  if (r.dir.includes('w')) { nw = Math.max(MIN_W, r.w - dx); nx = r.x + (r.w - nw) }
  if (r.dir.includes('n')) { nh = Math.max(MIN_H, r.h - dy); ny = Math.max(0, r.y + (r.h - nh)); nh = r.h + (r.y - ny) }
  x.value = nx; y.value = ny; w.value = nw; h.value = nh
}
function stopResize() {
  resizing = null
  window.removeEventListener('pointermove', onResize)
}

let drag = null
function startDrag(e) {
  if (e.target.closest('button') || maximized.value) return
  raise()
  drag = {dx: e.clientX - x.value, dy: e.clientY - y.value}
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', stopDrag, {once: true})
}
function onDrag(e) {
  if (!drag) return
  // Keep the title bar reachable: a window dragged off the screen could not be brought back
  x.value = Math.min(innerWidth - 80, Math.max(-props.width + 120, e.clientX - drag.dx))
  y.value = Math.min(innerHeight - 40, Math.max(0, e.clientY - drag.dy))
}
function stopDrag() {
  drag = null
  window.removeEventListener('pointermove', onDrag)
}
onBeforeUnmount(() => {
  delete windows[id]
  stopDrag()
  stopResize()
  window.removeEventListener('resize', onPageResize)
})
</script>

<style scoped>
.floating-window {
  position: fixed;
  display: flex;
  flex-direction: column;
  min-width: 280px;
  min-height: 40px;
  overflow: visible;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}
/* The same material language as the app bar: the theme's surface and text colours, nothing coloured */
.floating-title {
  height: 32px;
  flex: none;
  cursor: move;
  user-select: none;
  touch-action: none;
  padding-left: 10px;
  padding-right: 4px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px 4px 0 0;
}
.floating-title-icon {
  font-size: 16px !important;
  margin-right: 8px;
  color: inherit;
  opacity: 0.7;
}
.minimized {
  visibility: hidden;
  pointer-events: none;
}
.maximized .floating-title,
.maximized {
  border-radius: 0;
}

/* Plain buttons, not v-btn: the global rule on `.v-btn--icon .v-icon` makes the icon of every icon button huge.
   24 px with a 16 px glyph, about what a KDE title bar button is. */
.fw-btn {
  width: 24px;
  height: 24px;
  margin-left: 2px;
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
.fw-btn .v-icon {
  font-size: 16px !important;
  width: 16px;
  height: 16px;
}
.fw-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.12);
}
.fw-btn-close:hover {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
}
.floating-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-radius: 0 0 4px 4px;
}
</style>
