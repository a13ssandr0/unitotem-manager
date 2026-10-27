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
 * THE CRAWL IS MOVED BY THE COMPOSITOR, never by JavaScript. Moving text with a script (a position written every frame)
 * judders by construction: the script runs on the main thread, any other work delays a frame, and a position that is
 * rounded to whole pixels steps by 1 or 2 px alternately. Here the only thing that moves is a `transform` Web Animation
 * (linear, constant speed, fractional pixels) on an element with `will-change: transform`: the browser rasterises the text
 * ONCE into a texture and the GPU process slides it, with no main thread involved, so a busy main thread cannot make the
 * crawl miss a frame.
 *
 * Many headlines are never one long string, and never one element each: each lane is a RECYCLER of PAGES. A page is one element holding consecutive headlines, about 2-3 bar widths long (never more than
 * 8192 px: GPU texture limit) and has ONE animation from the right edge of the lane to its own width to the left. The next
 * page is built ahead with seconds of slack and its animation gets an exact `startTime` (start of the previous one +
 * the time the previous one needs to move by its own width, on document.timeline), so the gap is zero and the speed is the
 * same on every page. A page that has left goes back to a pool. Only a few elements exist whatever the list's length is
 * (500 headlines cost what 5 do), a change of the list never touches the pages already built (only the ones not built yet
 * use it, so the crawl never jumps), and nothing on the main thread depends on a frame deadline.
 *
 * The text of a feed is untrusted: it only reaches the page as `textContent` of spans styled with validated colours.
 */
import {
  barHeight, fontPx, itemKey, LINE_HEIGHT, nextExpiry, parseTemplate, renderItem, resumeIndex, selectItems,
} from './ticker-core.js'

const MAX_PAGE_PX = 8192 // a composited layer should stay inside the smallest GPU texture
const PAGES_AHEAD = 3 // the page on screen and the ones prepared behind it: tens of seconds of slack at any usable speed
const POOL = 4
const MAX_TIMER = 2 ** 31 - 1

const css = `
#ticker-host.ut-on { display: block; overflow: hidden; font-family: "Noto Sans", system-ui, sans-serif; line-height: ${LINE_HEIGHT};
  white-space: nowrap; -webkit-user-select: none; user-select: none; pointer-events: none; contain: layout paint; }
#ticker-host .ut-bar { display: flex; height: 100%; width: 100%; }
#ticker-host .ut-lanes { position: relative; flex: 1 1 0; min-width: 0; overflow: hidden; }
#ticker-host .ut-lane { position: absolute; left: 0; right: 0; overflow: hidden; }
#ticker-host .ut-page { position: absolute; left: 0; top: 0; height: 100%; display: flex; width: max-content; will-change: transform; }
#ticker-host .ut-item { flex: none; display: block; white-space: pre; }
#ticker-host .ut-sep { opacity: .6; white-space: pre; }
#ticker-host .ut-clock { flex: 0 0 auto; display: flex; flex-direction: column; justify-content: center; text-align: center;
  padding: 0 .7em; font-weight: bold; font-variant-numeric: tabular-nums; box-shadow: inset 0 0 0 999px rgba(128,128,128,.22); }
#ticker-host .ut-clock.ut-start { border-right: 1px solid rgba(128,128,128,.5); }
#ticker-host .ut-clock.ut-end { border-left: 1px solid rgba(128,128,128,.5); }
#ticker-host .ut-clock div { line-height: ${LINE_HEIGHT}; }
#ticker-host .ut-clock .ut-date { font-weight: normal; font-size: .8em; line-height: 1.3; }
`

