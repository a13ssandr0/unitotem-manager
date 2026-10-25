// node --test webui/tests   (no dependencies: the pure part of the ticker is plain ES modules)
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  barHeight, compileTimeFormat, effectiveRows, fontPx, formatTime, itemKey, nextExpiry, parseColor, parseTemplate, plainText, renderItem,
  resumeIndex, selectItems, startOfDay,
} from '../src/ticker-core.js'

const TZ = process.env.TZ
test.after(() => { if (TZ === undefined) delete process.env.TZ; else process.env.TZ = TZ })

const item = (over = {}) => ({time: Date.UTC(2026, 9, 5, 8, 5, 9) / 1000, source: 'ANSA', title: 'Hello world', summary: 'Sum', link: 'http://x/1', ...over})
const out = (tpl, it = item(), locale = 'en-GB') => {
  const p = parseTemplate(tpl)
  assert.equal(p.error, null, p.error)
  return plainText(renderItem(p, it, locale))
}

// ── time ─────────────────────────────────────────────────────────────────────

test('LDML time patterns', () => {
  process.env.TZ = 'UTC'
  const d = new Date(Date.UTC(2026, 9, 5, 8, 5, 9))
  assert.equal(formatTime(d, 'HH:mm', 'en-GB'), '08:05')
  assert.equal(formatTime(d, 'HH:mm:ss', 'en-GB'), '08:05:09')
  assert.equal(formatTime(d, 'H:m:s', 'en-GB'), '8:5:9')
  assert.equal(formatTime(d, 'hh:mm a', 'en-US'), '08:05 AM')
  assert.equal(formatTime(new Date(Date.UTC(2026, 9, 5, 15, 0)), 'h:mm a', 'en-US'), '3:00 PM')
  assert.equal(formatTime(new Date(Date.UTC(2026, 9, 5, 0, 30)), 'hh:mm', 'en-GB'), '12:30')
  assert.equal(formatTime(d, 'dd MMM', 'en-GB'), '05 Oct')
  assert.equal(formatTime(d, 'dd MMM', 'it-IT'), '05 ott')
  assert.equal(formatTime(d, 'd MMMM yyyy', 'en-GB'), '5 October 2026')
  assert.equal(formatTime(d, 'dd/MM/yy', 'en-GB'), '05/10/26')
  assert.equal(formatTime(d, 'dd/MM HH:mm', 'en-GB'), '05/10 08:05')
  assert.equal(formatTime(d, 'EEE', 'en-GB'), 'Mon')
  assert.equal(formatTime(d, 'EEEE', 'it-IT'), 'lunedì')
  assert.equal(formatTime(d, "HH'h'mm", 'en-GB'), '08h05')
  assert.equal(formatTime(d, "'it''s' HH", 'en-GB'), "it's 08")
  assert.equal(formatTime(d, '', 'en-GB'), '08:05')
})

test('strftime (Python) time patterns', () => {
  process.env.TZ = 'UTC'
  const d = new Date(Date.UTC(2026, 9, 5, 8, 5, 9))
  assert.equal(formatTime(d, '%H:%M', 'en-GB'), '08:05')
  assert.equal(formatTime(d, '%d/%m/%Y %H:%M:%S', 'en-GB'), '05/10/2026 08:05:09')
  assert.equal(formatTime(d, '%a %d %b', 'en-GB'), 'Mon 05 Oct')
  assert.equal(formatTime(d, '%A %-d %B', 'en-GB'), 'Monday 5 October')
  assert.equal(formatTime(d, '%I:%M %p', 'en-US'), '08:05 AM')
  assert.equal(formatTime(d, '100%%', 'en-GB'), '100%')
  assert.throws(() => compileTimeFormat('%Q'), /unsupported strftime/)
})

test('time patterns: errors and the MM warning', () => {
  assert.throws(() => compileTimeFormat('HH:mm X'), /unsupported time field "X"/)
  assert.throws(() => compileTimeFormat('yyyyy'), /too many/)
  assert.throws(() => compileTimeFormat("HH 'oops"), /unterminated/)
  assert.deepEqual(compileTimeFormat('HH:mm').warnings, [])
  assert.deepEqual(compileTimeFormat('dd MMM yyyy').warnings, [])
  assert.match(compileTimeFormat('hh:MM').warnings[0], /MM is the MONTH/)
  assert.match(compileTimeFormat('HH:MM').warnings[0], /minutes are mm/)
  assert.deepEqual(compileTimeFormat("HH 'MM'").warnings, [])
})

