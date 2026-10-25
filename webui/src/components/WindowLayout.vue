<!--
  The layout of one window: where its content is drawn (the area, a rectangle of the window, the rest being blank) and how that
  is divided into panes, each with its own playlist. Drawn as a preview of the window; the area is dragged and resized there (or
  typed in), the splitters between panes are dragged, and the panes are listed below with their playlist, split and close
  buttons. Everything the server accepts is pushed back to every page (Viewers/layout), so what is drawn is always what the
  window shows. `canEdit` is the administrator's: without it only the playlists of the panes can be changed.
-->
<template>
  <v-card variant="outlined" class="window-layout">
    <v-card-title class="d-flex align-center ga-2 text-subtitle-1">
      <v-icon size="small">mdi-monitor</v-icon>
      <span>{{ win.hostname }}</span>
      <span class="text-medium-emphasis">{{ win.screen_label }}</span>
      <v-spacer/>
      <span class="text-caption text-disabled">{{ win.width }}x{{ win.height }}<template v-if="rotated"> (rotated)</template></span>
    </v-card-title>
    <v-card-text>
      <!-- the window, in the space of its content: what the area and the panes are percentages of -->
      <div class="text-caption text-medium-emphasis text-center mb-1" data-test="orientation-note">
        {{ portrait ? 'Portrait' : 'Landscape' }} content<template v-if="turned">, turned {{ turned }}&deg;</template><template v-if="mirror">, mirrored {{ mirror === 'x' ? 'left-right' : 'top-bottom' }}</template>
      </div>
      <div class="preview-wrap"><div ref="preview" class="preview" :class="mirror && 'mirror-' + mirror" :style="previewStyle" data-test="preview">
        <div class="area" :class="{editable: canEdit}" :style="areaStyle" data-test="area"
             @pointerdown="canEdit && startDrag($event, 'move')">
          <PaneTree :node="layout.tree" :main="layout.main" :labels="labels" :editable="canEdit" :mirror="mirror" @ratio="onRatio"/>
          <template v-if="canEdit">
            <div v-for="h in handles" :key="h" class="handle" :class="h" :data-handle="h"
                 @pointerdown.stop.prevent="startDrag($event, h)"/>
          </template>
        </div>
      </div></div>

      <div v-if="canEdit" class="mt-4">
        <div class="text-caption font-weight-medium mb-1">Content area</div>
        <div class="d-flex flex-wrap ga-1 mb-2">
          <v-chip v-for="p in presets" :key="p.area.top + ':' + p.area.left + ':' + p.area.width + ':' + p.area.height" size="small" variant="outlined" :color="isPreset(p) ? 'primary' : undefined"
                  @click="setArea(p.area)">{{ p.name }}</v-chip>
        </div>
        <div class="d-flex flex-wrap ga-2 align-center">
          <v-text-field v-for="f in fields" :key="f.key" :label="f.label" :model-value="layout.area[f.key]" type="number"
                        step="0.5" min="0" max="100" suffix="%" density="compact" variant="outlined" hide-details
                        style="max-width: 110px" :data-test="'area-' + f.key"
                        @change="e => setArea({[f.key]: Number(e.target.value)})"/>
          <label class="d-flex align-center ga-2 text-caption">
            Margin
            <input type="color" :value="colorHex" data-test="margin-color" @input="e => marginColor(e.target.value, false)"
                   @change="e => marginColor(e.target.value, true)">
          </label>
        </div>
      </div>

      <div class="text-caption font-weight-medium mt-4 mb-1">Panes</div>
      <v-alert v-if="leafIds.length > 4" type="info" variant="tonal" density="compact" class="mb-2" data-test="many-panes-hint">
        Videos are decoded by the CPU on a Raspberry Pi 4: it plays about four 720p videos at once, more panes with a video play slower (still pictures and web pages cost little).
      </v-alert>
      <div v-for="id in leafIds" :key="id" class="d-flex align-center ga-2 mb-2" :data-test="'pane-row-' + id">
        <v-chip size="small" :color="id === layout.main ? 'primary' : undefined" style="min-width: 56px; justify-content: center">
          {{ id }}<template v-if="id === layout.main">&nbsp;main</template>
        </v-chip>
        <v-select :items="playlistItems" :model-value="layout.panes[id] ?? ''" label="Playlist" density="compact" variant="outlined"
                  hide-details :data-test="'pane-playlist-' + id" @update:model-value="v => assign(id, v)"/>
        <template v-if="canEdit">
          <v-btn icon="mdi-arrow-split-vertical" size="small" variant="text" title="Split: new pane at the right"
                 :disabled="leafIds.length >= 8" :data-test="'split-h-' + id" @click="split(id, 'h')"/>
          <v-btn icon="mdi-arrow-split-horizontal" size="small" variant="text" title="Split: new pane below"
                 :disabled="leafIds.length >= 8" :data-test="'split-v-' + id" @click="split(id, 'v')"/>
          <v-btn icon="mdi-close" size="small" variant="text" title="Close this pane (its neighbour takes the space)"
                 :disabled="leafIds.length < 2" :data-test="'close-' + id" @click="close(id)"/>
        </template>
      </div>
      <div v-if="canEdit" class="mt-4">
        <div class="text-caption font-weight-medium mb-1">RSS ticker (news crawl and clock bar)</div>
        <TickerSettings :viewer-id="win.viewer_id" :window-id="win.window_id"/>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup>
