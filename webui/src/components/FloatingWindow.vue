<template>
  <Teleport to="body">
    <div v-if="modelValue" ref="win" class="floating-window elevation-12" :style="style" @mousedown="raise">
      <div class="floating-title d-flex align-center px-3" @pointerdown="startDrag">
        <v-icon v-if="icon" size="small" class="me-2">{{ icon }}</v-icon>
        <span class="text-subtitle-2 text-truncate">{{ title }}</span>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-window-minimize" size="x-small" variant="text" title="Minimise" @click.stop="collapsed = !collapsed"></v-btn>
        <v-btn icon="mdi-close" size="x-small" variant="text" title="Close" @click.stop="$emit('update:modelValue', false)"></v-btn>
      </div>
      <div v-show="!collapsed" class="floating-body">
        <slot></slot>
      </div>
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

const style = computed(() => ({
  left: x.value + 'px', top: y.value + 'px', zIndex: z.value,
  width: props.width + 'px', height: collapsed.value ? 'auto' : props.height + 'px',
}))

function raise() {
  z.value = ++window.__floatingZ
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
onBeforeUnmount(stopDrag)
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
  resize: both;
  overflow: hidden;
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
}
.floating-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>