test('time is formatted in the device time zone', () => {
  const t = Date.UTC(2026, 6, 1, 22, 30) // 2026-07-01 22:30 UTC
  process.env.TZ = 'Europe/Rome'
  assert.equal(formatTime(new Date(t), 'HH:mm dd/MM', 'en-GB'), '00:30 02/07')
  process.env.TZ = 'America/New_York'
  assert.equal(formatTime(new Date(t), 'HH:mm dd/MM', 'en-GB'), '18:30 01/07')
})

// ── template: fields ─────────────────────────────────────────────────────────

test('fields and format specs', () => {
  process.env.TZ = 'UTC'
  assert.equal(out('{title}'), 'Hello world')
  assert.equal(out('{time:HH:mm} - {title}'), '08:05 - Hello world')
  assert.equal(out('{source}: {summary}'), 'ANSA: Sum')
  assert.equal(out('{title:.5}'), 'Hell…')
  assert.equal(out('{title:.1}'), 'H')
  assert.equal(out('{title:.0}'), '')
  assert.equal(out('[{source:>8}]'), '[    ANSA]')
  assert.equal(out('[{source:<8}]'), '[ANSA    ]')
  assert.equal(out('[{source:^8}]'), '[  ANSA  ]')
  assert.equal(out('[{source:*^9}]'), '[**ANSA***]')
  assert.equal(out('[{source:.3}]'), '[AN…]')
  assert.equal(out('[{source:>6.3s}]'), '[   AN…]')
  assert.equal(out('{title!u}'), 'HELLO WORLD')
  assert.equal(out('{title!l}'), 'hello world')
  assert.equal(out('{title!t}', item({title: 'hELLO wORLD of "x"'})), 'Hello World Of "X"')
  assert.equal(out('{title!c}', item({title: 'hello'})), 'Hello')
  assert.equal(out('{title!u:.6}'), 'HELLO…')
  assert.equal(out('{time!u:dd MMM}'), '05 OCT')
  assert.equal(out('{{literal}} {title}'), '{literal} Hello world')
})

test('bad fields are errors with a message, and the fallback is the plain title', () => {
  for (const [tpl, re] of [
    ['{nope}', /unknown field \{nope\}/], ['{title', /never closed/], ['title}', /single "\}"/],
    ['{title!x}', /unknown conversion/], ['{title:abc}', /invalid format spec/], ['{time:HH:mm X}', /unsupported time field/],
    ['{title:.9999}', /too large/], ['', /empty/], ['   ', /empty/], ['x'.repeat(501), /longer than/], ['{}', /invalid field/],
    ['{title.upper}', /invalid field/], ['{title[0]}', /invalid field/],
  ]) {
    const p = parseTemplate(tpl)
    assert.match(p.error ?? '', re, tpl)
    assert.equal(plainText(renderItem(p, item(), 'en-GB')), 'Hello world', 'falls back to the title')
  }
})

test('a template without fields warns', () => {
  assert.match(parseTemplate('breaking news').warnings[0], /no \{field\}/)
})

// ── template: Rich markup ────────────────────────────────────────────────────

const seg = (tpl, it = item()) => renderItem(parseTemplate(tpl), it, 'en-GB')

