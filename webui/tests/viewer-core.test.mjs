// node --test tests/ (the pure part of the Viewers page; no DOM)
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {fileURLToPath} from 'node:url'

import {
  DEFAULT_LAYOUT, buildWindows, capitalise, contentLanguageItems, countLeaves, keyboardLanguage, keyboardLanguageItems,
  keyboardLayout, keyboardVariant, languageVariants, schedulesFromText, screensaverChange, variantItems,
} from '../src/components/ViewerCore.js'

// The keyboard data as the page reads it (a small copy of the shape of src/keyboard/index.json)
const INDEX = JSON.parse(readFileSync(fileURLToPath(new URL('../src/keyboard/index.json', import.meta.url)), 'utf8'))

const viewers = {
  'pi-1': {
    hostname: 'totem-1', ip: '192.168.1.10', screens: [{name: 'HDMI-A-1', width: 1920, height: 1080}],
    windows: [{window_id: 7, screen_name: 'HDMI-A-1', width: 1920, height: 1080, assigned_playlist: 'P1',
      keyboard: {enabled: false, button_position: 'top', layout: 'it'}, screensaver: {enabled: true, home_url: 'https://h', idle_timeout: 60},
      refresh: {refresh_after_idle: 300, refresh_schedules: ['0 6 * * *']}, layout: {tree: {kind: 'split', a: {kind: 'leaf', id: 1}, b: {kind: 'leaf', id: 2}}, main: 1, panes: {}}}],
  },
  'pc-2': {hostname: 'pc-2', windows: [{window_id: 3, width: 1280, height: 720, orientation: 1, flip: 2, turned_by_output: true}]},
}

test('one window per viewer window, keyed by viewer and window id', () => {
  const wins = buildWindows(viewers)
  assert.deepEqual(wins.map(w => w.key), ['pi-1:7', 'pc-2:3'])
  assert.equal(wins[0].hostname, 'totem-1')
  assert.equal(wins[0].ip, '192.168.1.10')
  assert.equal(wins[0].screen_label, 'HDMI-A-1')
  assert.equal(wins[1].screen_label, 'window 3')
  assert.deepEqual(wins[1].screens, [])
})

test('defaults fill what the server does not store', () => {
  const [, pc] = buildWindows(viewers)
  assert.deepEqual(pc.keyboard, {enabled: true, button_position: 'bottom-right', layout: 'english'})
  assert.deepEqual(pc.screensaver, {enabled: false, home_url: '', idle_timeout: 120})
  assert.deepEqual(pc.refresh, {refresh_after_idle: null, refresh_schedules: []})
  assert.equal(pc.language, '')
  assert.equal(pc.assigned_playlist, undefined)
  assert.deepEqual(pc.layout, DEFAULT_LAYOUT)
  assert.equal(pc.pane_count, 1)
  assert.equal(pc.turned_by_output, true)
  assert.equal(pc.flip, 2)
})

test('stored settings win over the defaults; panes are counted', () => {
  const [pi] = buildWindows(viewers)
  assert.deepEqual(pi.keyboard, {enabled: false, button_position: 'top', layout: 'it'})
  assert.equal(pi.screensaver.idle_timeout, 60)
  assert.equal(pi.refresh.refresh_after_idle, 300)
  assert.equal(pi.assigned_playlist, 'P1')
  assert.equal(pi.pane_count, 2)
  assert.equal(countLeaves({kind: 'split', a: {kind: 'split', a: {kind: 'leaf'}, b: {kind: 'leaf'}}, b: {kind: 'leaf'}}), 3)
})

test('no viewers, no windows', () => {
  assert.deepEqual(buildWindows({}), [])
  assert.deepEqual(buildWindows({'x': {hostname: 'x'}}), [])
})

test('keyboard languages sorted by name, with their ids', () => {
  const items = keyboardLanguageItems(INDEX)
  assert.equal(items.length, INDEX.length)
  const titles = items.map(i => i.title)
  assert.deepEqual(titles, [...titles].sort((a, b) => a.localeCompare(b)))
  assert.ok(items.some(i => i.value === 'en-US' && i.title === 'English (US)'))
})

test('arrangements: the first one is the default, names are capitalised', () => {
  const items = variantItems(INDEX, 'en-US')
  assert.equal(items[0].value, 'qwerty')
  assert.equal(items[0].title, 'Qwerty (default)')
  assert.equal(items[1].title, 'Qwertz')
  assert.equal(capitalise('bulgarian bds'), 'Bulgarian Bds')
  assert.deepEqual(variantItems(INDEX, 'no-such-language'), [])
  assert.deepEqual(languageVariants(INDEX, 'no-such-language'), [])
})

test('keyboard setting <-> language and arrangement', () => {
  const win = {keyboard: {layout: 'en-US'}}
  assert.equal(keyboardLanguage(win), 'en-US')
  assert.equal(keyboardVariant(win, INDEX), 'qwerty')
  // English keeps its legacy name ('english', 'english/dvorak'); see keyboard-layouts.js
  assert.equal(keyboardLayout(INDEX, 'en-US', 'qwerty'), 'english')
  assert.equal(keyboardLayout(INDEX, 'en-US', 'dvorak'), 'english/dvorak')
  const legacy = {keyboard: {layout: 'italian'}}
  assert.equal(keyboardLanguage(legacy), 'it')
  assert.equal(keyboardVariant(legacy, INDEX), INDEX.find(l => l.id === 'it').variants[0])
})

test('page language list leaves out the no-language keyboard', () => {
  const items = contentLanguageItems([{title: 'Alphabet (QWERTY)', value: 'zz'}, {title: 'Italian', value: 'it'}])
  assert.deepEqual(items, [{title: 'Language of the node', value: ''}, {title: 'Italian', value: 'it'}])
})

test('interactive screen: enabling without a home page only opens the fields', () => {
  const win = {screensaver: {enabled: false, home_url: '', idle_timeout: 120}}
  assert.deepEqual(screensaverChange(win, {enabled: true}, false), {draft: true, request: null})
  const ok = screensaverChange(win, {enabled: true, home_url: 'https://x.example/'}, false)
  assert.equal(ok.draft, false)
  assert.deepEqual(ok.request, {enabled: true, home_url: 'https://x.example/', idle_timeout: 120})
  // a field typed while the switch is open keeps the switch on (draft) until the home page is given
  assert.deepEqual(screensaverChange(win, {idle_timeout: 30}, true), {draft: true, request: null})
  // an enabled screensaver stays enabled when another field changes
  const on = {screensaver: {enabled: true, home_url: 'https://h', idle_timeout: 120}}
  assert.deepEqual(screensaverChange(on, {idle_timeout: 45}, false).request, {enabled: true, home_url: 'https://h', idle_timeout: 45})
  assert.deepEqual(screensaverChange(on, {enabled: false}, false).request, {enabled: false, home_url: 'https://h', idle_timeout: 120})
})

test('refresh schedules: one per line, blanks dropped', () => {
  assert.deepEqual(schedulesFromText(' 0 6 * * * \n\n  30 2 * * 1 \n'), ['0 6 * * *', '30 2 * * 1'])
  assert.deepEqual(schedulesFromText(''), [])
})
