/**
 * The on-screen keyboard of a touch panel, drawn over the page the viewer shows.
 *
 * Loaded by the viewer's own page (boot-screen.html, from the viewer plugin) as a module from this node's manager, and
 * driven from outside: the viewer calls `focusChanged()` when a text field gets or loses the focus in any frame of the
 * page (it can see that, this page cannot: the content is usually from another origin), and `configure()` with the
 * settings of the window. Keys go back through the function `utKey` that the viewer adds to the page; it types them into
 * whatever has the focus.
 *
 * Whoever touches the screen is treated as the worst possible user: the keyboard can be moved and resized, and pushed
 * partly off the screen (to reach what it covers) but a part of its header always stays visible, never below a minimum
 * size, and its header cannot shrink or leave. The limits are applied after every gesture, on every change of the size
 * of the screen and on every rotation, not just while dragging.
 *
 * A key types when it is released, a long press on a letter or a symbol opens its variants
 * (accents, currencies...) to slide onto, the key `?123` switches to a page of symbols and `=\\<` to a second one, shift
 * pressed twice is caps lock and the backspace repeats while held. Moving and resizing is done here with pointer events instead of a library because the content can be
 * rotated and flipped with CSS (the page's orientation settings), and the libraries do not follow a transformed parent.
 */
import Keyboard from 'simple-keyboard'
import keyboardCss from 'simple-keyboard/build/css/index.css?inline'
import SYMBOL_PAGES from './gboard/symbols.json'
import {parseLayoutId} from './keyboard-layouts.js'

const BUTTON_PX = 56
const HEADER_PX = 40
const POSITIONS = ['top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right']
const NUMERIC = {default: ['1 2 3', '4 5 6', '7 8 9', '{bksp} 0 {enter}']}
const NUMERIC_MANUAL = {default: ['1 2 3', '4 5 6', '7 8 9', '{abc} 0 {bksp} {enter}']}
// the part of the panel's header that always stays on the screen, whatever the visitor does with it
const KEEP_PX = 160
const LONG_PRESS_MS = 380
const REPEAT_DELAY_MS = 400
const REPEAT_MS = 60
const DOUBLE_TAP_MS = 400
// The layouts are generated data, extracted from the keyboard's source application by tools/gboard/gboard_layouts.py (see its README): for every language the
// rows of its tablet keyboard (number row, tab, caps lock, enter, shift...) with the weight of every key, what a long press on a key
// offers and the two pages of symbols. One file per language, loaded when it is needed.
const LANGUAGE_FILES = import.meta.glob('./gboard/layouts/*.json')
const FALLBACK_LAYOUT = 'en-US'
// A row of the tablet layouts is 786 weight units wide (the layout resource: `weightSum="786"`)
const ROW_WEIGHT = 786
const BOTTOM = '{sym1} {space} {left} {right} {sym1}'
const BOTTOM_SYM = '{abc} {numpad} {space} {left} {right} {abc}'
// the bottom row (res/layout/..04f6): 58 for each arrow, 65 for the key at the right; the switch to the symbols is 58 plus the one of
// the emoji key, which is not here (user: "useless and hard"), the space bar takes the rest
const BOTTOM_WEIGHTS = [90.5, 0, 58, 58, 65]
// the symbols pages (res/layout/..0512): back to the letters 90.5, the digit keyboard 90.5, space, the arrows, back to the letters 65
const BOTTOM_SYM_WEIGHTS = [90.5, 90.5, 0, 58, 58, 65]
const FUNCTION_TOKENS = {tab: '{tab}', bksp: '{bksp}', lock: '{lock}', enter: '{enter}', shift: '{shift}', sym1: '{sym1}', sym2: '{sym2}',
  none: '{gap}', 'icon-sym1': '{pagecaps}', 'icon-sym2': '{pagecaps}'}