import {computed, ref, watch} from 'vue'
import {api} from '@/api'
import PaneTree from './PaneTree.vue'
import TickerSettings from './TickerSettings.vue'

const props = defineProps({
  win: {type: Object, required: true},          // viewer_id, window_id, width, height, orientation, layout: {area, tree, main, panes}
  playlists: {type: Array, default: () => []},
  canEdit: {type: Boolean, default: false},
})

const preview = ref(null)
// What is shown while something is dragged: the server's answers do not move the thing under the pointer. Dropped when the
// server's own layout arrives (the answer to the last change), or after a moment if it never does.
const draft = ref(null)
let dragging = false
let holdUntil = 0
let settle = null
const layout = computed(() => draft.value ?? props.win.layout)
// the answers to the updates sent during a drag arrive after it ended, older than what is drawn: they are not shown
watch(() => props.win.layout, () => { if (!dragging && Date.now() >= holdUntil) draft.value = null })
function holdDraft() {
  clearTimeout(settle)
  holdUntil = Date.now() + 800
  settle = setTimeout(() => { if (!dragging) draft.value = null }, 1500)
}

// The preview is the window in the space of its CONTENT, the one the area and the panes are percentages of: turned by the
// content orientation (the width and height of a vertical screen swap), mirrored by the flip, the way it is seen
const rotated = computed(() => [1, 3].includes(props.win.orientation))
const turned = computed(() => (props.win.orientation || 0) * 90)
const mirror = computed(() => props.win.flip === 1 ? 'x' : props.win.flip === 2 ? 'y' : '')
const contentSize = computed(() => rotated.value ? [props.win.height, props.win.width] : [props.win.width, props.win.height])
const portrait = computed(() => contentSize.value[1] > contentSize.value[0])
const previewStyle = computed(() => {
  const [w, h] = contentSize.value
  return {aspectRatio: `${w || 16} / ${h || 9}`, backgroundColor: layout.value.area.margin_color}
})
const areaStyle = computed(() => {
  const a = layout.value.area
  return {left: a.left + '%', top: a.top + '%', width: a.width + '%', height: a.height + '%'}
})

const leaves = node => node.kind === 'leaf' ? [node.id] : [...leaves(node.a), ...leaves(node.b)]
const leafIds = computed(() => leaves(layout.value.tree))
const playlistName = id => props.playlists.find(p => p.playlist_id === id)?.name
const labels = computed(() => Object.fromEntries(leafIds.value.map(id => [id, playlistName(layout.value.panes[id])])))
const playlistItems = computed(() => [{title: 'None (idle page)', value: ''},
  ...props.playlists.map(p => ({title: p.name, value: p.playlist_id}))])

