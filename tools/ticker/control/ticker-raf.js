/**
 * The RSS bar of a window (a TV news crawl), drawn inside the viewer's page.
 *
 * Loaded on demand by boot-screen.html (like keyboard.js) as a module of this node's manager once the bar of the window is
 * enabled, and driven from outside: `utTicker.configure(settings)` with the settings of the window (`SetTicker`) and
 * `utTicker.setItems(list)` with the merged list (`TickerItems`); the page calls both from `applyTicker()` whenever the
 * viewer hands it new ones. The page never asks the manager for anything.
 *
 * Contract with the page: it has `#area` containing `#ticker-host`; the page's CSS puts `#ticker-host` on the top or the
 * bottom edge of `#area` (`#area[data-ticker=off|overlay|reserve]`, `[data-ticker-pos=top|bottom]`, which this module also
 * sets) and, when the bar reserves its space, leaves `var(--ticker-h)` free for it. This module sets `--ticker-h` (px) on
 * `#area`, sizes and paints the host, and draws inside it. Nothing outside `#area` is touched.
 *
 * Many headlines are never one long string. Each lane is a RECYCLER: a belt (one element
 * moved with one `transform`, the only animated layer of the lane) carries the few headline elements that are on screen
 * plus a buffer on the right; an element that has left on the left goes to a pool and is filled again with the next
 * headline of the list, which wraps around. 500 headlines cost the same as 5. Headlines are placed at the moment they are
 * needed and an element is only measured when it is filled, so a change of the list never makes the crawl jump: the lane
 * goes on after the last headline it placed.
 *
 * The text of a feed is untrusted: it only reaches the page as `textContent` of spans styled with validated colours.
 */
import {
  barHeight, effectiveRows, fontPx, itemKey, LINE_HEIGHT, nextExpiry, PADDING_EM, parseTemplate, renderItem, resumeIndex, selectItems,
} from './ticker-core.js'

const FRAME_MS = 1000 / (Number(window.__utTickerFps) || 30) // a crawl is smooth at 30 fps and software compositing pays for every frame
const MAX_DT = 250 // ms: after a stall (a hidden page, a slow frame) the crawl goes on, it does not jump
const REBASE_PX = 40000 // the belt is moved back to 0 now and then so its transform never becomes huge
const MAX_TIMER = 2 ** 31 - 1

const css = `
#ticker-host.ut-on { display: block; overflow: hidden; font-family: "Noto Sans", system-ui, sans-serif; line-height: ${LINE_HEIGHT};
  white-space: nowrap; -webkit-user-select: none; user-select: none; pointer-events: none; contain: layout paint; }
#ticker-host .ut-bar { display: flex; height: 100%; width: 100%; }
#ticker-host .ut-lanes { position: relative; flex: 1 1 0; min-width: 0; overflow: hidden; }
#ticker-host .ut-lane { position: absolute; left: 0; right: 0; overflow: hidden; }
#ticker-host .ut-belt { position: absolute; left: 0; top: 0; height: 100%; will-change: transform; }
#ticker-host .ut-item { position: absolute; left: 0; top: 0; height: 100%; display: block; white-space: pre; }
#ticker-host .ut-sep { opacity: .6; white-space: pre; }
#ticker-host .ut-clock { flex: 0 0 auto; display: flex; flex-direction: column; justify-content: center; text-align: center;
  padding: 0 .7em; font-weight: bold; font-variant-numeric: tabular-nums; box-shadow: inset 0 0 0 999px rgba(128,128,128,.22); }
#ticker-host .ut-clock.ut-start { border-right: 1px solid rgba(128,128,128,.5); }
#ticker-host .ut-clock.ut-end { border-left: 1px solid rgba(128,128,128,.5); }
#ticker-host .ut-clock div { line-height: ${LINE_HEIGHT}; }
#ticker-host .ut-clock .ut-date { font-weight: normal; font-size: .8em; line-height: 1.3; }
`

const state = {
  cfg: null, key: '', structKey: '', parsed: null, items: [], itemsKey: '', selected: [], host: null, area: null,
  lanes: [], font: 0, lineH: 0, barH: 0, laneW: 0, hidden: false,
  timer: null, raf: null, lastTs: 0, expiry: null, clockTimer: null, clockEl: null, clockText: '', clockMinW: 0, observer: null,
}

function ensureStyle() {
  if (document.getElementById('ut-ticker-style')) return
  const el = document.createElement('style')
  el.id = 'ut-ticker-style'
  el.textContent = css
  document.head.appendChild(el)
}