const css = `${keyboardCss}
#ut-kbd-layer { position: absolute; inset: 0; z-index: 2147483647; pointer-events: none; overflow: hidden;
  font-family: sans-serif; }
#ut-kbd-layer * { box-sizing: border-box; -webkit-user-select: none; user-select: none; touch-action: none; }
#ut-kbd-toggle { position: absolute; width: ${BUTTON_PX}px; height: ${BUTTON_PX}px; border-radius: 12px; border: 0;
  background: rgba(30, 30, 30, .85); color: #fff; font-size: 28px; pointer-events: auto; cursor: pointer; padding: 0; }
#ut-kbd-panel { position: absolute; background: #222; border-radius: 10px; box-shadow: 0 4px 24px #000a;
  pointer-events: auto; display: none; flex-direction: column; overflow: hidden; will-change: transform; }
#ut-kbd-panel.ut-moving { box-shadow: none; }
#ut-kbd-header { flex: 0 0 ${HEADER_PX}px; height: ${HEADER_PX}px; background: #111; color: #ccc; display: flex;
  align-items: center; justify-content: space-between; padding: 0 0 0 14px; cursor: move; font-size: 14px; }
#ut-kbd-close { width: ${HEADER_PX}px; height: ${HEADER_PX}px; border: 0; background: transparent; color: #fff;
  font-size: 22px; cursor: pointer; }
#ut-kbd-body { flex: 1 1 auto; min-height: 0; padding: 8px; background: #eff4fa; }
#ut-kbd-body .simple-keyboard { height: 100%; background: transparent; padding: 0; }
#ut-kbd-body .hg-rows { height: 100%; display: flex; flex-direction: column; }
#ut-kbd-body .hg-row { flex: 1 1 0; display: flex; margin-bottom: 8px; }
#ut-kbd-body .hg-row .hg-button:not(:last-child) { margin-right: 8px; }
#ut-kbd-body .hg-row:last-child { margin-bottom: 0; }
#ut-kbd-body .hg-button { height: auto; font-size: clamp(12px, 2.4vmin, 40px); }
/* The size of every key is set by sizeKeys() below (tablet geometry); here only the colours. */
#ut-kbd-body .hg-button { position: relative; transition: background-color .08s; border: 0; border-radius: 10px; box-shadow: none; color: #1f1f1f; }
#ut-kbd-body .hg-button[data-skbtn="{enter}"] { border-radius: 999px; background: #0b57cf !important; color: #fff; }
#ut-kbd-body .hg-button[data-skbtn="{enter}"]:hover { background: #0a4cb4 !important; }
/* feedback: a key lights up under the pointer and while it is pressed */
#ut-kbd-body .hg-button:hover { background: #e3e7ec !important; }
#ut-kbd-body .hg-button.ut-down, #ut-kbd-body .hg-button.hg-activeButton { background: #b8c4d6 !important; }
#ut-kbd-preview { position: absolute; display: none; min-width: ${BUTTON_PX}px; height: ${BUTTON_PX * 1.3}px; padding: 0 10px;
  align-items: center; justify-content: center; background: #fff; color: #111; font-size: 34px; border-radius: 10px;
  box-shadow: 0 2px 14px #000a; pointer-events: none; }
#ut-kbd-body .hg-button, #ut-kbd-body .hg-button.hg-standardBtn[data-skbtn] { min-width: 0; max-width: none; } /* simple-keyboard caps the @ key at 60 px */
#ut-kbd-body .hg-button[data-skbtn^="{"][data-skbtn$="}"] { background: #e1e3e0; }
#ut-kbd-body .hg-button[data-skbtn="{space}"][data-skbtn="{space}"] { background: #fff; }
#ut-kbd-body .hg-button[data-skbtn="{gap}"][data-skbtn="{gap}"] { visibility: hidden; }
.ut-hint { position: absolute; top: 3px; right: 6px; font-size: .55em; line-height: 1; color: #8a8f98; pointer-events: none; }
#ut-kbd-body.ut-numeric .hg-button { flex: 1 1 0; }
#ut-kbd-body .hg-button.ut-on { background: #4a90e2 !important; color: #fff; }
#ut-kbd-alts { position: absolute; display: none; flex-wrap: wrap; justify-content: center; max-width: 80%; background: #fff; border-radius: 10px; box-shadow: 0 4px 24px #000a;
  padding: 4px; pointer-events: auto; gap: 4px; }
#ut-kbd-alts span { min-width: ${BUTTON_PX}px; height: ${BUTTON_PX}px; display: flex; align-items: center;
  justify-content: center; border-radius: 8px; font-size: 26px; color: #111; }
#ut-kbd-alts span.on { background: #4a90e2; color: #fff; }
#ut-kbd-grip { position: absolute; right: 0; bottom: 0; width: 36px; height: 36px; cursor: nwse-resize;
  background: linear-gradient(135deg, transparent 50%, #888 50%, #888 58%, transparent 58%, transparent 70%, #888 70%,
  #888 78%, transparent 78%); }`


let hostEl, layer, toggle, panel, closeButton, body, grip, keyboard, alts, preview
let enabled = false
let config = {enabled: true, button_position: 'bottom-right', layout: FALLBACK_LAYOUT}
let open = false
let openedByUser = false
let shift = false
let caps = false
let numeric = false
let manualNumeric = false // the visitor opened the digit keyboard from the symbols page
let page = 'letters' // letters | sym1 | sym2
let lastShiftTap = 0
const box = {x: 0, y: 0, w: 0, h: 0} // the panel, in the coordinates of the layer