test('Rich styles and colours', () => {
  let s = seg('[bold red]{title}[/]')
  assert.deepEqual(s, [{text: 'Hello world', style: {bold: true, color: 'red'}}])
  s = seg('[b]a[/b][i]b[/i][u]c[/u][s]d[/s][dim]e[/dim]')
  assert.deepEqual(s.map(x => Object.keys(x.style)[0]), ['bold', 'italic', 'underline', 'strike', 'dim'])
  s = seg('[on blue]x[/]')
  assert.deepEqual(s[0].style, {bg: 'blue'})
  s = seg('[white on #112233]x')                   // unclosed tags close at the end, like Rich
  assert.deepEqual(s[0].style, {color: 'white', bg: '#112233'})
  s = seg('[#ff8800]x[/][rgb(1,2,3)]y[/][bright_green]z[/][color(9)]w[/][color(196)]v[/]')
  assert.deepEqual(s.map(x => x.style.color), ['#ff8800', 'rgb(1,2,3)', '#00ff00', '#ff0000', 'rgb(255,0,0)'])
  s = seg('[bold]a[not bold]b[/not bold]c[/bold]')
  assert.deepEqual(s.map(x => !!x.style.bold), [true, false, true])
})

test('text of the same style is one segment', () => {
  assert.deepEqual(seg('[on red] {source} - {title} [/]').map(x => x.text), [' ANSA - Hello world '])
  assert.equal(seg('a{title}b').length, 1)
  assert.equal(seg('a[b]{title}[/b]b').length, 3)
})

test('nesting and closing', () => {
  let s = seg('[bold]a[red]b[/red]c[/bold]d')
  assert.deepEqual(s.map(x => x.style), [{bold: true}, {bold: true, color: 'red'}, {bold: true}, {}])
  s = seg('[bold]a[red]b[/bold]c[/red]d')           // closing an outer tag first is fine, the inner one stays
  assert.deepEqual(s.map(x => x.style), [{bold: true}, {bold: true, color: 'red'}, {color: 'red'}, {}])
  s = seg('[b]x[/bold]y')                            // [/bold] closes [b]
  assert.deepEqual(s.map(x => x.style), [{bold: true}, {}])
  s = seg('[red]a[blue]b[/]c[/]d')
  assert.deepEqual(s.map(x => x.style.color), ['red', 'blue', 'red', undefined])
})

test('markup errors', () => {
  for (const [tpl, re] of [
    ['[/]x', /nothing is open/], ['[bold]x[/italic]', /does not match/], ['[nonsense]x', /unknown style or colour "nonsense"/],
    ['[on nonsense]x', /unknown background colour/], ['[not]x', /"not" must be followed/], ['[link=http://x]x[/link]', /not supported/],
    ['[@click=go]x', /not supported/], ['[#12]x', /unknown style or colour/], ['[rgb(300,0,0)]x', /unknown style/],
    ['[red; background:url(x)]x', /unknown style or colour/],
  ]) {
    assert.match(parseTemplate(tpl).error ?? '', re, tpl)
  }
})

test('brackets that are not tags stay text, like in Rich', () => {
  assert.equal(out('[1] {title} [ ] [ok'), '[1] Hello world [ ] [ok')
  assert.equal(out('a [ b ]'), 'a [ b ]')
})

test('backslash escapes a tag', () => {
  assert.equal(out('\\[bold]{title}'), '[bold]Hello world')
  assert.equal(out('\\\\[bold]x[/]'), '\\x')                // two backslashes: one stays and the tag works
  assert.equal(out('a\\b {title}'), 'a\\b Hello world')     // a backslash elsewhere is just a backslash
  assert.equal(out('\\\\\\[bold]x'), '\\[bold]x')
})

// ── feed text is data, never markup ──────────────────────────────────────────

test('markup, fields and braces in a headline are shown as they are', () => {
  const hostile = item({title: '[bold red]PWNED[/] {source} {{x}} \\[ [/]', source: '[on red]S[/]', summary: '{title}'})
  const s = seg('[green]{title}|{source}|{summary}[/]', hostile)
  assert.equal(plainText(s), '[bold red]PWNED[/] {source} {{x}} \\[ [/]|[on red]S[/]|{title}')
  assert.ok(s.every(x => x.style.color === 'green' && !x.style.bold && !x.style.bg), 'the style is the template\'s only')
})

test('fields are capped and missing ones are empty', () => {
  assert.equal(out('{title}', item({title: 'x'.repeat(5000)})).length, 1000)
  assert.equal(out('[{summary}][{link}]', {time: 0, title: 't', source: 's'}), '[][]')
  assert.equal(out('{time:HH}-{title}', {title: 't'}), '-t')
})