// ── the area ───────────────────────────────────────────────────────────────

const MIN = 5
const handles = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
const fields = [{key: 'left', label: 'Left'}, {key: 'top', label: 'Top'}, {key: 'width', label: 'Width'},
  {key: 'height', label: 'Height'}]
// Full width, a part of the HEIGHT: for a portrait screen (a tall panel, most of it out of reach) this is the usable band
const presets = computed(() => [
  {name: 'Full window', area: {left: 0, top: 0, width: 100, height: 100}},
  {name: portrait.value ? 'Centre third (portrait screen)' : 'Centre third of the height', area: {left: 0, top: 33.33, width: 100, height: 33.34}},
  {name: portrait.value ? 'Centre half (portrait screen)' : 'Centre half of the height', area: {left: 0, top: 25, width: 100, height: 50}},
  {name: portrait.value ? 'Top third (portrait screen)' : 'Top half', area: portrait.value ? {left: 0, top: 0, width: 100, height: 33.33} : {left: 0, top: 0, width: 100, height: 50}},
  {name: portrait.value ? 'Bottom third (portrait screen)' : 'Bottom half', area: portrait.value ? {left: 0, top: 66.67, width: 100, height: 33.33} : {left: 0, top: 50, width: 100, height: 50}},
  {name: 'Centre, half each way', area: {left: 25, top: 25, width: 50, height: 50}},
])
const isPreset = p => ['left', 'top', 'width', 'height'].every(k => Math.abs(layout.value.area[k] - p.area[k]) < 0.05)

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const round2 = v => Math.round(v * 100) / 100

/** The area with some of its fields changed, kept inside the window and at least MIN in each direction. */
function fit(area, patch) {
  const a = {...area, ...patch}
  a.width = clamp(Number.isFinite(+a.width) ? +a.width : 100, MIN, 100)
  a.height = clamp(Number.isFinite(+a.height) ? +a.height : 100, MIN, 100)
  a.left = clamp(Number.isFinite(+a.left) ? +a.left : 0, 0, 100 - a.width)
  a.top = clamp(Number.isFinite(+a.top) ? +a.top : 0, 0, 100 - a.height)
  return {...a, left: round2(a.left), top: round2(a.top), width: round2(a.width), height: round2(a.height)}
}

let lastSent = 0
function sendArea(area, final) {
  const now = Date.now()
  if (!final && now - lastSent < 100) return          // a drag is many events: ten updates a second are plenty
  lastSent = now
  api.Viewers.set_area({viewer_id: props.win.viewer_id, window_id: props.win.window_id, left: area.left, top: area.top,
    width: area.width, height: area.height, margin_color: area.margin_color})
}

function setArea(patch) {
  const area = fit(layout.value.area, patch)
  draft.value = {...layout.value, area}
  holdDraft()
  sendArea(area, true)
}

const colorHex = computed(() => {
  const m = /^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/.exec(layout.value.area.margin_color)
  if (m) return '#' + m.slice(1, 4).map(n => (+n).toString(16).padStart(2, '0')).join('')
  return /^#[0-9a-f]{6}$/i.test(layout.value.area.margin_color) ? layout.value.area.margin_color : '#000000'
})
function marginColor(value, final) {
  draft.value = {...layout.value, area: {...layout.value.area, margin_color: value}}
  holdDraft()
  if (final) sendArea(draft.value.area, true)
}