const send = payload => {
  try { window.utKey(JSON.stringify(payload)) } catch (e) { /* the viewer is not listening */ }
}

// ── geometry ────────────────────────────────────────────────────────────

const limits = () => {
  const W = layer.clientWidth
  const H = layer.clientHeight
  return {
    W, H,
    minW: Math.min(W, Math.max(300, W * 0.3)),
    minH: Math.min(H, Math.max(HEADER_PX + 110, H * 0.18)),
  }
}

/** Puts the panel back inside the screen and inside its size limits: called after anything that could break them. */
let placed = {x: 0, y: 0}
function clamp(moveOnly = false) {
  const {W, H, minW, minH} = limits()
  box.w = Math.min(Math.max(box.w, minW), W)
  box.h = Math.min(Math.max(box.h, minH), H)
  // partly off the screen is allowed (to reach what the keyboard covers), but not out of reach: the top of the panel
  // (its header) stays on the screen, and so do KEEP_PX of its width
  const keep = Math.min(KEEP_PX, box.w)
  box.x = Math.min(Math.max(box.x, keep - box.w), W - keep)
  box.y = Math.min(Math.max(box.y, 0), H - HEADER_PX)
  if (moveOnly) {
    // while the panel is dragged only a compositor transform changes (no layout of the ~40 keys at every pointer event)
    panel.style.transform = `translate3d(${box.x - placed.x}px, ${box.y - placed.y}px, 0)`
    return
  }
  panel.style.transform = ''
  placed = {x: box.x, y: box.y}
  panel.style.left = `${box.x}px`
  panel.style.top = `${box.y}px`
  panel.style.width = `${box.w}px`
  panel.style.height = `${box.h}px`
}

function placeToggle() {
  const p = POSITIONS.includes(config.button_position) ? config.button_position : 'bottom-right'
  const margin = 12
  const W = layer.clientWidth
  const H = layer.clientHeight
  const x = p.includes('left') ? margin : (p.includes('right') ? W - BUTTON_PX - margin : (W - BUTTON_PX) / 2)
  const y = p.startsWith('top') ? margin : (p.startsWith('bottom') ? H - BUTTON_PX - margin : (H - BUTTON_PX) / 2)
  toggle.style.left = `${Math.max(0, x)}px`
  toggle.style.top = `${Math.max(0, y)}px`
}

// The layer changes size when the window is turned (a vertical panel: 1920x1080 becomes 1080x1920) or its screen changes mode: the
// panel keeps its place and size relative to the screen, instead of staying where it was in pixels and being squeezed back inside
let lastSize = null
function resized() {
  const W = layer.clientWidth
  const H = layer.clientHeight
  if (lastSize && lastSize.W && lastSize.H && (lastSize.W !== W || lastSize.H !== H)) {
    const sx = W / lastSize.W
    const sy = H / lastSize.H
    box.x *= sx; box.w *= sx; box.y *= sy; box.h *= sy
  }
  lastSize = {W, H}
  placeToggle()
  clamp()
}

function defaultBox() {
  const {W, H} = limits()
  box.w = W * 0.7
  box.h = H * 0.35
  box.x = (W - box.w) / 2
  box.y = H - box.h - BUTTON_PX - 24
}

/** A movement on the screen, in the coordinates of the layer: the content may be rotated and flipped by the page. */
function toLayer(dx, dy) {
  const rotation = +(document.body.getAttribute('r') || 0)
  const flip = +(document.getElementById('content-root')?.getAttribute('f') || 0)
  if (flip === 1) dx = -dx
  if (flip === 2) dy = -dy
  switch (rotation) {
    case 1: return [dy, -dx]
    case 2: return [-dx, -dy]
    case 3: return [-dy, dx]
    default: return [dx, dy]
  }
}

// A button of the keyboard must not take the focus from the field being typed in, so its touchstart is cancelled (see
// build()); and a cancelled touchstart also cancels the click a tap would turn into, which made these buttons dead under a
// real finger (found on the test VM, invisible with synthesized mouse events). So the tap is taken from pointer events,
// which are not affected: a press and a release on the same button.
function onTap(button, action) {
  let pressed = false
  button.addEventListener('pointerdown', () => { pressed = true })
  button.addEventListener('pointerleave', () => { pressed = false })
  button.addEventListener('pointercancel', () => { pressed = false })
  button.addEventListener('pointerup', () => {
    if (!pressed) return
    pressed = false
    action()
  })
}

