<!--
  The tree of panes of a window, drawn the way the window will be: a leaf is a pane, a split lays its two halves side by
  side ('h') or stacked ('v'), and the gutter between them can be dragged. Used inside the preview of WindowLayout.vue; it
  only draws and reports: `ratio` is emitted with the path of the split (see Viewers/set_split_ratio) and the new share of
  its first half while the gutter moves, and once more with final=true when it is let go.
-->
<template>
  <div v-if="node.kind === 'leaf'" class="leaf" :class="{main: node.id === main}" :data-pane="node.id">
    <span class="leaf-id">{{ node.id }}</span>
    <span class="leaf-name">{{ labels[node.id] || '-' }}</span>
  </div>
  <div v-else ref="el" class="split" :class="node.dir" :data-path="path">
    <div class="half" :style="{flex: `${node.ratio} 1 0`}">
      <PaneTree :node="node.a" :path="path + 'a'" :main="main" :labels="labels" :editable="editable" :mirror="mirror"
                @ratio="(...args) => emit('ratio', ...args)"/>
    </div>
    <div class="gutter" :class="[node.dir, {editable}]" :data-gutter="path"
         @pointerdown.stop.prevent="editable && start($event)"/>
    <div class="half" :style="{flex: `${1 - node.ratio} 1 0`}">
      <PaneTree :node="node.b" :path="path + 'b'" :main="main" :labels="labels" :editable="editable" :mirror="mirror"
                @ratio="(...args) => emit('ratio', ...args)"/>
    </div>
  </div>
</template>

<script setup>
import {ref} from 'vue'

const props = defineProps({
  node: {type: Object, required: true},
  path: {type: String, default: ''},
  main: {type: Number, default: 1},
  labels: {type: Object, default: () => ({})},    // pane id -> text shown in it
  editable: {type: Boolean, default: false},
  mirror: {type: String, default: ''},          // 'x' | 'y' | '': the preview is mirrored (the window's content is flipped)
})
const emit = defineEmits(['ratio'])
const el = ref(null)

function ratioAt(event) {
  const box = el.value.getBoundingClientRect()
  let share = props.node.dir === 'h' ? (event.clientX - box.left) / box.width : (event.clientY - box.top) / box.height
  if ((props.node.dir === 'h' && props.mirror === 'x') || (props.node.dir === 'v' && props.mirror === 'y')) share = 1 - share
  return Math.min(0.9, Math.max(0.1, Math.round(share * 1000) / 1000))
}

function start(event) {
  const target = event.currentTarget
  target.setPointerCapture(event.pointerId)
  const move = e => emit('ratio', props.path, ratioAt(e), false)
  const end = e => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', end)
    window.removeEventListener('pointercancel', end)
    emit('ratio', props.path, ratioAt(e), true)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', end)
  window.addEventListener('pointercancel', end)
}
</script>

<style scoped>
.leaf, .split { width: 100%; height: 100%; }
.leaf {
  display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden;
  background: rgba(var(--v-theme-primary), 0.18);
  outline: 1px solid rgba(var(--v-theme-primary), 0.6); outline-offset: -1px;
  font-size: 12px; line-height: 1.2; text-align: center; padding: 2px;
}
.leaf.main { background: rgba(var(--v-theme-primary), 0.3); }
.leaf-id { font-weight: 700; font-size: 16px; }
.leaf-name { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 0.85; }
.split { display: flex; }
.split.v { flex-direction: column; }
.half { min-width: 0; min-height: 0; overflow: hidden; }
.gutter { flex: 0 0 6px; background: rgba(var(--v-theme-on-surface), 0.25); touch-action: none; }
.gutter.h { cursor: col-resize; }
.gutter.v { cursor: row-resize; }
.gutter.editable:hover { background: rgb(var(--v-theme-primary)); }
</style>
