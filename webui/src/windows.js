/**
 * Registry of the open floating windows, shared by the windows themselves and the taskbar at the bottom of the page.
 * A minimised window is not destroyed (a terminal keeps its session): it is hidden and listed in the taskbar.
 */
import {reactive} from 'vue'

export const windows = reactive({})   // id -> {title, icon, minimized, raise}

let counter = 0
export const newWindowId = () => 'window-' + (++counter)

/** Taskbar click: bring a minimised window back, minimise one that is in front, raise one that is behind. */
export function toggleWindow(id, frontId) {
  const w = windows[id]
  if (!w) return
  if (w.minimized) {
    w.minimized = false
    w.raise?.()
  } else if (frontId === id) {
    w.minimized = true
  } else {
    w.raise?.()
  }
}