/** The 1 Euro filter (Casiez et al.) of one coordinate: the cutoff grows with the speed, so it is steady when slow and quick when fast. */
function oneEuro(start, time, minCutoff = 1.5, beta = 0.05, derivativeCutoff = 1) {
  let value = start
  let speed = 0
  let last = time
  const alpha = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt))
  return (next, now) => {
    const dt = Math.max((now - last) / 1000, 0.001)
    last = now
    speed += alpha(derivativeCutoff, dt) * ((next - value) / dt - speed)
    value += alpha(minCutoff + beta * Math.abs(speed), dt) * (next - value)
    return value
  }
}

function drag(handle, onMove, moveOnly = false) {
  handle.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return
    event.preventDefault()
    handle.setPointerCapture(event.pointerId)
    let lastX = event.clientX
    let lastY = event.clientY
    // a finger that is nearly still sends noise (and the input prediction extrapolates it): the position is smoothed by a 1 Euro filter, which
    // smooths a lot at low speed (no more jumping) and hardly at all when the finger is fast (no lag)
    const filterX = oneEuro(event.clientX, event.timeStamp)
    const filterY = oneEuro(event.clientY, event.timeStamp)
    // pointermove is already aligned to the frames by the browser: applying at once (not from a requestAnimationFrame of
    // our own) saves one frame of lag behind the finger; no shadow while it moves (a big blur to repaint)
    panel.classList.add('ut-moving')
    const move = e => {
      const x = filterX(e.clientX, e.timeStamp)
      const y = filterY(e.clientY, e.timeStamp)
      const [dx, dy] = toLayer(x - lastX, y - lastY)
      lastX = x
      lastY = y
      onMove(dx, dy)
      clamp(moveOnly)
    }
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      panel.classList.remove('ut-moving')
      clamp()
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  })
}

// ── keys ────────────────────────────────────────────────────────────────

const SPECIAL = {'{bksp}': 'Backspace', '{enter}': 'Enter', '{tab}': 'Tab', '{left}': 'ArrowLeft', '{right}': 'ArrowRight'}
const REPEATING = new Set(['{bksp}', '{left}', '{right}'])
const DISPLAY = {'{bksp}': '⌫', '{enter}': '⏎', '{shift}': '⇧', '{lock}': '⇪', '{tab}': '⇥', '{space}': ' ',
  '{sym1}': '?123', '{sym2}': '=\\<', '{abc}': 'ABC', '{left}': '◀', '{right}': '▶', '{hide}': '⌨', '{numpad}': '▦', '{pagecaps}': ' ', '{gap}': ' '}


const languages = new Map() // language -> promise of its file
function loadLanguage(lang) {
  if (!languages.has(lang)) {
    const load = LANGUAGE_FILES[`./gboard/layouts/${lang}.json`]
    languages.set(lang, load ? load().then(module => module.default) : Promise.reject(new Error(`no keyboard layout for ${lang}`)))
  }
  return languages.get(lang)
}

/** The capital of a key (a sharp s stays one character); the file says it when it is not just that (a Turkish i). */
const capital = text => { const up = text.toUpperCase(); return [...up].length === [...text].length ? up : text }

/** One page of the keyboard, for simple-keyboard (`layout`: its rows of keys) and for us (`meta`: what each key is: its weight and popup). */
function pageOf(rows, {mode = 'default', bottom = BOTTOM, weights = BOTTOM_WEIGHTS}) {
  const layout = []
  const meta = []
  const display = {}
  for (const row of rows) {
    const tokens = []
    const keys = []
    for (const key of row) {
      if (key.f) {
        tokens.push(FUNCTION_TOKENS[key.f] ?? '{gap}')
        keys.push({w: key.w})
      } else if (!key.k || /\s/.test(key.k)) {
        tokens.push('{gap}')
        keys.push({w: key.w})
      } else {
        // caps lock: the capitals; shift: the capitals and, on a tablet, the symbols of the digits and the punctuation (as on a PC)
        const caps = key.u ?? capital(key.k)
        const text = mode === 'shift' ? (key.s ?? caps) : (mode === 'caps' ? caps : key.k)
        tokens.push(text)
        if (key.t) display[text] = key.t
        const capsPopup = key.L ?? key.l?.map(capital)
        const popup = mode === 'shift' ? (key.S ?? capsPopup) : (mode === 'caps' ? capsPopup : key.l)
        keys.push({w: key.w, popup: popup ?? []})
      }
    }
    layout.push(tokens.join(' '))
    meta.push(keys)
  }
  layout.push(bottom)
  meta.push(bottom.split(' ').map((token, i) => ({w: weights[i]})))
  return {layout, meta, display}
}

