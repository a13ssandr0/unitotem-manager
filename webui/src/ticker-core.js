/**
 * The pure part of the RSS ticker: no DOM, no timers, so node can test it (webui/tests/ticker-core.test.mjs) and the
 * settings dialog can use the very same code to draw its live preview.
 *
 * - `parseTemplate(text)`: the template language, which is Rich/Textual console markup (`[bold red]..[/]`, `[on blue]`,
 *   `[#ff8800]`, `[rgb(1,2,3)]`, `[dim]`, `[italic]`, `[underline]`, `[strike]`, `[/bold]`, `\[` to write a bracket)
 *   plus Python f-string style fields: `{time:HH:mm}`, `{title:.80}`, `{source:>12}`, `{title!u}`, `{{` and `}}` for
 *   braces. The result is a list of tokens; an invalid template gives an `error` and the caller falls back to plain text.
 * - `renderItem(parsed, item, locale)`: the tokens + one headline = a list of `{text, style}` segments. The text of a
 *   feed is only ever put into a segment as TEXT: it is never parsed as markup (a title with `[red]` in it shows
 *   `[red]`), and the page draws segments with `textContent` and validated colours, never with innerHTML.
 * - `selectItems(items, select, now)`: which headlines the bar shows (the newest N, today's, the last S seconds).
 * - `formatTime`: LDML patterns (`HH:mm`, `dd MMM yyyy`, `EEE`) or Python/strftime ones (`%H:%M`) in the window's locale.
 */

// ── colours ──────────────────────────────────────────────────────────────────

/* The 16 colours of the standard terminal palette, in xterm's order: what `color(0)`..`color(15)` mean. The plain names
   (`red`, `green`...) are CSS's, not the dark terminal ones: a crawl must be readable, and nobody writing [red] on a
   web page means #800000. Only the bright_* names, which CSS does not have, come from this table. */
const ANSI16 = ['#000000', '#800000', '#008000', '#808000', '#000080', '#800080', '#008080', '#c0c0c0',
  '#808080', '#ff0000', '#00ff00', '#ffff00', '#0000ff', '#ff00ff', '#00ffff', '#ffffff']
const ANSI_NAMES = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white']
const BRIGHT = Object.fromEntries(ANSI_NAMES.map((n, i) => [`bright_${n}`, ANSI16[8 + i]]))

/* CSS named colours */
const CSS_NAMES = new Set(('black white red green blue yellow cyan magenta aliceblue antiquewhite aqua aquamarine azure ' +
  'beige bisque blanchedalmond blueviolet brown ' +
  'burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson darkblue darkcyan darkgoldenrod ' +
  'darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon ' +
  'darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray ' +
  'dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray grey ' +
  'greenyellow honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue ' +
  'lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen ' +
  'lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen maroon ' +
  'mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen ' +
  'mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab ' +
  'orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum ' +
  'powderblue purple rebeccapurple rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver ' +
  'skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat ' +
  'whitesmoke yellowgreen transparent').split(' '))

const XTERM_LEVELS = [0, 95, 135, 175, 215, 255]