function find() {
  const area = document.getElementById('area')
  let host = document.getElementById('ticker-host')
  if (!area || !host) {
    console.warn('ticker: the page has no #area / #ticker-host')
    return false
  }
  state.area = area
  state.host = host
  return true
}

// ── drawing one headline ─────────────────────────────────────────────────────

function styleSpan(span, st) {
  const s = span.style
  if (st.color) s.color = st.color
  if (st.bg) { s.backgroundColor = st.bg; s.padding = '0 .25em'; s.borderRadius = '.15em' }
  if (st.reverse) { s.color = 'var(--ut-bg)'; s.backgroundColor = 'var(--ut-fg)'; s.padding = '0 .25em' }
  if (st.bold) s.fontWeight = 'bold'
  if (st.dim) s.opacity = '.6'
  if (st.italic) s.fontStyle = 'italic'
  const lines = [st.underline && 'underline', st.strike && 'line-through', st.overline && 'overline'].filter(Boolean)
  if (lines.length) s.textDecoration = lines.join(' ')
}

function fill(el, item) {
  const frag = document.createDocumentFragment()
  for (const seg of renderItem(state.parsed, item, state.cfg.locale)) {
    const span = document.createElement('span')
    span.textContent = seg.text // never innerHTML: the text comes from a feed
    styleSpan(span, seg.style)
    frag.appendChild(span)
  }
  if (state.cfg.separator) {
    const sep = document.createElement('span')
    sep.className = 'ut-sep'
    sep.textContent = state.cfg.separator
    frag.appendChild(sep)
  }
  el.replaceChildren(frag)
}

// ── the recycler ─────────────────────────────────────────────────────────────

function makeLane(k) {
  const el = document.createElement('div')
  el.className = 'ut-lane'
  // the lanes are centred in the bar (a one row bar next to a two row clock is two lines tall)
  const free = state.barH - state.cfg.rows * state.lineH
  el.style.top = `${Math.round(free / 2 + k * state.lineH)}px`
  el.style.height = `${state.lineH}px`
  const belt = document.createElement('div')
  belt.className = 'ut-belt'
  el.appendChild(belt)
  // the bar starts full: the first headline is at the left edge, not still to enter on the right
  return {k, el, belt, active: [], pool: [], offset: 0, endX: 0, idx: k, lastKey: null}
}

function placeNext(lane) {
  const list = state.selected
  const item = list[lane.idx % list.length]
  const el = lane.pool.pop() || Object.assign(document.createElement('div'), {className: 'ut-item'})
  fill(el, item)
  lane.belt.appendChild(el)
  const w = Math.max(8, el.offsetWidth) // measured once, when the element is filled
  const x = lane.endX
  el.style.transform = `translate3d(${x}px,0,0)`
  lane.active.push({el, x, w})
  lane.endX = x + w
  lane.lastKey = itemKey(item)
  lane.idx = (lane.idx + state.cfg.rows) % list.length
}

function stepLane(lane, dx) {
  lane.offset += dx
  if (lane.offset > REBASE_PX) {
    const d = lane.offset
    lane.offset = 0
    lane.endX -= d
    for (const a of lane.active) { a.x -= d; a.el.style.transform = `translate3d(${a.x}px,0,0)` }
  }
  // recycle what has left on the left
  while (lane.active.length && lane.active[0].x + lane.active[0].w < lane.offset - 2) {
    const gone = lane.active.shift()
    gone.el.remove()
    if (lane.pool.length < 12) lane.pool.push(gone.el)
  }
  // and place what is about to enter on the right (a few at most per frame, whatever the list is)
  const buffer = Math.max(200, state.laneW * 0.25)
  for (let guard = 0; state.selected.length && guard < 6 && lane.endX < lane.offset + state.laneW + buffer; guard++) placeNext(lane)
  lane.belt.style.transform = `translate3d(${-Math.round(lane.offset)}px,0,0)`
}

function frame(ts) {
  state.raf = null
  const dt = state.lastTs ? Math.min(ts - state.lastTs, MAX_DT) : FRAME_MS
  state.lastTs = ts
  const dx = (state.cfg.speed * state.font * dt) / 1000
  for (const lane of state.lanes) stepLane(lane, dx)
  updateVisibility()
  schedule()
}

function schedule() {
  if (state.timer !== null || state.raf !== null || !state.cfg || !state.cfg.enabled || document.hidden) return
  // one wake-up per frame at the crawl's own rate, aligned to the display by requestAnimationFrame
  state.timer = setTimeout(() => { state.timer = null; state.raf = requestAnimationFrame(frame) }, Math.max(0, FRAME_MS - 8))
}

function stopLoop() {
  if (state.timer !== null) { clearTimeout(state.timer); state.timer = null }
  if (state.raf !== null) { cancelAnimationFrame(state.raf); state.raf = null }
  state.lastTs = 0
}