/** Everything the keyboard needs for a layout setting: its four pages. */
async function buildLayout(id) {
  let {lang, variant} = parseLayoutId(id)
  let record
  try { record = await loadLanguage(lang) } catch (e) { console.warn('keyboard:', e.message); lang = FALLBACK_LAYOUT; variant = undefined; record = await loadLanguage(lang) }
  const rows = record.variants[variant] ?? Object.values(record.variants)[0]
  const symbols = record.symbols ?? SYMBOL_PAGES
  const pages = {
    default: pageOf(rows, {}),
    caps: pageOf(rows, {mode: 'caps'}),
    shift: pageOf(rows, {mode: 'shift'}),
    sym1: pageOf(symbols[0], {bottom: BOTTOM_SYM, weights: BOTTOM_SYM_WEIGHTS}),
    sym2: pageOf(symbols[1], {bottom: BOTTOM_SYM, weights: BOTTOM_SYM_WEIGHTS}),
  }
  const display = {}
  for (const name of Object.keys(pages)) Object.assign(display, pages[name].display)
  return {pages, display, simple: Object.fromEntries(Object.entries(pages).map(([name, p]) => [name, p.layout]))}
}

// Tablet geometry: a key is as wide as its weight says out of the 786 of the row (the layout resource of the keyboard), so tab,
// backspace, caps lock, enter and shift keep the size they have there whatever the language, and every row ends where the others do
let current = null // what buildLayout() returned for the layout on show
const GAP_PX = 8

/** Sets the size of every key of the page on show (called after every render): rows as tall as each other, keys as wide as their weight. */
function sizeKeys() {
  if (!keyboard) return // simple-keyboard renders once while it is being constructed
  const meta = numeric ? null : current?.pages[keyboard.options.layoutName]?.meta
  body.querySelectorAll('.hg-row').forEach((el, i) => {
    const keys = meta?.[i]
    el.style.justifyContent = 'flex-start'
    if (!keys) { el.querySelectorAll('.hg-button').forEach(button => { button.style.flex = '' }); return }
    const total = Math.max(ROW_WEIGHT, keys.reduce((sum, key) => sum + key.w, 0))
    el.querySelectorAll('.hg-button').forEach((button, j) => {
      const key = keys[j]
      button.utKey = key
      // the width is the key's share of the row less its share of the gaps between the keys, so a row is flush to both sides
      button.style.flex = !key.w ? '1 1 0' : `0 0 calc(${(100 * key.w / total).toFixed(3)}% - ${(GAP_PX * (1 - key.w / total)).toFixed(2)}px)`
    })
  })
}

function layoutOf() {
  return current?.simple ?? {default: ['{space}']}
}

const layoutName = () => page !== 'letters' ? page : (caps ? 'caps' : (shift ? 'shift' : 'default'))

/** What simple-keyboard is told after every change of state: the page, which keys are lit (shift, caps) and the key size. */
function appearance() {
  body.classList.toggle('ut-numeric', numeric)
  const themes = []
  if (shift && !caps) themes.push({class: 'ut-on', buttons: '{shift}'})
  if (caps) themes.push({class: 'ut-on', buttons: '{lock}'})
  return {layoutName: numeric ? 'default' : layoutName(), display: DISPLAY, buttonTheme: themes}
}

function refreshLayout() {
  keyboard.setOptions(appearance())
}

function typeKey(button) {
  if (button === '{shift}') {
    const now = Date.now()
    if (caps) { caps = false; shift = false } else if (shift && now - lastShiftTap < DOUBLE_TAP_MS) { caps = true; shift = false } else { shift = !shift }
    lastShiftTap = now
    refreshLayout()
    return
  }
  if (button === '{hide}') { setOpen(false); return }
  if (button === '{pagecaps}') { page = page === 'sym1' ? 'sym2' : 'sym1'; refreshLayout(); return }
  if (button === '{numpad}') { manualNumeric = true; numeric = true; applyLayout(); return }
  if (button === '{lock}') { caps = !caps; shift = false; refreshLayout(); return }
  if (button === '{abc}' && manualNumeric) { manualNumeric = false; numeric = false; page = 'letters'; applyLayout(); return }
  if (button === '{sym1}' || button === '{sym2}' || button === '{abc}') {
    page = button === '{abc}' ? 'letters' : button.slice(1, -1)
    refreshLayout()
    return
  }
  if (SPECIAL[button]) send({key: SPECIAL[button]})
  else if (button === '{space}') send({text: ' '})
  else if (!/^\{.+\}$/.test(button)) send({text: button}) // a lone '{' is a character
  if (shift && !caps) { shift = false; refreshLayout() }
}

