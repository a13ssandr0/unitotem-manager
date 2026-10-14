/**
 * The on-screen keyboard of a touch panel, drawn over the page the viewer shows.
 *
 * Loaded by the viewer's own page (boot-screen.html, from the viewer plugin) as a module from this node's manager, and
 * driven from outside: the viewer calls `focusChanged()` when a text field gets or loses the focus in any frame of the
 * page (it can see that, this page cannot: the content is usually from another origin), and `configure()` with the
 * settings of the window. Keys go back through the function `utKey` that the viewer adds to the page; it types them into
 * whatever has the focus.
 *
 * Whoever touches the screen is treated as the worst possible user: the keyboard can be moved and resized, but only
 * inside the screen, never below a minimum size, and its header (with the close button) cannot shrink or leave. The
 * limits are applied after every gesture, on every change of the size of the screen and on every rotation, not just
 * while dragging. Moving and resizing is done here with pointer events instead of a library because the content can be
 * rotated and flipped with CSS (the page's orientation settings), and the libraries do not follow a transformed parent.
 */
import Keyboard from 'simple-keyboard'
import keyboardCss from 'simple-keyboard/build/css/index.css?inline'
import SimpleKeyboardLayouts from 'simple-keyboard-layouts'

const BUTTON_PX = 56
const HEADER_PX = 40
const POSITIONS = ['top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right']
const NUMERIC = {default: ['1 2 3', '4 5 6', '7 8 9', '{bksp} 0 {enter}']}

const css = `${keyboardCss}
#ut-kbd-layer { position: absolute; inset: 0; z-index: 2147483647; pointer-events: none; overflow: hidden;
  font-family: sans-serif; }
#ut-kbd-layer * { box-sizing: border-box; -webkit-user-select: none; user-select: none; touch-action: none; }
#ut-kbd-toggle { position: absolute; width: ${BUTTON_PX}px; height: ${BUTTON_PX}px; border-radius: 12px; border: 0;
  background: rgba(30, 30, 30, .85); color: #fff; font-size: 28px; pointer-events: auto; cursor: pointer; padding: 0; }
#ut-kbd-panel { position: absolute; background: #222; border-radius: 10px; box-shadow: 0 4px 24px #000a;
  pointer-events: auto; display: none; flex-direction: column; overflow: hidden; }
#ut-kbd-header { flex: 0 0 ${HEADER_PX}px; height: ${HEADER_PX}px; background: #111; color: #ccc; display: flex;
  align-items: center; justify-content: space-between; padding: 0 0 0 14px; cursor: move; font-size: 14px; }
#ut-kbd-close { width: ${HEADER_PX}px; height: ${HEADER_PX}px; border: 0; background: transparent; color: #fff;
  font-size: 22px; cursor: pointer; }
#ut-kbd-body { flex: 1 1 auto; min-height: 0; padding: 4px; }
#ut-kbd-body .simple-keyboard { height: 100%; background: transparent; padding: 0; }
#ut-kbd-body .hg-rows { height: 100%; display: flex; flex-direction: column; }
#ut-kbd-body .hg-row { flex: 1 1 0; display: flex; margin-bottom: 4px; }
#ut-kbd-body .hg-row:last-child { margin-bottom: 0; }
#ut-kbd-body .hg-button { height: auto; font-size: clamp(12px, 2.4vmin, 40px); }
#ut-kbd-grip { position: absolute; right: 0; bottom: 0; width: 36px; height: 36px; cursor: nwse-resize;
  background: linear-gradient(135deg, transparent 50%, #888 50%, #888 58%, transparent 58%, transparent 70%, #888 70%,
  #888 78%, transparent 78%); }`

const layouts = new SimpleKeyboardLayouts()