function startDrag(event, how) {
  const box = preview.value.getBoundingClientRect()
  const from = {...layout.value.area}
  const x0 = event.clientX, y0 = event.clientY
  const target = event.currentTarget
  target.setPointerCapture(event.pointerId)
  dragging = true
  draft.value = {...layout.value, area: from}
  const area = e => {
    const dx = (e.clientX - x0) / box.width * 100 * (mirror.value === 'x' ? -1 : 1)
    const dy = (e.clientY - y0) / box.height * 100 * (mirror.value === 'y' ? -1 : 1)
    if (how === 'move') return fit(from, {left: from.left + dx, top: from.top + dy})
    let {left, top, width, height} = from
    if (how.includes('e')) width = from.width + dx
    if (how.includes('s')) height = from.height + dy
    if (how.includes('w')) { width = from.width - dx; left = from.left + dx }
    if (how.includes('n')) { height = from.height - dy; top = from.top + dy }
    // the edge that is not being dragged stays where it is, whatever the clamping does to the one that is
    const patch = fit(from, {left, top, width, height})
    if (how.includes('w')) patch.left = round2(from.left + from.width - patch.width)
    if (how.includes('n')) patch.top = round2(from.top + from.height - patch.height)
    return patch
  }
  const move = e => {
    const a = area(e)
    draft.value = {...layout.value, area: a}
    sendArea(a, false)
  }
  const end = e => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', end)
    window.removeEventListener('pointercancel', end)
    dragging = false
    const a = area(e)
    draft.value = {...layout.value, area: a}
    holdDraft()
    sendArea(a, true)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', end)
  window.addEventListener('pointercancel', end)
}

// ── the panes ──────────────────────────────────────────────────────────────

const setNode = (node, path, ratio) => path === ''
  ? {...node, ratio}
  : {...node, [path[0] === 'a' ? 'a' : 'b']: setNode(path[0] === 'a' ? node.a : node.b, path.slice(1), ratio)}

let lastRatio = 0
function onRatio(path, ratio, final) {
  dragging = !final
  draft.value = {...layout.value, tree: setNode(layout.value.tree, path, ratio)}
  if (final) holdDraft()
  const now = Date.now()
  if (!final && now - lastRatio < 100) return
  lastRatio = now
  api.Viewers.set_split_ratio({viewer_id: props.win.viewer_id, window_id: props.win.window_id, path, ratio})
}

const target = () => ({viewer_id: props.win.viewer_id, window_id: props.win.window_id})
const split = (id, direction) => api.Viewers.split_pane({...target(), pane_id: id, direction})
const close = id => api.Viewers.close_pane({...target(), pane_id: id})
function assign(id, playlistId) {
  if (playlistId) api.Viewers.assign_playlist({...target(), playlist_id: playlistId, pane_id: id})
  else api.Viewers.unassign_playlist({...target(), pane_id: id})
}
</script>

<style scoped>
.preview-wrap { padding: 10px; max-width: 660px; margin: 0 auto; }   /* room for the handles of an area that fills the window */
.preview {
  position: relative; width: 100%;
  border: 2px solid rgba(var(--v-theme-on-surface), 0.4); border-radius: 4px; user-select: none;
}
.preview.mirror-x { transform: scaleX(-1); }
.preview.mirror-y { transform: scaleY(-1); }
.preview.mirror-x :deep(.leaf-id), .preview.mirror-x :deep(.leaf-name) { display: inline-block; transform: scaleX(-1); }
.preview.mirror-y :deep(.leaf-id), .preview.mirror-y :deep(.leaf-name) { display: inline-block; transform: scaleY(-1); }
.area { position: absolute; box-shadow: 0 0 0 1px rgb(var(--v-theme-primary)); }
.area.editable { cursor: move; touch-action: none; }
.handle { position: absolute; background: rgb(var(--v-theme-primary)); z-index: 2; touch-action: none; }
.handle.n, .handle.s { left: 12px; right: 12px; height: 6px; cursor: ns-resize; }
.handle.e, .handle.w { top: 12px; bottom: 12px; width: 6px; cursor: ew-resize; }
.handle.n { top: -3px; } .handle.s { bottom: -3px; } .handle.e { right: -3px; } .handle.w { left: -3px; }
.handle.ne, .handle.nw, .handle.se, .handle.sw { width: 12px; height: 12px; border-radius: 50%; }
.handle.ne { top: -6px; right: -6px; cursor: nesw-resize; } .handle.nw { top: -6px; left: -6px; cursor: nwse-resize; }
.handle.se { bottom: -6px; right: -6px; cursor: nwse-resize; } .handle.sw { bottom: -6px; left: -6px; cursor: nesw-resize; }
</style>