/** The variants a long press on a key offers (a symbol, accents...), in the case of the key. */
function variantsOf(btn) {
  const key = btn.getAttribute('data-skbtn')
  if (/^\{.+\}$/.test(key)) return []
  return btn.utKey?.popup ?? []
}

/** Draws, on every key that has variants, the first of them in grey at the top right (called by simple-keyboard after each render). */
function addHints() {
  for (const button of body.querySelectorAll('.hg-button')) {
    button.querySelector('.ut-hint')?.remove()
    // the key at caps lock's place on the symbols pages has an icon (a chevron over a bar)
    if (button.getAttribute('data-skbtn') === '{pagecaps}' && !button.querySelector('svg')) {
      button.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 24 24" width="1.4em" height="1.4em" fill="currentColor"><path d="M12,8.41L16.59,13 18,11.59l-6,-6 -6,6L7.41,13 12,8.41zM6,18h12v-2H6v2z"/></svg>')
    }
    const key = button.getAttribute('data-skbtn')
    const first = variantsOf(button)[0]
    // on the letter page only a letter carries a hint, and only when it is a symbol (`q` has `%`, `e` has `|`; its accents
    // are only in the popup); the symbol pages show the first variant of every key
    const shown = first && (page !== 'letters' || (/^\p{L}$/u.test(key) && !/\p{L}/u.test(first)))
    if (shown) button.appendChild(Object.assign(document.createElement('span'), {className: 'ut-hint', textContent: first}))
  }
}

function offsetIn(element, ancestor) {
  let x = 0
  let y = 0
  for (let e = element; e && e !== ancestor; e = e.offsetParent) { x += e.offsetLeft; y += e.offsetTop }
  return [x, y]
}

function showPreview(btn) {
  const text = btn.querySelector('span:not(.ut-hint)')?.textContent ?? ''
  if (!text.trim()) return
  preview.textContent = text
  preview.style.display = 'flex'
  const [bx, by] = offsetIn(btn, panel)
  const W = layer.clientWidth
  const x = box.x + bx + btn.offsetWidth / 2 - preview.offsetWidth / 2
  preview.style.left = `${Math.min(Math.max(x, 0), Math.max(0, W - preview.offsetWidth))}px`
  preview.style.top = `${Math.max(box.y + by - preview.offsetHeight - 4, 0)}px`
}

function openVariants(press) {
  const list = variantsOf(press.btn)
  if (!list.length) return
  preview.style.display = 'none'
  alts.replaceChildren(...list.map(v => Object.assign(document.createElement('span'), {textContent: v})))
  alts.style.display = 'flex'
  const [bx, by] = offsetIn(press.btn, panel)
  const W = layer.clientWidth
  // the first variant is right above the key, the others follow to the side (pulled back when they would leave the screen)
  const x = box.x + bx
  const y = box.y + by - alts.offsetHeight - 6
  alts.style.left = `${Math.min(Math.max(x, 0), Math.max(0, W - alts.offsetWidth))}px`
  alts.style.top = `${Math.max(y, 0)}px`
  press.long = true
  press.origin = [box.x + bx + press.btn.offsetWidth / 2, box.y + by + press.btn.offsetHeight / 2] // where the finger went down, in the layer
  pickVariant(press, null)
}

/** The variant under the finger: only the movement ALONG the popup counts. The finger does not have to reach the popup, it
 *  can slide sideways on the key row it is on; the popup's row is the nearest one to the finger (a single row ignores the height). */
function pickVariant(press, e) {
  const [dx, dy] = e ? toLayer(e.clientX - press.from[0], e.clientY - press.from[1]) : [0, 0]
  const px = press.origin[0] + dx
  const py = press.origin[1] + dy
  const spans = [...alts.children]
  const rows = [...new Set(spans.map(sp => sp.offsetTop))]
  // the row nearest to the finger's height (the bottom row is the one above the key, a finger on the key row or below it stays on it)
  const rowTop = rows.length === 1 ? rows[0] : rows.reduce((best, top) => Math.abs(alts.offsetTop + top + spans[0].offsetHeight / 2 - py) < Math.abs(alts.offsetTop + best + spans[0].offsetHeight / 2 - py) ? top : best, rows[0])
  const inRow = spans.filter(sp => sp.offsetTop === rowTop)
  const x = px - alts.offsetLeft
  let chosen = inRow[0]
  for (const sp of inRow) if (x >= sp.offsetLeft) chosen = sp
  for (const sp of spans) sp.classList.toggle('on', sp === chosen)
  return chosen
}