const state = {
  cfg: null, key: '', parsed: null, items: [], itemsKey: '', selected: [], host: null, area: null,
  lanes: [], font: 0, lineH: 0, barH: 0, laneW: 0, hidden: false,
  expiry: null, clockTimer: null, clockEl: null, clockText: '', clockMinW: 0, observer: null,
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
  const host = document.getElementById('ticker-host')
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

function headline(item) {
  const el = document.createElement('div')
  el.className = 'ut-item'
  for (const seg of renderItem(state.parsed, item, state.cfg.locale)) {
    const span = document.createElement('span')
    span.textContent = seg.text // never innerHTML: the text comes from a feed
    styleSpan(span, seg.style)
    el.appendChild(span)
  }
  if (state.cfg.separator) {
    const sep = document.createElement('span')
    sep.className = 'ut-sep'
    sep.textContent = state.cfg.separator
    el.appendChild(sep)
  }
  return el
}

// ── the recycler: chained pages, each one constant-velocity compositor animation ──

const speedPx = () => state.cfg.speed * state.font // px per second

function makeLane(k) {
  const el = document.createElement('div')
  el.className = 'ut-lane'
  // the lanes are centred in the bar (a one row bar next to a two row clock is two lines tall)
  const free = state.barH - state.cfg.rows * state.lineH
  el.style.top = `${Math.round(free / 2 + k * state.lineH)}px`
  el.style.height = `${state.lineH}px`
  // pages: {el, anim, x0, width}; nextStart: when the page after the last one must start (ms on document.timeline)
  return {k, el, pages: [], pool: [], idx: k, lastKey: null, nextStart: 0, building: false}
}

/** Builds the next page of a lane from the list (consecutive headlines, as many as make 2-3 bar widths) and chains it. */
function addPage(lane) {
  const list = state.selected
  if (!list.length) return false
  const first = lane.pages.length === 0
  const el = lane.pool.pop() || Object.assign(document.createElement('div'), {className: 'ut-page'})
  el.replaceChildren()
  el.style.transform = `translate3d(${state.laneW}px,0,0)` // parked out of sight until its animation starts
  lane.el.appendChild(el)
  const target = Math.min(MAX_PAGE_PX, Math.max(2 * state.laneW, 1200))
  let width = 0
  for (let n = 0; n < 200 && (width < target || n === 0); n++) {
    const item = list[lane.idx % list.length]
    el.appendChild(headline(item))
    lane.lastKey = itemKey(item)
    lane.idx = (lane.idx + state.cfg.rows) % list.length
    width = el.getBoundingClientRect().width // layout of this small page only; fractional on purpose
    if (width >= MAX_PAGE_PX) break
  }
  const px = speedPx()
  const x0 = first ? 0 : state.laneW // the very first page starts already filling the bar
  const now = document.timeline.currentTime
  const startTime = first ? now : Math.max(now, lane.nextStart)
  const anim = el.animate(
    [{transform: `translate3d(${x0}px,0,0)`}, {transform: `translate3d(${-width}px,0,0)`}],
    {duration: ((x0 + width) / px) * 1000, easing: 'linear', fill: 'both'})
  anim.startTime = startTime
  // the next page starts when this one's right edge reaches the right edge of the lane
  lane.nextStart = startTime + (Math.max(0, x0 + width - state.laneW) / px) * 1000
  const page = {el, anim, x0, width}
  anim.onfinish = () => retire(lane, page)
  lane.pages.push(page)
  return true
}

function retire(lane, page) {
  const at = lane.pages.indexOf(page)
  if (at < 0) return
  lane.pages.splice(at, 1)
  page.anim.onfinish = null
  page.anim.cancel()
  page.el.remove()
  if (lane.pool.length < POOL) lane.pool.push(page.el)
  extend(lane)
  updateVisibility()
}

/** Keeps the pages ahead prepared. Called when a page has left and when the list or the lanes change. */
function extend(lane) {
  while (lane.pages.length < PAGES_AHEAD && addPage(lane)) { /* one more */ }
}

function rebuildLanes() {
  for (const lane of state.lanes) { for (const p of lane.pages) { p.anim.onfinish = null; p.anim.cancel() } lane.el.remove() }
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
  // all the lanes start on the same tick: whatever each costs to build, the timeline start is shared
  for (const lane of state.lanes) extend(lane)
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
    // the pages built stay as they are; the next ones go on after the last headline placed: no jump, no repeat
    for (const lane of state.lanes) {
      lane.idx = lane.lastKey !== null ? resumeIndex(list, lane.lastKey, state.cfg.rows) : (list.length ? lane.k % list.length : 0)
    }
  }
  const at = nextExpiry(state.items, state.cfg.select, now)
  if (at !== null) state.expiry = setTimeout(reselect, Math.min(MAX_TIMER, Math.max(1000, at - now + 50)))
  for (const lane of state.lanes) extend(lane)
  updateVisibility()
}