test('colours are validated: only hex, rgb(), CSS names reach the style', () => {
  for (const bad of ['red;color:blue', 'url(x)', 'expression(1)', '#ggg', 'var(--x)', 'rgb(1,2)', '', 'RED); x(']) assert.equal(parseColor(bad), null, bad)
  assert.equal(parseColor('Red'), 'red')
  assert.equal(parseColor('#ABC'), '#abc')
  assert.equal(parseColor('bright_white'), '#ffffff')
})

// ── selection ────────────────────────────────────────────────────────────────

const at = (iso) => Date.parse(iso) / 1000

test('selection by count', () => {
  const items = [1, 5, 3, 4, 2].map(t => ({time: t, title: String(t)}))
  assert.deepEqual(selectItems(items, {mode: 'count', count: 3}, 0).map(i => i.time), [5, 4, 3])
  assert.equal(selectItems(items, {mode: 'count', count: 100}, 0).length, 5)
  assert.equal(selectItems(items, {mode: 'count', count: 0}, 0).length, 0)
  assert.equal(selectItems([], {mode: 'count', count: 3}, 0).length, 0)
  assert.equal(selectItems(items, undefined, 0).length, 0)
})

test('selection by range', () => {
  const now = Date.parse('2026-10-05T12:00:00Z')
  const items = [at('2026-10-05T11:59:00Z'), at('2026-10-05T10:00:00Z'), at('2026-10-05T09:59:59Z'), at('2026-10-04T12:00:00Z')].map(t => ({time: t}))
  assert.equal(selectItems(items, {mode: 'range', seconds: 7200}, now).length, 2)      // exactly 2 h old is in
  assert.equal(selectItems(items, {mode: 'range', seconds: 7201}, now).length, 3)
  assert.equal(selectItems(items, {mode: 'range', seconds: 2 * 86400}, now).length, 4)
  assert.equal(selectItems(items, {mode: 'range', seconds: 60}, now).length, 1)
})

test('selection of today around midnight', () => {
  process.env.TZ = 'Europe/Rome'
  const items = [at('2026-10-04T21:59:59Z'), at('2026-10-04T22:00:00Z'), at('2026-10-05T10:00:00Z')].map(t => ({time: t}))
  // Rome is UTC+2 on 5 October: local midnight is 22:00:00Z of the 4th
  assert.equal(startOfDay(Date.parse('2026-10-05T10:00:00Z')), Date.parse('2026-10-04T22:00:00Z'))
  assert.equal(selectItems(items, {mode: 'today'}, Date.parse('2026-10-05T10:00:00Z')).length, 2)
  // one second before local midnight it is still the 4th: today starts at 22:00:00Z of the 3rd
  const early = [{time: at('2026-10-03T21:59:59Z')}, {time: at('2026-10-03T22:00:00Z')}]
  assert.equal(selectItems(early, {mode: 'today'}, Date.parse('2026-10-04T21:59:59Z')).length, 1)
  assert.equal(selectItems(early, {mode: 'today'}, Date.parse('2026-10-04T22:00:00Z')).length, 0)   // and at midnight it flips
})

test('selection of today on the days the clock changes', () => {
  process.env.TZ = 'Europe/Rome'
  // 2026-03-29: clocks go 02:00 -> 03:00, the day has 23 hours; midnight is 23:00Z of the 28th (still CET)
  assert.equal(startOfDay(Date.parse('2026-03-29T12:00:00Z')), Date.parse('2026-03-28T23:00:00Z'))
  // 2026-10-25: 03:00 -> 02:00, 25 hours; midnight is 22:00Z of the 24th (still CEST)
  assert.equal(startOfDay(Date.parse('2026-10-25T12:00:00Z')), Date.parse('2026-10-24T22:00:00Z'))
  // the day after: midnight at 23:00Z again (CET)
  assert.equal(startOfDay(Date.parse('2026-10-26T12:00:00Z')), Date.parse('2026-10-25T23:00:00Z'))
  // an item at 00:30 local on the 25 hour day is today, the previous evening's is not
  const items = [at('2026-10-24T22:30:00Z'), at('2026-10-24T21:30:00Z')].map(t => ({time: t}))
  assert.equal(selectItems(items, {mode: 'today'}, Date.parse('2026-10-25T09:00:00Z')).length, 1)
  // a zone where DST starts AT midnight (America/Sao_Paulo used to, Asia/Beirut does): midnight does not exist
  process.env.TZ = 'Asia/Beirut'
  const mid = startOfDay(Date.parse('2026-03-29T12:00:00Z'))
  assert.ok(new Date(mid).getHours() <= 1 && mid <= Date.parse('2026-03-29T12:00:00Z') && mid > Date.parse('2026-03-28T12:00:00Z'))
  // and a range spanning the change is plain elapsed time
  process.env.TZ = 'Europe/Rome'
  const now = Date.parse('2026-03-29T10:00:00Z')
  const it2 = [{time: (now - 86400000 + 1000) / 1000}, {time: (now - 86400000 - 1000) / 1000}]
  assert.equal(selectItems(it2, {mode: 'range', seconds: 86400}, now).length, 1)
})