/** The CSS colour for a Rich colour word, or null when it is not one (never anything but a hex colour or a CSS name). */
export function parseColor(word) {
  const w = word.trim().toLowerCase()
  if (BRIGHT[w]) return BRIGHT[w]
  if (CSS_NAMES.has(w)) return w
  if (/^#[0-9a-f]{3}$|^#[0-9a-f]{6}$/.test(w)) return w
  let m = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(w)
  if (m && [m[1], m[2], m[3]].every(n => Number(n) <= 255)) return `rgb(${Number(m[1])},${Number(m[2])},${Number(m[3])})`
  m = /^color\(\s*(\d{1,3})\s*\)$/.exec(w)
  if (m && Number(m[1]) <= 255) {
    const n = Number(m[1])
    if (n < 16) return ANSI16[n]
    if (n >= 232) { const g = 8 + (n - 232) * 10; return `rgb(${g},${g},${g})` }
    const c = n - 16
    return `rgb(${XTERM_LEVELS[Math.floor(c / 36)]},${XTERM_LEVELS[Math.floor(c / 6) % 6]},${XTERM_LEVELS[c % 6]})`
  }
  return null
}

// ── styles ───────────────────────────────────────────────────────────────────

const FLAGS = {
  bold: 'bold', b: 'bold', dim: 'dim', d: 'dim', italic: 'italic', i: 'italic', underline: 'underline', u: 'underline',
  strike: 'strike', s: 'strike', strikethrough: 'strike', reverse: 'reverse', r: 'reverse', overline: 'overline', o: 'overline',
}

/** `bold red on blue` -> {bold: true, color: 'red', bg: 'blue'}; throws a MarkupError on a word it does not know. */
function parseStyle(text) {
  const words = text.trim().split(/\s+/).filter(Boolean)
  const style = {}
  for (let i = 0; i < words.length; i++) {
    const word = words[i].toLowerCase()
    if (word === 'not') {
      const flag = FLAGS[(words[++i] || '').toLowerCase()]
      if (!flag) throw new MarkupError(`"not" must be followed by a style such as bold (in [${text}])`)
      style[flag] = false
    } else if (word === 'on') {
      const color = parseColor(words[++i] || '')
      if (!color) throw new MarkupError(`unknown background colour "${words[i] || ''}" (in [${text}])`)
      style.bg = color
    } else if (FLAGS[word]) {
      style[FLAGS[word]] = true
    } else {
      const color = parseColor(word)
      if (!color) throw new MarkupError(`unknown style or colour "${words[i]}" (in [${text}])`)
      style.color = color
    }
  }
  return style
}

/** Style of a nested tag over the style outside it ('not bold' switches a flag off). */
function mergeStyle(base, add) {
  const out = {...base}
  for (const [k, v] of Object.entries(add)) {
    if (v === false) delete out[k]
    else out[k] = v
  }
  return out
}

export class MarkupError extends Error {}

// ── text format specs of the fields (Python's [[fill]align][width][.precision]) ──

function parseTextSpec(spec) {
  if (!spec) return null
  const m = /^(?:(.)?([<>^]))?(\d+)?(?:\.(\d+))?s?$/su.exec(spec)
  if (!m) throw new MarkupError(`invalid format spec "${spec}" (use [[fill]<|>|^][width][.precision], e.g. ">12" or ".80")`)
  const width = m[3] ? Number(m[3]) : 0
  const precision = m[4] ? Number(m[4]) : null
  if (width > 500 || (precision !== null && precision > 500)) throw new MarkupError(`the format spec "${spec}" is too large`)
  return {fill: m[1] ?? ' ', align: m[2] ?? '<', width, precision}
}

function applyTextSpec(text, spec) {
  if (!spec) return text
  let chars = [...text]
  if (spec.precision !== null && chars.length > spec.precision) {
    chars = spec.precision > 1 ? [...chars.slice(0, spec.precision - 1), '…'] : chars.slice(0, spec.precision)
  }
  const pad = spec.width - chars.length
  if (pad > 0) {
    const fill = spec.fill
    if (spec.align === '>') chars = [...fill.repeat(pad), ...chars]
    else if (spec.align === '^') chars = [...fill.repeat(Math.floor(pad / 2)), ...chars, ...fill.repeat(pad - Math.floor(pad / 2))]
    else chars = [...chars, ...fill.repeat(pad)]
  }
  return chars.join('')
}

const CONVERSIONS = {
  s: t => t,
  u: t => t.toLocaleUpperCase(),
  l: t => t.toLocaleLowerCase(),
  t: t => t.toLocaleLowerCase().replace(/(^|[\s\-"'(“])(\p{L})/gu, (_m, a, b) => a + b.toLocaleUpperCase()),
  c: t => (t ? t.charAt(0).toLocaleUpperCase() + t.slice(1) : t),
}

// ── time ─────────────────────────────────────────────────────────────────────

const pad2 = n => String(n).padStart(2, '0')
const nameCache = new Map()

function intl(locale, options, date) {
  const key = `${locale}|${JSON.stringify(options)}`
  let f = nameCache.get(key)
  if (!f) {
    try { f = new Intl.DateTimeFormat(locale || undefined, options) } catch { f = new Intl.DateTimeFormat(undefined, options) }
    nameCache.set(key, f)
  }
  return f.format(date)
}

function dayPeriod(locale, date) {
  const key = `${locale}|period`
  let f = nameCache.get(key)
  if (!f) {
    try { f = new Intl.DateTimeFormat(locale || undefined, {hour: 'numeric', hour12: true}) } catch { f = new Intl.DateTimeFormat('en', {hour: 'numeric', hour12: true}) }
    nameCache.set(key, f)
  }
  const part = f.formatToParts(date).find(p => p.type === 'dayPeriod')
  return part ? part.value : (date.getHours() < 12 ? 'AM' : 'PM')
}

/* the value of one LDML field letter run, or undefined when unsupported */
const LDML = {
  y: (d, n) => (n === 2 ? pad2(d.getFullYear() % 100) : String(d.getFullYear()).padStart(n === 4 ? 4 : 1, '0')),
  M: (d, n, l) => (n === 1 ? String(d.getMonth() + 1) : n === 2 ? pad2(d.getMonth() + 1)
    : intl(l, {month: n === 3 ? 'short' : n === 4 ? 'long' : 'narrow'}, d)),
  d: (d, n) => (n === 1 ? String(d.getDate()) : pad2(d.getDate())),
  E: (d, n, l) => intl(l, {weekday: n <= 3 || n === 6 ? 'short' : n === 4 ? 'long' : 'narrow'}, d),
  a: (d, _n, l) => dayPeriod(l, d),
  H: (d, n) => (n === 1 ? String(d.getHours()) : pad2(d.getHours())),
  h: (d, n) => { const v = d.getHours() % 12 || 12; return n === 1 ? String(v) : pad2(v) },
  K: (d, n) => { const v = d.getHours() % 12; return n === 1 ? String(v) : pad2(v) },
  k: (d, n) => { const v = d.getHours() || 24; return n === 1 ? String(v) : pad2(v) },
  m: (d, n) => (n === 1 ? String(d.getMinutes()) : pad2(d.getMinutes())),
  s: (d, n) => (n === 1 ? String(d.getSeconds()) : pad2(d.getSeconds())),
}
LDML.L = LDML.M

const MAX_RUN = {y: 4, M: 5, L: 5, d: 2, E: 6, a: 1, H: 2, h: 2, K: 2, k: 2, m: 2, s: 2}

/** Compiles an LDML pattern into a list of parts; throws a MarkupError for an unsupported letter. */
function compileLdml(spec) {
  const parts = []
  let i = 0
  while (i < spec.length) {
    const ch = spec[i]
    if (ch === "'") {
      if (spec[i + 1] === "'") { parts.push({lit: "'"}); i += 2; continue }
      let j = i + 1
      let lit = ''
      for (;; j++) { // inside quotes, '' is an apostrophe
        if (j >= spec.length) throw new MarkupError(`unterminated quote in the time format "${spec}"`)
        if (spec[j] === "'") {
          if (spec[j + 1] === "'") { lit += "'"; j++ } else break
        } else lit += spec[j]
      }
      parts.push({lit})
      i = j + 1
    } else if (/[A-Za-z]/.test(ch)) {
      let n = 1
      while (spec[i + n] === ch) n++
      if (!LDML[ch]) throw new MarkupError(`unsupported time field "${ch}" in "${spec}" (use y M d E a H h m s; quote text as 'text')`)
      if (n > MAX_RUN[ch]) throw new MarkupError(`too many "${ch}" in "${spec}"`)
      parts.push({field: ch, n})
      i += n
    } else {
      parts.push({lit: ch})
      i++
    }
  }
  return parts
}

const STRFTIME = {
  a: (d, l) => intl(l, {weekday: 'short'}, d), A: (d, l) => intl(l, {weekday: 'long'}, d),
  b: (d, l) => intl(l, {month: 'short'}, d), B: (d, l) => intl(l, {month: 'long'}, d),
  d: d => pad2(d.getDate()), e: d => String(d.getDate()).padStart(2, ' '),
  H: d => pad2(d.getHours()), I: d => pad2(d.getHours() % 12 || 12), m: d => pad2(d.getMonth() + 1),
  M: d => pad2(d.getMinutes()), S: d => pad2(d.getSeconds()), p: (d, l) => dayPeriod(l, d),
  y: d => pad2(d.getFullYear() % 100), Y: d => String(d.getFullYear()),
  x: (d, l) => intl(l, {dateStyle: 'short'}, d), X: (d, l) => intl(l, {timeStyle: 'medium'}, d),
  c: (d, l) => intl(l, {dateStyle: 'short', timeStyle: 'medium'}, d), '%': () => '%',
}
const STRFTIME_NOPAD = {d: d => String(d.getDate()), H: d => String(d.getHours()), I: d => String(d.getHours() % 12 || 12),
  m: d => String(d.getMonth() + 1), M: d => String(d.getMinutes()), S: d => String(d.getSeconds())}

function compileStrftime(spec) {
  const parts = []
  for (let i = 0; i < spec.length; i++) {
    if (spec[i] !== '%') { parts.push({lit: spec[i]}); continue }
    let ch = spec[++i]
    let fn
    if (ch === '-') { ch = spec[++i]; fn = STRFTIME_NOPAD[ch] }
    else fn = STRFTIME[ch]
    if (!fn) throw new MarkupError(`unsupported strftime directive "%${ch ?? ''}" in "${spec}"`)
    parts.push({fn})
  }
  return parts
}

/** Compiles a time format spec (LDML, or strftime when it holds a `%`) into a function (Date, locale) -> string. */
export function compileTimeFormat(spec) {
  const text = spec || 'HH:mm'
  const warnings = []
  let parts
  if (text.includes('%')) {
    parts = compileStrftime(text)
  } else {
    parts = compileLdml(text)
    if (/[Hhk]+[^A-Za-z]{0,3}M{1,2}(?!M)/.test(text.replace(/'[^']*'/g, ''))) {
      warnings.push(`In "${text}", MM is the MONTH; minutes are mm (e.g. HH:mm)`)
    }
  }
  const fn = (date, locale) => parts.map(p => (p.lit !== undefined ? p.lit : p.fn ? p.fn(date, locale) : LDML[p.field](date, p.n, locale))).join('')
  fn.warnings = warnings
  return fn
}

/** One-off formatting, for tests and the clock. */
export function formatTime(date, spec, locale = '') {
  return compileTimeFormat(spec)(date, locale)
}

// ── the template ─────────────────────────────────────────────────────────────

export const FIELDS = ['time', 'title', 'source', 'summary', 'link']
export const MAX_TEMPLATE = 500

/**
 * Parses a template. Returns `{tokens, warnings, error}`: on an error `tokens` is the plain-title fallback and `error` says
 * what is wrong and where, so a bad template never breaks the bar and the settings dialog can show the message.
 */
export function parseTemplate(source) {
  const fallback = {tokens: [{t: 'field', name: 'title', conv: null, spec: null, fmt: null}], warnings: [], error: null}
  try {
    if (typeof source !== 'string' || !source.trim()) throw new MarkupError('the template is empty')
    if (source.length > MAX_TEMPLATE) throw new MarkupError(`the template is longer than ${MAX_TEMPLATE} characters`)
    return {...tokenize(source), error: null}
  } catch (e) {
    if (!(e instanceof MarkupError)) throw e
    return {...fallback, error: e.message}
  }
}

function tokenize(src) {
  const tokens = []
  const warnings = []
  const open = [] // normalised text of the open tags
  let text = ''
  const flush = () => { if (text) { tokens.push({t: 'text', v: text}); text = '' } }

  for (let i = 0; i < src.length;) {
    const ch = src[i]
    if (ch === '{') {
      if (src[i + 1] === '{') { text += '{'; i += 2; continue }
      const end = src.indexOf('}', i + 1)
      if (end < 0) throw new MarkupError(`"{" at position ${i + 1} is never closed (write {{ for a brace)`)
      flush()
      const field = parseField(src.slice(i + 1, end), warnings)
      tokens.push(field)
      i = end + 1
    } else if (ch === '}') {
      if (src[i + 1] === '}') { text += '}'; i += 2; continue }
      throw new MarkupError(`single "}" at position ${i + 1} (write }} for a brace)`)
    } else if (ch === '\\') {
      // Rich: n backslashes before a tag: half of them stay, and an odd one escapes the bracket
      let n = 0
      while (src[i + n] === '\\') n++
      if (src[i + n] === '[' && TAG_START.test(src.slice(i + n + 1, i + n + 2))) {
        text += '\\'.repeat(Math.floor(n / 2))
        i += n
        if (n % 2) { text += '['; i++ }
      } else {
        text += '\\'.repeat(n)
        i += n
      }
    } else if (ch === '[' && TAG_START.test(src.slice(i + 1, i + 2))) {
      const end = src.indexOf(']', i + 1)
      const inner = end < 0 ? null : src.slice(i + 1, end)
      if (inner === null || inner.includes('[')) { text += ch; i++; continue } // not a tag: plain text, like Rich
      flush()
      if (inner[0] === '/') {
        const name = inner.slice(1).trim()
        let idx = open.length - 1
        if (name) { // [/] closes the last one, [/bold] the nearest open tag with that style ([/b] closes [bold] too)
          const want = canonical(parseStyle(name))
          while (idx >= 0 && open[idx] !== want) idx--
        }
        if (idx < 0) {
          throw new MarkupError(name ? `closing tag [/${name}] does not match any open tag` : 'closing tag [/] but nothing is open')
        }
        open.splice(idx, 1)
        tokens.push({t: 'close', index: idx})
      } else {
        const norm = inner.trim().toLowerCase()
        if (/^(link|@)/.test(norm)) throw new MarkupError(`[${inner}] is not supported on a ticker (no links or actions)`)
        const style = parseStyle(inner)
        tokens.push({t: 'open', style})
        open.push(canonical(style))
      }
      i = end + 1
    } else {
      text += ch
      i++
    }
  }
  flush()
  if (!tokens.some(t => t.t === 'field')) warnings.push('The template has no {field}: every headline would look the same')
  return {tokens, warnings}
}

const canonical = style => JSON.stringify(Object.entries(style).sort())

const TAG_START = /^[a-zA-Z#/@]$/

function parseField(inner, warnings) {
  const m = /^([A-Za-z_][A-Za-z0-9_]*)(?:!([A-Za-z]))?(?::([\s\S]*))?$/.exec(inner)
  if (!m) throw new MarkupError(`invalid field {${inner}}`)
  const [, name, conv, spec] = m
  if (!FIELDS.includes(name)) throw new MarkupError(`unknown field {${name}} (use ${FIELDS.map(f => `{${f}}`).join(', ')})`)
  if (conv && !CONVERSIONS[conv]) throw new MarkupError(`unknown conversion !${conv} in {${inner}} (use !u upper, !l lower, !t title, !c capitalise)`)
  if (name === 'time') {
    const fmt = compileTimeFormat(spec)
    warnings.push(...fmt.warnings)
    return {t: 'field', name, conv: conv || null, spec: spec ?? null, fmt}
  }
  return {t: 'field', name, conv: conv || null, spec: spec ?? null, fmt: parseTextSpec(spec)}
}

const MAX_FIELD_CHARS = 1000

/**
 * One headline through the tokens: a list of `{text, style}`; the style is `{bold, dim, italic, underline, strike,
 * overline, reverse, color, bg}` with only validated colours in it. Feed text is data, not markup.
 */
export function renderItem(parsed, item, locale = '') {
  const segments = []
  const tags = []
  let current = {}
  const push = text => { if (text) segments.push({text, style: current}) }
  const restyle = () => { current = tags.reduce(mergeStyle, {}) }
  for (const tok of parsed.tokens) {
    if (tok.t === 'text') push(tok.v)
    else if (tok.t === 'open') { tags.push(tok.style); restyle() }
    else if (tok.t === 'close') { tags.splice(tok.index, 1); restyle() }
    else {
      let value
      if (tok.name === 'time') value = Number.isFinite(item.time) ? tok.fmt(new Date(item.time * 1000), locale) : ''
      else value = applyTextSpec(String(item[tok.name] ?? '').slice(0, MAX_FIELD_CHARS), tok.fmt)
      if (tok.conv) value = CONVERSIONS[tok.conv](value)
      push(value)
    }
  }
  return segments
}

/** The plain text of a rendering (for tests, the preview's tooltip and accessibility). */
export const plainText = segments => segments.map(s => s.text).join('')

// ── selection ────────────────────────────────────────────────────────────────

/** Local midnight (ms) of the day of `now`: setHours handles the 23 and 25 hour days of a DST change. */
export function startOfDay(now) {
  const d = new Date(now)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/**
 * The headlines to show now: `{mode: 'count', count: N}` the newest N, `{mode: 'today'}` those since local midnight,
 * `{mode: 'range', seconds: S}` those of the last S seconds. `now` is in ms (the device clock); items are `{time}` in
 * epoch seconds, newest first as the server sends them (sorted again here: the order is the page's contract).
 */
export function selectItems(items, select, now) {
  const sorted = [...items].sort((a, b) => (b.time || 0) - (a.time || 0))
  const mode = select && select.mode
  if (mode === 'today') {
    const from = startOfDay(now) / 1000
    return sorted.filter(i => i.time >= from)
  }
  if (mode === 'range') {
    const from = now / 1000 - (Number(select.seconds) || 0)
    return sorted.filter(i => i.time >= from)
  }
  return sorted.slice(0, Math.max(0, Math.floor(Number(select && select.count) || 0)))
}

/**
 * When the selection can change by itself (an item ages out of the range, midnight passes): the ms timestamp of the
 * next such moment, or null. The page sets ONE timer for it instead of polling.
 */
export function nextExpiry(items, select, now) {
  if (!select) return null
  if (select.mode === 'today') {
    const d = new Date(now)
    d.setHours(24, 0, 0, 0)
    return d.getTime()
  }
  if (select.mode === 'range') {
    const from = now / 1000 - (Number(select.seconds) || 0)
    let soonest = null
    for (const i of items) {
      if (i.time >= from) {
        const at = (i.time + Number(select.seconds)) * 1000
        if (soonest === null || at < soonest) soonest = at
      }
    }
    return soonest
  }
  return null
}

// ── sequence (recycler support) ──────────────────────────────────────────────

export const itemKey = item => `${item.time}|${item.source}|${item.title}`

/**
 * Where a lane goes on after the list changed: just after the last headline it placed, so the crawl continues without a
 * jump; when that headline is gone (aged out), from the start.
 */
export function resumeIndex(list, lastKey, step = 1) {
  if (!list.length) return 0
  if (lastKey === null || lastKey === undefined) return 0
  const at = list.findIndex(i => itemKey(i) === lastKey)
  return at < 0 ? 0 : (at + step) % list.length
}

// ── geometry ─────────────────────────────────────────────────────────────────

export const LINE_HEIGHT = 1.45 // em
export const PADDING_EM = 0.25 // em, above and below the lines

/** Font size in px: a percentage of the smaller side of the window, or pixels. */
export function fontPx(cfg, width, height) {
  const px = cfg.font_unit === 'px' ? cfg.font_size : (Math.min(width, height) * cfg.font_size) / 100
  return Math.max(6, Math.round(px))
}

/** Lines the bar is tall: the lanes, or the two lines of a clock that shows its date under the time. */
export function effectiveRows(cfg) {
  const clock = cfg.clock || {}
  return Math.max(cfg.rows, clock.enabled && clock.rows === 2 && clock.date ? 2 : 1)
}

/** Height in px of the bar: the lines of text and the padding. */
export function barHeight(cfg, font) {
  return Math.round(font * (effectiveRows(cfg) * LINE_HEIGHT + 2 * PADDING_EM))
}