/** The bar takes no room while there is nothing to show (no headline on screen or to come, no clock). */
function updateVisibility() {
  if (!state.cfg || !state.cfg.enabled) return
  const busy = state.selected.length > 0 || state.cfg.clock.enabled || state.lanes.some(l => l.pages.length)
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

// ── clock (static text: nothing animated) ────────────────────────────────────

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
  tickClock() // the clock takes its width before the lanes are measured
  state.hidden = false
  applyHeight()
  state.laneW = lanes.clientWidth || windowSize()[0]

  if (state.observer) state.observer.disconnect()
  state.observer = new ResizeObserver(() => relayout())
  state.observer.observe(lanes)
  state.observer.observe(document.body)
  rebuildLanes()
}

/** The window (or the lanes) changed size, e.g. a rotation: the pages were measured for the old one, so rebuild cleanly. */
function relayout() {
  if (!state.cfg || !state.cfg.enabled) return
  const lanes = state.host.querySelector('.ut-lanes')
  const laneW = lanes ? lanes.clientWidth : state.laneW
  const before = state.font
  measure()
  if (state.font !== before) state.host.style.fontSize = `${state.font}px`
  applyHeight()
  if (state.font !== before || laneW !== state.laneW) {
    state.laneW = laneW
    rebuildLanes()
  }
}

function teardown() {
  clearTimeout(state.expiry)
  clearTimeout(state.clockTimer)
  if (state.observer) state.observer.disconnect()
  for (const lane of state.lanes) for (const p of lane.pages) { p.anim.onfinish = null; p.anim.cancel() }
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
  state.key = key
  state.cfg = settings
  if (!settings.enabled) { teardown(); return }
  const parsed = parseTemplate(settings.template)
  if (parsed.error) console.error('ticker: invalid template, showing the titles:', parsed.error)
  state.parsed = parsed
  state.selected = selectItems(state.items, settings.select, Date.now()) // the pages of the new bar are built from the right list
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

/** Settings and list together (what the page does): the pages of a new bar are built from the right list. */
function update(cfg, items) {
  if (Array.isArray(items)) {
    state.items = items.slice(0, 500)
    state.itemsKey = ''
  }
  configure(cfg)
  reselect()
  if (Array.isArray(items)) setItems(items)
}

window.utTicker = {
  configure,
  setItems,
  update,
  destroy() { teardown(); state.cfg = null; state.key = '' },
  /* for tests and the console */
  inspect: () => ({
    enabled: !!(state.cfg && state.cfg.enabled), font: state.font, barH: state.barH, laneW: state.laneW, speedPx: state.cfg ? speedPx() : 0,
    selected: state.selected.length, items: state.items.length, hidden: state.hidden,
    lanes: state.lanes.map(l => ({
      pages: l.pages.length, pool: l.pool.length, idx: l.idx, lastKey: l.lastKey,
      widths: l.pages.map(p => Math.round(p.width)), running: l.pages.filter(p => p.anim.playState === 'running').length,
      // where the first page is, from its animation clock (the compositor moves it; this only reads the clock)
      x: l.pages.length ? Math.round(l.pages[0].x0 + ((l.pages[0].anim.currentTime || 0) / 1000) * -speedPx()) : null,
    })),
    domPages: state.host ? state.host.querySelectorAll('.ut-page').length : 0,
    domItems: state.host ? state.host.querySelectorAll('.ut-item').length : 0,
  }),
}
