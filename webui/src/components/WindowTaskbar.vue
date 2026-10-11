<template>
  <div v-if="entries.length" class="window-taskbar">
    <button v-for="[id, w] in entries" :key="id" type="button" class="taskbar-item"
            :class="{minimized: w.minimized}" :title="w.title" @click="toggleWindow(id, frontId)">
      <v-icon v-if="w.icon" class="taskbar-icon">{{ w.icon }}</v-icon>
      <span class="text-truncate">{{ w.title }}</span>
    </button>
  </div>
</template>

<script setup>
/** The strip at the bottom of the page: one button per open floating window, minimised ones included. */
import {computed, watch} from 'vue'
import {toggleWindow, windows} from '@/windows'

const entries = computed(() => Object.entries(windows))
// Page elements anchored to the bottom (the floating action button) move up by the height of the bar
watch(() => entries.value.length > 0, (visible) => {
  document.documentElement.style.setProperty('--taskbar-height', visible ? '32px' : '0px')
}, {immediate: true})
// The window in front is the one with the highest z-index that is not minimised
const frontId = computed(() => {
  let best = null
  let bestZ = -1
  for (const [id, w] of entries.value) {
    if (!w.minimized && (w.z ?? 0) > bestZ) {
      best = id
      bestZ = w.z ?? 0
    }
  }
  return best
})
</script>

<style scoped>
.window-taskbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  height: 32px;
  z-index: 3000;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.taskbar-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 200px;
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.12);
  color: inherit;
  font-size: 0.8125rem;
  cursor: pointer;
}
.taskbar-item.minimized {
  background: transparent;
  opacity: 0.8;
}
.taskbar-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.2);
}
.taskbar-icon {
  font-size: 16px !important;
}
</style>
