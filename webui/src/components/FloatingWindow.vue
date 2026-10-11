<template>
  <Teleport to="body">
    <div v-if="modelValue" ref="win" class="floating-window elevation-12" :style="style" @mousedown="raise">
      <div class="floating-title d-flex align-center px-3" @pointerdown="startDrag">
        <v-icon v-if="icon" :size="16" class="me-2">{{ icon }}</v-icon>
        <span class="floating-title-text text-truncate">{{ title }}</span>
        <v-spacer></v-spacer>
        <v-btn class="floating-btn" icon variant="text" density="compact" :size="24" title="Minimise"
               @click.stop="collapsed = !collapsed"><v-icon :size="16">mdi-window-minimize</v-icon></v-btn>
        <v-btn class="floating-btn" icon variant="text" density="compact" :size="24" title="Close"
               @click.stop="$emit('update:modelValue', false)"><v-icon :size="16">mdi-close</v-icon></v-btn>
      </div>
      <div v-show="!collapsed" class="floating-body">
        <slot></slot>
      </div>
      <template v-if="!collapsed">
        <div v-for="dir in HANDLES" :key="dir" class="resize-handle" :class="'resize-' + dir" :data-dir="dir"
             @pointerdown.stop.prevent="startResize($event, dir)"></div>
      </template>
    </div>
  </Teleport>
</template>

<script setup>
/**
 * A window that floats over the page, not a dialog: it does not dim or block what is behind it, can be dragged by
 * its title bar, resized from the corner, collapsed to the title bar and closed. Several can be open at once and the
 * one clicked comes to the front. It is the building block of the desktop-style interface (see TODO).
 */
import {computed, onBeforeUnmount, ref} from 'vue'

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
const collapsed = ref(false)
const win = ref(null)

// Position and size are state of their own: the user moves and resizes the window, so nothing is rebuilt from the
// initial values.
const w = ref(props.width)
const h = ref(props.height)

const style = computed(() => ({
  left: x.value + 'px', top: y.value + 'px', zIndex: z.value,
  width: w.value + 'px', height: collapsed.value ? 'auto' : h.value + 'px',
}))

const MIN_W = 280
const MIN_H = 120
const HANDLES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']

function raise() {
  z.value = ++window.__floatingZ
}

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
  if (e.target.closest('button')) return
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
  stopDrag()
  stopResize()
})
</script>

<style scoped>
.floating-window {
  position: fixed;
  display: flex;
  flex-direction: column;
  min-width: 280px;
  min-height: 40px;
  max-width: 95vw;
  max-height: 90vh;
  overflow: visible;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}
.floating-title {
  height: 36px;
  flex: none;
  cursor: move;
  user-select: none;
  touch-action: none;
  background: rgb(var(--v-theme-surface-variant));
  color: rgb(var(--v-theme-on-surface-variant));
  border-radius: 8px 8px 0 0;
}
/* Grab areas on every edge and corner, a few pixels wide and partly outside the window so they are easy to hit */
.resize-handle {
  position: absolute;
  z-index: 2;
  touch-action: none;
}
.resize-n, .resize-s { left: 8px; right: 8px; height: 8px; cursor: ns-resize; }
.resize-n { top: -3px; }
.resize-s { bottom: -3px; }
.resize-e, .resize-w { top: 8px; bottom: 8px; width: 8px; cursor: ew-resize; }
.resize-e { right: -3px; }
.resize-w { left: -3px; }
.resize-ne, .resize-nw, .resize-se, .resize-sw { width: 14px; height: 14px; }
.resize-ne { top: -3px; right: -3px; cursor: nesw-resize; }
.resize-nw { top: -3px; left: -3px; cursor: nwse-resize; }
.resize-se { bottom: -3px; right: -3px; cursor: nwse-resize; }
.resize-sw { bottom: -3px; left: -3px; cursor: nesw-resize; }

.floating-title-text {
  font-size: 0.8125rem;
  font-weight: 500;
}
.floating-btn {
  margin-left: 2px;
}
.floating-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-radius: 0 0 8px 8px;
}
</style>