function rebuildLanes() {
  for (const lane of state.lanes) lane.el.remove()
  const old = state.lanes.map(l => l.lastKey)
  state.lanes = []
  const box = state.host.querySelector('.ut-lanes')
  if (!box) return
  for (let k = 0; k < state.cfg.rows; k++) {
    const lane = makeLane(k)
    lane.lastKey = old[k] ?? null
    if (lane.lastKey !== null) lane.idx = resumeIndex(state.selected, lane.lastKey, state.cfg.rows)
    else lane.idx = state.selected.length ? k % state.selected.length : 0
    box.appendChild(lane.el)
    state.lanes.push(lane)
  }
  state.lastTs = 0
}

// ── selection (with the device clock) ────────────────────────────────────────

function reselect() {
  if (!state.cfg) return
  clearTimeout(state.expiry)
  state.expiry = null
  const now = Date.now()
  const list = selectItems(state.items, state.cfg.select, now)
  const same = list.length === state.selected.length && list.every((it, i) => itemKey(it) === itemKey(state.selected[i]))
  if (!same) {
    state.selected = list
    // the lanes go on after the last headline they placed: no jump, no repeat
    for (const lane of state.lanes) {
      lane.idx = lane.lastKey !== null ? resumeIndex(list, lane.lastKey, state.cfg.rows) : (list.length ? lane.k % list.length : 0)
    }
  }
  const at = nextExpiry(state.items, state.cfg.select, now)
  if (at !== null) state.expiry = setTimeout(reselect, Math.min(MAX_TIMER, Math.max(1000, at - now + 50)))
  updateVisibility()
  schedule()
}

/** The bar takes no room while there is nothing to show (no headline on screen or to come, no clock). */
function updateVisibility() {
  if (!state.cfg || !state.cfg.enabled) return
  const busy = state.selected.length > 0 || state.cfg.clock.enabled || state.lanes.some(l => l.active.length)
  if (busy === !state.hidden) return
  state.hidden = !busy
  applyHeight()
}

function applyHeight() {
  const h = state.hidden || !state.cfg || !state.cfg.enabled ? 0 : state.barH
  state.host.style.height = `${h}px`
  state.host.style.display = h ? '' : 'none'
  state.area.style.setProperty('--ticker-h', `${h}px`)
}

// ── clock ────────────────────────────────────────────────────────────────────

function clockText(now) {
  const c = state.cfg.clock
  const loc = state.cfg.locale || undefined
  const time = new Intl.DateTimeFormat(loc, {hour: '2-digit', minute: '2-digit', ...(c.seconds ? {second: '2-digit'} : {})}).format(now)
  const date = c.date ? new Intl.DateTimeFormat(loc, {weekday: 'short', day: 'numeric', month: 'short'}).format(now) : ''
  return {time, date}
}

function tickClock() {
  clearTimeout(state.clockTimer)
  state.clockTimer = null
  const el = state.clockEl
  if (!el || !state.cfg || !state.cfg.clock.enabled) return
  const now = new Date()
  const {time, date} = clockText(now)
  const text = `${time}|${date}`
  if (text !== state.clockText) {
    state.clockText = text
    const c = state.cfg.clock
    if (c.rows === 2 && date) {
      const a = document.createElement('div')
      a.textContent = time
      const b = document.createElement('div')
      b.className = 'ut-date'
      b.textContent = date
      el.replaceChildren(a, b)
    } else {
      el.replaceChildren(Object.assign(document.createElement('div'), {textContent: date ? `${date}  ${time}` : time}))
    }
    // never narrower than it has been: a digit must not make the lanes next to it jitter
    state.clockMinW = Math.max(state.clockMinW, el.offsetWidth)
    el.style.minWidth = `${state.clockMinW}px`
  }
  const period = state.cfg.clock.seconds ? 1000 : 60000
  state.clockTimer = setTimeout(tickClock, period - (Date.now() % period) + 5) // aligned to the next second / minute: a timer, not a poll
}

// ── (re)building the bar ─────────────────────────────────────────────────────

function windowSize() {
  const b = document.body
  return [b.clientWidth || window.innerWidth, b.clientHeight || window.innerHeight]
}

function measure() {
  const [w, h] = windowSize()
  const font = fontPx(state.cfg, w, h)
  state.font = font
  state.lineH = Math.round(font * LINE_HEIGHT)
  state.barH = barHeight(state.cfg, font)
  return font
}

