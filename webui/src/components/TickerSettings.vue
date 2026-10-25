<template>
  <div class="d-flex flex-column ga-2 align-center">
    <v-chip size="small" :color="saved.enabled ? 'primary' : undefined" variant="tonal">
      {{ saved.enabled ? `On, ${saved.rows} ${saved.rows === 1 ? 'row' : 'rows'}, ${saved.mode}` : 'Off' }}
    </v-chip>
    <v-btn size="small" variant="outlined" prepend-icon="mdi-rss" @click="open = true">Configure</v-btn>

    <v-dialog v-model="open" max-width="980" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center ga-2">
          <v-icon>mdi-rss</v-icon> RSS ticker of this window
          <v-spacer></v-spacer>
          <v-switch v-model="draft.enabled" label="Enabled" color="primary" density="compact" hide-details></v-switch>
        </v-card-title>
        <v-card-text>
          <!-- live preview: the same code that draws the bar on the screen -->
          <div class="preview mb-4" :style="{background: draft.background, color: draft.color}">
            <div v-for="(line, i) in previewLines" :key="i" class="preview-line">
              <span v-for="(seg, j) in line" :key="j" :style="segStyle(seg.style)">{{ seg.text }}</span>
              <span class="preview-sep">{{ draft.separator }}</span>
            </div>
            <div v-if="!previewLines.length" class="preview-line text-medium-emphasis">Nothing to show with this selection</div>
          </div>
          <div class="text-caption text-medium-emphasis mb-4">
            Preview of the template with {{ usingSamples ? 'sample headlines (no source has news yet)' : 'the real headlines' }};
            the bar on the screen crawls them. Text colours: {{ draft.color }} on {{ draft.background }}.
          </div>
          <v-alert v-if="parsed.error" type="error" variant="tonal" density="compact" class="mb-2">
            Template error: {{ parsed.error }}. The screen shows the titles only until it is fixed.
          </v-alert>
          <v-alert v-for="w in parsed.warnings" :key="w" type="warning" variant="tonal" density="compact" class="mb-2">{{ w }}</v-alert>

          <v-row>
            <v-col cols="12" md="6">
              <div class="text-subtitle-2 mb-2">Template</div>
              <v-select :items="presets" item-title="label" item-value="template" label="Presets" density="compact"
                        variant="outlined" hide-details class="mb-2" :model-value="null" placeholder="Choose a preset"
                        @update:model-value="v => { if (v) draft.template = v }"></v-select>
              <v-textarea v-model="draft.template" label="Template" rows="2" auto-grow density="compact" variant="outlined"
                          class="mono" counter="500" :error-messages="parsed.error ? [parsed.error] : []"></v-textarea>
              <div class="text-caption text-medium-emphasis">
                Fields: <code>{time:HH:mm}</code> <code>{title}</code> <code>{source}</code> <code>{summary}</code>
                &middot; text spec <code>{title:.80}</code> <code>{source:&gt;12}</code>, conversions
                <code>{title!u}</code> <code>!l</code> <code>!t</code> &middot; time: <code>HH</code> hour 00-23,
                <code>hh</code> 01-12, <code>mm</code> minutes, <code>MM</code> month, <code>MMM</code> month name,
                <code>dd</code> day, <code>EEE</code> weekday, <code>yyyy</code> year, or Python style
                <code>{time:%H:%M}</code> &middot; colours and styles as in Rich:
                <code>[bold red]..[/]</code> <code>[on blue]</code> <code>[#ff8800]</code> <code>[dim]</code>
                <code>[italic]</code> <code>[underline]</code> <code>[strike]</code>; <code>\[</code> writes a bracket.
              </div>
              <v-text-field v-model="draft.separator" label="Between headlines" density="compact" variant="outlined"
                            class="mt-4" hide-details maxlength="20"></v-text-field>
            </v-col>

            <v-col cols="12" md="6">
              <div class="text-subtitle-2 mb-2">Which headlines</div>
              <v-radio-group v-model="draft.select.mode" density="compact" hide-details inline>
                <v-radio label="Newest N" value="count"></v-radio>
                <v-radio label="Today" value="today"></v-radio>
                <v-radio label="Last period" value="range"></v-radio>
              </v-radio-group>
              <v-text-field v-if="draft.select.mode === 'count'" v-model.number="draft.select.count" type="number" min="1"
                            max="500" label="How many (1-500)" density="compact" variant="outlined" hide-details class="mt-2"></v-text-field>
              <div v-if="draft.select.mode === 'range'" class="d-flex ga-2 mt-2">
                <v-select :items="rangePresets" :model-value="rangePreset" label="Period" density="compact" variant="outlined"
                          hide-details @update:model-value="v => { if (v) draft.select.seconds = v }"></v-select>
                <v-text-field v-model.number="rangeMinutes" type="number" min="1" label="Minutes" density="compact"
                              variant="outlined" hide-details max-width="130"></v-text-field>
              </div>
              <div class="text-caption text-medium-emphasis mt-1">
                Evaluated by the screen with its own clock: headlines age out by themselves.
              </div>

              <div class="text-subtitle-2 mt-4 mb-2">Bar</div>
              <div class="d-flex ga-4 flex-wrap">
                <v-btn-toggle v-model="draft.position" mandatory density="compact" variant="outlined" divided>
                  <v-btn value="top">Top</v-btn><v-btn value="bottom">Bottom</v-btn>
                </v-btn-toggle>
                <v-btn-toggle v-model="draft.mode" mandatory density="compact" variant="outlined" divided>
                  <v-btn value="overlay">Over the content</v-btn><v-btn value="reserve">Below / above it</v-btn>
                </v-btn-toggle>
                <v-btn-toggle v-model="draft.rows" mandatory density="compact" variant="outlined" divided>
                  <v-btn :value="1">1 row</v-btn><v-btn :value="2">2 rows</v-btn>
                </v-btn-toggle>
              </div>
              <div class="d-flex ga-2 mt-4">
                <v-text-field v-model.number="draft.font_size" type="number" step="0.5" min="1"
                              :label="draft.font_unit === 'percent' ? 'Text size (% of the smaller side)' : 'Text size (px)'"
                              density="compact" variant="outlined" hide-details></v-text-field>
                <v-select v-model="draft.font_unit" :items="[{title: '%', value: 'percent'}, {title: 'px', value: 'px'}]"
                          density="compact" variant="outlined" hide-details max-width="100"></v-select>
              </div>
              <div class="d-flex ga-4 mt-4 align-center">
                <v-menu :close-on-content-click="false">
                  <template #activator="{props}">
                    <v-btn v-bind="props" variant="outlined" size="small"><span class="swatch" :style="{background: draft.color}"></span>Text</v-btn>
                  </template>
                  <v-color-picker v-model="draft.color" mode="rgba" show-swatches></v-color-picker>
                </v-menu>
                <v-menu :close-on-content-click="false">
                  <template #activator="{props}">
                    <v-btn v-bind="props" variant="outlined" size="small"><span class="swatch" :style="{background: draft.background}"></span>Background</v-btn>
                  </template>
                  <v-color-picker v-model="draft.background" mode="rgba" show-swatches></v-color-picker>
                </v-menu>
              </div>
              <div class="mt-4">
                <div class="text-caption">Speed: {{ draft.speed }} em/s <span class="text-medium-emphasis">({{ speedName }})</span></div>
                <div class="d-flex align-center ga-2">
                  <v-slider v-model="draft.speed" :min="0.25" :max="10" :step="0.25" hide-details color="primary"></v-slider>
                  <v-btn size="x-small" variant="text" @click="draft.speed = 2.5">Default</v-btn>
                </div>
                <div class="text-caption text-medium-emphasis">
                  em = the text size: {{ draft.speed }} em/s is about {{ pxPerSecond }} px/s on a 1080p screen. The default,
                  2.5 em/s, is the middle of what signage practice uses and below the speed at which reading studies see
                  legibility fall.
                </div>
              </div>
            </v-col>
          </v-row>

          <v-divider class="my-4"></v-divider>
          <div class="text-subtitle-2 mb-2">Clock</div>
          <div class="d-flex ga-4 align-center flex-wrap">
            <v-switch v-model="draft.clock.enabled" label="Show a clock" color="primary" density="compact" hide-details></v-switch>
            <template v-if="draft.clock.enabled">
              <v-btn-toggle v-model="draft.clock.position" mandatory density="compact" variant="outlined" divided>
                <v-btn value="start">At the start</v-btn><v-btn value="end">At the end</v-btn>
              </v-btn-toggle>
              <v-switch v-model="draft.clock.seconds" label="Seconds" color="primary" density="compact" hide-details></v-switch>
              <v-switch v-model="draft.clock.date" label="Date" color="primary" density="compact" hide-details></v-switch>
              <v-btn-toggle v-model="draft.clock.rows" mandatory density="compact" variant="outlined" divided :disabled="!draft.clock.date">
                <v-btn :value="1">1 row</v-btn><v-btn :value="2">2 rows</v-btn>
              </v-btn-toggle>
            </template>
            <v-text-field v-model="draft.locale" label="Language of the names (e.g. it-IT)" placeholder="the screen's own"
                          density="compact" variant="outlined" hide-details max-width="260"></v-text-field>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn @click="open = false">Cancel</v-btn>
          <v-btn color="primary" variant="flat" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import {computed, ref, watch} from 'vue'
import {api} from '@/api'
import {parseTemplate, renderItem, selectItems} from '@/ticker-core'

const props = defineProps({viewerId: {type: String, required: true}, windowId: {type: Number, required: true}})

const DEFAULTS = {
  enabled: false, position: 'bottom', mode: 'reserve', rows: 1, font_size: 3, font_unit: 'percent',
  color: 'rgb(255,255,255)', background: 'rgba(0,0,0,0.85)', speed: 2.5, separator: '   •   ',
  template: '[b]{time:HH:mm}[/b] - {title}', select: {mode: 'count', count: 20, seconds: 7200},
  clock: {enabled: false, position: 'end', seconds: false, date: false, rows: 1}, locale: '',
}
const clone = v => JSON.parse(JSON.stringify(v))
const merge = (s) => ({...DEFAULTS, ...s, select: {...DEFAULTS.select, ...s?.select}, clock: {...DEFAULTS.clock, ...s?.clock}})

const open = ref(false)
const saved = ref(clone(DEFAULTS))
const draft = ref(clone(DEFAULTS))

const presets = [
  {label: 'hh:mm - text', template: '[b]{time:HH:mm}[/b] - {title}'},
  {label: 'dd MMM - text', template: '{time:dd MMM} - {title}'},
  {label: 'date, time, source dimmed, text', template: '{time:dd/MM HH:mm} [dim]{source}[/dim] {title}'},
  {label: 'Source in yellow, text', template: '[yellow]{source:.12}[/] {title}'},
  {label: 'Weekday and time, text', template: '{time:EEE HH:mm}  {title}'},
  {label: 'TV style: SOURCE on yellow, text', template: '[bold black on yellow] {source!u} [/] {title}'},
  {label: 'Time in cyan, text, source in brackets', template: '[cyan]{time:HH:mm}[/cyan]  {title} [dim]({source})[/dim]'},
  {label: '12 hours with am/pm', template: '[b]{time:hh:mm a}[/b] {title}'},
  {label: 'Python style date', template: '{time:%d/%m %H:%M} {title}'},
  {label: 'Capitals', template: '[bold]{title!u}[/bold]'},
  {label: 'Title and summary', template: '[b]{title}[/b] - {summary:.120}'},
  {label: 'Text only', template: '{title}'},
]

const rangePresets = [
  {title: 'Last hour', value: 3600}, {title: 'Last 2 hours', value: 7200}, {title: 'Last 6 hours', value: 21600},
  {title: 'Last 12 hours', value: 43200}, {title: 'Last 24 hours', value: 86400}, {title: 'Last 2 days', value: 172800},
  {title: 'Last 7 days', value: 604800},
]
const rangePreset = computed(() => rangePresets.find(p => p.value === draft.value.select.seconds)?.value ?? null)
const rangeMinutes = computed({
  get: () => Math.round(draft.value.select.seconds / 60),
  set: v => { draft.value.select.seconds = Math.max(1, Number(v) || 1) * 60 },
})

const speedName = computed(() => {
  const s = draft.value.speed
  return s < 1 ? 'very slow' : s < 2 ? 'slow' : s <= 3.5 ? 'normal' : s <= 6 ? 'fast' : 'very fast'
})
const pxPerSecond = computed(() => Math.round(draft.value.speed * 0.03 * 1080)) // default text size 3% of 1080

// ── preview ──────────────────────────────────────────────────────────────────
const preview = api.Ticker.preview.use({pick: 'items', init: false, initial: []})
const now = () => Date.now() / 1000
const samples = () => [
  ['ANSA', 'Maltempo, allerta arancione in tre regioni', 120], ['BBC', 'Talks continue as deadline approaches', 900],
  ['Reuters', 'Markets close higher on earnings optimism', 2400], ['ANSA', 'Il museo riapre dopo il restauro', 5400],
].map(([source, title, ago]) => ({time: now() - ago, source, title, summary: 'A short summary of the story that gives a little more detail.', link: ''}))
const usingSamples = computed(() => !preview.value.length)
const parsed = computed(() => parseTemplate(draft.value.template))
const previewLines = computed(() => {
  const items = usingSamples.value ? samples() : selectItems(preview.value, draft.value.select, Date.now())
  return items.slice(0, 4).map(it => renderItem(parsed.value, it, draft.value.locale))
})
const segStyle = st => ({
  color: st.color, backgroundColor: st.bg, fontWeight: st.bold ? 'bold' : undefined, fontStyle: st.italic ? 'italic' : undefined,
  opacity: st.dim ? 0.6 : undefined, padding: st.bg ? '0 .25em' : undefined,
  textDecoration: [st.underline && 'underline', st.strike && 'line-through', st.overline && 'overline'].filter(Boolean).join(' ') || undefined,
})

// ── talking to the server ────────────────────────────────────────────────────
const mine = msg => msg.viewer_id === props.viewerId && msg.window_id === props.windowId
api.Ticker.settings.on(msg => {
  if (!mine(msg)) return
  saved.value = merge(msg.settings)
  if (!open.value) draft.value = clone(saved.value)
})
const ask = () => api.Ticker.settings({viewer_id: props.viewerId, window_id: props.windowId})
ask()
watch(open, isOpen => {
  if (!isOpen) return
  draft.value = clone(saved.value)
  api.Ticker.preview()
  ask()
})
function save() {
  api.Ticker.set_settings({viewer_id: props.viewerId, window_id: props.windowId, settings: clone(draft.value)})
  open.value = false
  setTimeout(ask, 500) // the server answers with the real settings (clamped), or with an error that leaves them as they were
}

</script>

<style scoped>
.preview { border-radius: 6px; padding: 8px 12px; font-size: 18px; line-height: 1.5; overflow: hidden; border: 1px solid rgba(128, 128, 128, .4); }
.preview-line { white-space: pre; overflow: hidden; text-overflow: ellipsis; }
.preview-sep { opacity: .6; white-space: pre; }
.mono :deep(textarea) { font-family: ui-monospace, monospace; }
.swatch { display: inline-block; width: 14px; height: 14px; border-radius: 3px; border: 1px solid #888; margin-right: 6px; }
code { background: rgba(128, 128, 128, .2); border-radius: 3px; padding: 0 3px; }
</style>