/** The behaviour of the keys of the panel: typed on release, long press for the variants, repeat on backspace. */
function wireKeys(host) {
  let press = null
  const stop = () => {
    if (!press) return null
    const p = press
    press = null
    clearTimeout(p.timer)
    clearInterval(p.repeat)
    alts.style.display = 'none'
    preview.style.display = 'none'
    p.btn.classList.remove('ut-down')
    return p
  }
  host.addEventListener('pointerdown', e => {
    const btn = e.target.closest('.hg-button')
    if (!btn) return
    stop()
    const key = btn.getAttribute('data-skbtn')
    press = {btn, key, long: false, timer: null, repeat: null, from: [e.clientX, e.clientY]}
    btn.classList.add('ut-down')
    if (variantsOf(btn).length) showPreview(btn) // only a key with other symbols behind it gets a bubble (user)
    if (REPEATING.has(key)) {
      typeKey(key)
      press.timer = setTimeout(() => { press.repeat = setInterval(() => typeKey(key), REPEAT_MS) }, REPEAT_DELAY_MS)
    } else if (variantsOf(btn).length) {
      press.timer = setTimeout(() => openVariants(press), LONG_PRESS_MS)
    }
  })
  window.addEventListener('pointermove', e => {
    if (!press || !press.long) return
    pickVariant(press, e)
  })
  window.addEventListener('pointerup', e => {
    const on = press?.long ? pickVariant(press, e) : null // before stop() hides the variants
    const p = stop()
    if (!p || REPEATING.has(p.key)) return
    if (p.long) {
      if (on) { send({text: on.textContent}); if (shift && !caps) { shift = false; refreshLayout() } }
      return
    }
    if (e.target.closest?.('.hg-button') === p.btn) typeKey(p.key)
  })
  window.addEventListener('pointercancel', stop)
}

let layoutRequest = 0
async function applyLayout() {
  if (numeric) {
    keyboard.setOptions({layout: manualNumeric ? NUMERIC_MANUAL : NUMERIC, ...appearance()})
    return
  }
  const request = ++layoutRequest
  let next
  try { next = await buildLayout(config.layout) } catch (e) { console.error('keyboard layout', e); return }
  if (request !== layoutRequest || numeric) return // another layout was asked for meanwhile, or a number field took over
  current = next
  keyboard.setOptions({layout: layoutOf(), display: {...DISPLAY, ...current.display}, ...appearance()})
}

// ── showing and hiding ──────────────────────────────────────────────────

function render() {
  const visible = enabled && open
  panel.style.display = visible ? 'flex' : 'none'
  toggle.style.display = enabled ? 'block' : 'none'
  if (visible) clamp()
  else setContentShift(0)
}

function setOpen(value, byUser = false) {
  open = value
  openedByUser = value && byUser
  render()
}

function build() {
  const style = document.createElement('style')
  style.textContent = css
  document.head.appendChild(style)

  layer = document.createElement('div')
  layer.id = 'ut-kbd-layer'
  // inside the rotated and flipped part of the page, so the keyboard follows the content
  ;(document.getElementById('orient-intro') ?? document.body).appendChild(layer)

  toggle = document.createElement('button')
  toggle.id = 'ut-kbd-toggle'
  toggle.textContent = '⌨'
  toggle.setAttribute('aria-label', 'Keyboard')
  onTap(toggle, () => {
    // opened with the button, the keyboard is where it always starts (it may have been dragged or placed under a field)
    if (!open) { defaultBox(); clamp() }
    setOpen(!open, true)
  })

  panel = document.createElement('div')
  panel.id = 'ut-kbd-panel'
  const header = document.createElement('div')
  header.id = 'ut-kbd-header'
  header.innerHTML = '<span>Keyboard</span>'
  closeButton = document.createElement('button')
  closeButton.id = 'ut-kbd-close'
  closeButton.textContent = '✕'
  closeButton.setAttribute('aria-label', 'Close the keyboard')
  onTap(closeButton, () => setOpen(false))
  header.appendChild(closeButton)
  body = document.createElement('div')
  body.id = 'ut-kbd-body'
  const host = document.createElement('div')
  host.className = 'simple-keyboard'
  body.appendChild(host)
  hostEl = host
  grip = document.createElement('div')
  grip.id = 'ut-kbd-grip'
  panel.append(header, body, grip)
  alts = document.createElement('div')
  alts.id = 'ut-kbd-alts'
  preview = document.createElement('div')
  preview.id = 'ut-kbd-preview'
  layer.append(panel, toggle, alts, preview)

  // Taps on the keyboard must not move the focus out of the field being typed in
  for (const element of [toggle, panel]) {
    element.addEventListener('pointerdown', e => e.preventDefault())
    element.addEventListener('mousedown', e => e.preventDefault())
    element.addEventListener('touchstart', e => e.preventDefault(), {passive: false})
  }

  keyboard = new Keyboard(host, {
    onRender: () => { sizeKeys(); addHints() },
    onKeyPress: () => {}, // the keys are handled by wireKeys(): they type on release
    theme: 'hg-theme-default hg-layout-default',
    layout: layoutOf(),
    display: DISPLAY,
    preventMouseDownDefault: true,
    preventMouseUpDefault: true,
    stopMouseDownPropagation: false,
    useTouchEvents: false,
  })

  sizeKeys()
  addHints()
  wireKeys(host)
  drag(header, (dx, dy) => { box.x += dx; box.y += dy }, true)
  drag(grip, (dx, dy) => { box.w += dx; box.h += dy })

  new ResizeObserver(resized).observe(layer)
  defaultBox()
  lastSize = {W: layer.clientWidth, H: layer.clientHeight}
  placeToggle()
  clamp()
}

