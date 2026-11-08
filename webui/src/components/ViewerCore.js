// The pure part of the Viewers page: the window list built from the server's viewers, the keyboard and the interactive-screen
// rules. No Vue, no DOM: tested by node --test (tests/viewer-core.test.mjs).
import {layoutId, parseLayoutId} from '../keyboard-layouts.js'

export const KEYBOARD_POSITIONS = ['top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right']

// What a window without a stored layout is: one pane filling it
export const DEFAULT_LAYOUT = {area: {left: 0, top: 0, width: 100, height: 100, margin_color: 'rgb(0,0,0)'},
  tree: {kind: 'leaf', id: 1}, main: 1, panes: {}}
export const countLeaves = node => node.kind === 'leaf' ? 1 : countLeaves(node.a) + countLeaves(node.b)

// One entry per window of every viewer (viewers: instance_id -> info). A screen is a window, and each one gets its own playlist.
// The screen name comes from the viewer, the only place that knows which output a window sits on.
export function buildWindows(viewers) {
  return Object.entries(viewers).flatMap(([viewerId, viewer]) =>
    (viewer.windows ?? []).map(win => ({
      key: `${viewerId}:${win.window_id}`,
      viewer_id: viewerId,
      window_id: win.window_id,
      hostname: viewer.hostname,
      ip: viewer.ip,
      screens: viewer.screens ?? [],
      screen_label: win.screen_name ?? `window ${win.window_id}`,
      width: win.width,
      height: win.height,
      assigned_playlist: win.assigned_playlist,
      audio_output: win.audio_output,
      keyboard: {enabled: true, button_position: 'bottom-right', layout: 'english', ...win.keyboard},
      screensaver: {enabled: false, home_url: '', idle_timeout: 120, ...win.screensaver},
      refresh: {refresh_after_idle: null, refresh_schedules: [], ...win.refresh},
      language: win.language ?? '',
      orientation: win.orientation ?? 0,
      flip: win.flip ?? 0,
      turned_by_output: win.turned_by_output ?? false,     // the compositor turned the screen: width and height are the turned ones
      layout: {...DEFAULT_LAYOUT, ...win.layout},
      pane_count: countLeaves((win.layout ?? DEFAULT_LAYOUT).tree),
    }))
  )
}

// 'bulgarian_bds' -> 'Bulgarian bds'
export const capitalise = text => text.replace(/\b\p{L}/gu, c => c.toUpperCase())

// The languages of the keyboard data ({id, name, variants}) as {title, value}, by name
export function keyboardLanguageItems(index) {
  return index.map(lang => ({title: lang.name, value: lang.id})).sort((a, b) => a.title.localeCompare(b.title))
}
export const languageVariants = (index, lang) => index.find(l => l.id === lang)?.variants ?? []
// The first variant is the language's own arrangement
export function variantItems(index, lang) {
  return languageVariants(index, lang).map((v, i) =>
    ({title: capitalise(v.replace(/_/g, ' ')) + (i === 0 ? ' (default)' : ''), value: v}))
}
export const keyboardLanguage = win => parseLayoutId(win.keyboard.layout).lang
export const keyboardVariant = (win, index) =>
  parseLayoutId(win.keyboard.layout).variant ?? languageVariants(index, keyboardLanguage(win))[0]
// The value of the keyboard setting for a language and an arrangement
export const keyboardLayout = (index, lang, variant) => layoutId(lang, variant, languageVariants(index, lang))

// The page language: the keyboard languages that are a language of a page (`zz` is LatinIME's "Alphabet (QWERTY)", for no language)
export function contentLanguageItems(languages) {
  return [{title: 'Language of the node', value: ''}, ...languages.filter(lang => lang.value !== 'zz')]
}

// The interactive screen. The server refuses an enabled screensaver without a home page, so such a change only opens the
// fields (`draft`) and sends nothing; `draftOn` is the switch as the user left it.
export function screensaverChange(win, patch, draftOn) {
  const {enabled, home_url, idle_timeout} = {...win.screensaver, enabled: win.screensaver.enabled || draftOn, ...patch}
  const draft = enabled && !home_url
  return {draft, request: draft ? null : {enabled, home_url, idle_timeout}}
}

// The 'At times' field: one cron schedule per line, blank lines dropped
export const schedulesFromText = text => text.split('\n').map(line => line.trim()).filter(Boolean)