function build() {
  const cfg = state.cfg
  const host = state.host
  host.className = 'ut-on'
  host.style.color = cfg.color
  host.style.background = cfg.background
  host.style.setProperty('--ut-fg', cfg.color)
  host.style.setProperty('--ut-bg', cfg.background)
  measure()
  host.style.fontSize = `${state.font}px`
  state.area.dataset.ticker = cfg.mode
  state.area.dataset.tickerPos = cfg.position

  const bar = document.createElement('div')
  bar.className = 'ut-bar'
  const lanes = document.createElement('div')
  lanes.className = 'ut-lanes'
  state.clockEl = null
  state.clockText = ''
  state.clockMinW = 0
  if (cfg.clock.enabled) {
    state.clockEl = document.createElement('div')
    state.clockEl.className = `ut-clock ut-${cfg.clock.position}`
  }
  if (state.clockEl && cfg.clock.position === 'start') bar.appendChild(state.clockEl)
  bar.appendChild(lanes)
  if (state.clockEl && cfg.clock.position === 'end') bar.appendChild(state.clockEl)
  host.replaceChildren(bar)

  if (state.observer) state.observer.disconnect()
  state.observer = new ResizeObserver(() => relayout())
  state.observer.observe(lanes)
  state.observer.observe(document.body)
  state.laneW = lanes.clientWidth || windowSize()[0]
  state.hidden = false
  rebuildLanes()
  applyHeight()
  tickClock()
  // the clock changed the lanes' width: measured now that it is in
  state.laneW = lanes.clientWidth || state.laneW
}

/** The window or the lanes changed size: a new font size rebuilds the lanes (the widths of their headlines were measured
 *  with the old one), a new width only needs to be known. */
function relayout() {
  if (!state.cfg || !state.cfg.enabled) return
  const before = state.font
  const lanes = state.host.querySelector('.ut-lanes')
  state.laneW = lanes ? lanes.clientWidth : state.laneW
  measure()
  if (state.font !== before) {
    state.host.style.fontSize = `${state.font}px`
    rebuildLanes()
  }
  applyHeight()
}

function teardown() {
  stopLoop()
  clearTimeout(state.expiry)
  clearTimeout(state.clockTimer)
  if (state.observer) state.observer.disconnect()
  state.expiry = state.clockTimer = state.observer = null
  state.lanes = []
  state.selected = []
  if (state.host) {
    state.host.replaceChildren()
    state.host.className = ''
    state.host.style.height = '0px'
    state.host.style.display = 'none'
  }
  if (state.area) {
    state.area.dataset.ticker = 'off'
    state.area.style.setProperty('--ticker-h', '0px')
  }
}

// ── public ───────────────────────────────────────────────────────────────────

function configure(cfg) {
  if (!cfg || typeof cfg !== 'object') return
  const {module: _module, ...settings} = cfg
  const key = JSON.stringify(settings)
  if (key === state.key) return
  ensureStyle()
  if (!find()) return
  const previous = state.cfg
  state.key = key
  state.cfg = settings
  if (!settings.enabled) { teardown(); return }
  // speed alone changes nothing on screen: it is read on every frame
  const {speed: _speed, ...rest} = settings
  const structKey = JSON.stringify(rest)
  if (previous && previous.enabled && structKey === state.structKey) { schedule(); return }
  state.structKey = structKey
  const parsed = parseTemplate(settings.template)
  if (parsed.error) console.error('ticker: invalid template, showing the titles:', parsed.error)
  state.parsed = parsed
  build()
  reselect()
}

function setItems(items) {
  if (!Array.isArray(items)) return
  const clean = items.slice(0, 500)
  const key = `${clean.length}|${clean.length ? itemKey(clean[0]) + itemKey(clean[clean.length - 1]) : ''}|${clean.reduce((a, i) => a + (i.time || 0), 0)}`
  if (key === state.itemsKey) return
  state.itemsKey = key
  state.items = clean
  reselect()
}

document.addEventListener('visibilitychange', () => { if (!document.hidden) { state.lastTs = 0; schedule() } })

window.utTicker = {
  configure,
  setItems,
  destroy() { teardown(); state.cfg = null; state.key = state.structKey = '' },
  /* for tests and the console */
  inspect: () => ({
    enabled: !!(state.cfg && state.cfg.enabled), fps: Math.round(1000 / FRAME_MS), font: state.font, barH: state.barH, laneW: state.laneW,
    selected: state.selected.length, items: state.items.length, hidden: state.hidden,
    lanes: state.lanes.map(l => ({active: l.active.length, pool: l.pool.length, offset: Math.round(l.offset), idx: l.idx, lastKey: l.lastKey})),
    domItems: state.host ? state.host.querySelectorAll('.ut-item').length : 0,
  }),
}