let layer, toggle, panel, closeButton, body, grip, keyboard
let enabled = false
let config = {enabled: false, button_position: 'bottom-right', layout: 'english'}
let open = false
let openedByUser = false
let shift = false
let caps = false
let numeric = false
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
function clamp() {
  const {W, H, minW, minH} = limits()
  box.w = Math.min(Math.max(box.w, minW), W)
  box.h = Math.min(Math.max(box.h, minH), H)
  box.x = Math.min(Math.max(box.x, 0), W - box.w)
  box.y = Math.min(Math.max(box.y, 0), H - box.h)
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

function drag(handle, onMove) {
  handle.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return
    event.preventDefault()
    handle.setPointerCapture(event.pointerId)
    let lastX = event.clientX
    let lastY = event.clientY
    const move = e => {
      const [dx, dy] = toLayer(e.clientX - lastX, e.clientY - lastY)
      lastX = e.clientX
      lastY = e.clientY
      onMove(dx, dy)
      clamp()
    }
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      clamp()
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  })
}

// ── keys ────────────────────────────────────────────────────────────────

const SPECIAL = {'{bksp}': 'Backspace', '{enter}': 'Enter', '{tab}': 'Tab'}

function refreshLayout() {
  keyboard.setOptions({layoutName: shift || caps ? 'shift' : 'default'})
}

function onKeyPress(button) {
  if (button === '{shift}') { shift = !shift; refreshLayout(); return }
  if (button === '{lock}') { caps = !caps; refreshLayout(); return }
  if (SPECIAL[button]) { send({key: SPECIAL[button]}) } else if (button === '{space}') { send({text: ' '}) } else if (!button.startsWith('{')) { send({text: button}) }
  if (shift) { shift = false; refreshLayout() }
}

function applyLayout() {
  if (numeric) {
    keyboard.setOptions({layout: NUMERIC, layoutName: 'default'})
    return
  }
  const wanted = layouts.get(config.layout)?.layout ?? layouts.get('english').layout
  keyboard.setOptions({layout: wanted, layoutName: shift || caps ? 'shift' : 'default'})
}

// ── showing and hiding ──────────────────────────────────────────────────

function render() {
  const visible = enabled && open
  panel.style.display = visible ? 'flex' : 'none'
  toggle.style.display = enabled ? 'block' : 'none'
  if (visible) clamp()
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
  onTap(toggle, () => setOpen(!open, true))

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
  grip = document.createElement('div')
  grip.id = 'ut-kbd-grip'
  panel.append(header, body, grip)
  layer.append(panel, toggle)

  // Taps on the keyboard must not move the focus out of the field being typed in
  for (const element of [toggle, panel]) {
    element.addEventListener('pointerdown', e => e.preventDefault())
    element.addEventListener('mousedown', e => e.preventDefault())
    element.addEventListener('touchstart', e => e.preventDefault(), {passive: false})
  }

  keyboard = new Keyboard(host, {
    onKeyPress,
    theme: 'hg-theme-default hg-layout-default',
    layout: layouts.get('english').layout,
    display: {'{bksp}': '⌫', '{enter}': '⏎', '{shift}': '⇧', '{lock}': '⇪', '{tab}': '⇥', '{space}': ' '},
    preventMouseDownDefault: true,
    preventMouseUpDefault: true,
    stopMouseDownPropagation: false,
    useTouchEvents: false,
  })

  drag(header, (dx, dy) => { box.x += dx; box.y += dy })
  drag(grip, (dx, dy) => { box.w += dx; box.h += dy })

  new ResizeObserver(() => { placeToggle(); clamp() }).observe(layer)
  defaultBox()
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
export function focusChanged(on, kind) {
  if (!enabled || !layer) return
  if (on) {
    const wantsNumeric = kind === 'number' || kind === 'tel'
    if (wantsNumeric !== numeric) { numeric = wantsNumeric; applyLayout() }
    setOpen(true, false)
  } else if (!openedByUser) {
    setOpen(false) // what opened itself closes itself; what the visitor opened stays until they close it
  }
}

window.utKeyboard = {configure, focusChanged}