// ── what the viewer calls ───────────────────────────────────────────────

export function configure(settings) {
  config = {...config, ...settings}
  if (!layer) build()
  enabled = !!config.enabled
  if (!enabled) open = false
  placeToggle()
  applyLayout()
  render()
}

/** `kind` is the type of the field that has the focus (text, password, number, email, tel...) or null. */
/** A field that has no room for the keyboard below it is brought up instead: the whole content of the window
 *  slides up, off the screen if it has to, and slides back when the keyboard goes. The transform is applied to #stage and
 *  #home-frame by the page's stylesheet (boot-screen.html), with the animation. */
let contentShift = 0
let anchor = null // the last text field that was touched: {top, bottom, at}, in layer coordinates, BEFORE any shift
function setContentShift(px) {
  const wrap = layer.parentElement
  if (!wrap) return
  contentShift = px
  if (px) {
    wrap.style.setProperty('--ut-kbd-shift', `${px}px`)
    wrap.setAttribute('data-kbd-shift', '')
  } else if (wrap.hasAttribute('data-kbd-shift')) {
    wrap.style.setProperty('--ut-kbd-shift', '0px')
    setTimeout(() => { if (!contentShift) wrap.removeAttribute('data-kbd-shift') }, 500)
  }
}

function placeBelowAnchor() {
  if (!anchor || !open) return
  const {W, H} = limits()
  const gap = 12
  box.x = (W - box.w) / 2 // always centred on the screen
  if (anchor.bottom + gap + box.h <= H) {
    setContentShift(0)
    box.y = anchor.bottom + gap
  } else {
    // not comfortable: the field goes to the top of the screen and the keyboard sits under it
    const margin = Math.max(gap, H * 0.08)
    setContentShift(Math.min(0, margin - anchor.top))
    box.y = Math.min(margin + (anchor.bottom - anchor.top) + gap, H - box.h)
  }
  clamp()
}

/** The keyboard goes just below the text field that was touched (the content slides up when there is no room).
 *  `frame` is the iframe element that holds the field, `rect` = [left, top, width, height, frameInnerWidth, frameInnerHeight]
 *  of the field inside that frame. */
export function placeNear(frame, rect) {
  if (!enabled || !layer || !frame || !rect) return
  const [, fy, , fh] = rect
  const [, oy] = offsetIn(frame, layer.offsetParent)
  const m = new DOMMatrix(getComputedStyle(frame).transform === 'none' ? undefined : getComputedStyle(frame).transform)
  const k = Math.hypot(m.a, m.b) || 1 // a page shown with a zoom is an iframe scaled by CSS
  anchor = {top: oy + fy * k, bottom: oy + (fy + fh) * k, at: Date.now()}
  placeBelowAnchor()
}

export function focusChanged(on, kind) {
  if (!enabled || !layer) return
  if (on) {
    const wantsNumeric = kind === 'number' || kind === 'tel'
    if (!manualNumeric && wantsNumeric !== numeric) { numeric = wantsNumeric; applyLayout() }
    if (manualNumeric && !wantsNumeric) { /* a text field after the digits: back to the letters */ manualNumeric = false; numeric = false; applyLayout() }
    if (!open) setOpen(true, false) // already open (by the button too): leave it as it is
    if (anchor && Date.now() - anchor.at < 1500) placeBelowAnchor() // the position arrived before the keyboard was open
  } else if (!openedByUser) {
    setOpen(false) // what opened itself closes itself; what the visitor opened stays until they close it
  }
}

window.utKeyboard = {configure, focusChanged, placeNear}