test('items in the future of the device clock are still shown (the device clock may be wrong, the news are not)', () => {
  assert.equal(selectItems([{time: 5000}], {mode: 'range', seconds: 60}, 1000 * 1000).length, 1)
})

test('next expiry is one timestamp, never a poll', () => {
  process.env.TZ = 'Europe/Rome'
  const now = Date.parse('2026-10-05T10:00:00Z')
  assert.equal(nextExpiry([], {mode: 'count', count: 5}, now), null)
  assert.equal(nextExpiry([], {mode: 'today'}, now), Date.parse('2026-10-05T22:00:00Z'))   // next local midnight
  const items = [{time: now / 1000 - 600}, {time: now / 1000 - 100}, {time: now / 1000 - 5000}]
  assert.equal(nextExpiry(items, {mode: 'range', seconds: 3600}, now), now - 600000 + 3600000)
  assert.equal(nextExpiry([{time: 1}], {mode: 'range', seconds: 3600}, now), null)         // nothing selected, nothing to age
})

// ── recycler support and geometry ────────────────────────────────────────────

test('resume index continues after the last placed item, or restarts', () => {
  const list = [1, 2, 3, 4].map(n => ({time: n, source: 's', title: `t${n}`}))
  assert.equal(resumeIndex(list, itemKey(list[1])), 2)
  assert.equal(resumeIndex(list, itemKey(list[3])), 0)
  assert.equal(resumeIndex(list, itemKey(list[1]), 2), 3)
  assert.equal(resumeIndex(list, 'gone'), 0)
  assert.equal(resumeIndex(list, null), 0)
  assert.equal(resumeIndex([], 'x'), 0)
  // the list got a new head: the lane goes on with what followed, nothing repeats, nothing jumps
  const grown = [{time: 9, source: 's', title: 'new'}, ...list]
  assert.equal(grown[resumeIndex(grown, itemKey(list[1]))].title, 't3')
})

test('font size and bar height', () => {
  assert.equal(fontPx({font_unit: 'percent', font_size: 3}, 1920, 1080), 32)
  assert.equal(fontPx({font_unit: 'percent', font_size: 3}, 1080, 1920), 32)       // the smaller side, whatever the orientation
  assert.equal(fontPx({font_unit: 'px', font_size: 48}, 1920, 1080), 48)
  assert.equal(fontPx({font_unit: 'percent', font_size: 1}, 100, 100), 6)           // never unreadably small
  assert.ok(barHeight({rows: 2}, 32) > barHeight({rows: 1}, 32) && barHeight({rows: 1}, 32) > 32)
  // a clock with its date on a second line makes a one lane bar two lines tall
  const clock = (rows, date) => ({rows: 1, clock: {enabled: true, rows, date}})
  assert.equal(barHeight(clock(2, true), 32), barHeight({rows: 2}, 32))
  assert.equal(barHeight(clock(2, false), 32), barHeight({rows: 1}, 32))
  assert.equal(barHeight(clock(1, true), 32), barHeight({rows: 1}, 32))
  assert.equal(effectiveRows({rows: 2, clock: {enabled: true, rows: 2, date: true}}), 2)
})
