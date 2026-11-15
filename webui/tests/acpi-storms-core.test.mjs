// node --test tests/ (the wording of the ACPI storm alerts; no DOM)
import test from 'node:test'
import assert from 'node:assert/strict'

import {CLOCK_NOT_SET_BEFORE, localDate, stormDetail, stormTitle} from '../src/acpi-storms-core.js'

const storm = (over = {}) => ({
  name: 'gpe03', first_seen: 1763300000, last_seen: 1763399999, rate: 10238, boots: 3,
  masked_now: true, seen_now: true, ...over,
})

// A local-time epoch, so the expected date does not depend on the time zone of the machine running the tests
const localEpoch = (y, m, d, h = 12) => Math.floor(new Date(y, m - 1, d, h).getTime() / 1000)

test('title: masked wording with the rate', () => {
  assert.equal(stormTitle(storm()), 'ACPI event gpe03 masked: 10238 events/s')
})

test('title: cannot be masked only when this boot saw it and did not mask it', () => {
  assert.equal(stormTitle(storm({masked_now: false, seen_now: true})), 'ACPI event gpe03 cannot be masked: 10238 events/s')
  // a record from an earlier boot: the mask was applied then
  assert.equal(stormTitle(storm({masked_now: false, seen_now: false})), 'ACPI event gpe03 masked: 10238 events/s')
  assert.equal(stormTitle(storm({masked_now: true, seen_now: false})), 'ACPI event gpe03 masked: 10238 events/s')
})

test('title: any event name is shown as it is', () => {
  assert.equal(stormTitle(storm({name: 'gpe0A', rate: 2500})), 'ACPI event gpe0A masked: 2500 events/s')
})

test('detail: date from first_seen (local) and plural boots', () => {
  assert.equal(stormDetail(storm({first_seen: localEpoch(2026, 11, 16)})), 'First seen 2026-11-16, in 3 boots')
})

test('detail: singular boot', () => {
  assert.equal(stormDetail(storm({first_seen: localEpoch(2026, 1, 5), boots: 1})), 'First seen 2026-01-05, in 1 boot')
})

test('detail: a clock that was not set (before 2024-01-01) gives no date', () => {
  assert.equal(stormDetail(storm({first_seen: 1000, boots: 3})), 'Seen in 3 boots')
  assert.equal(stormDetail(storm({first_seen: CLOCK_NOT_SET_BEFORE - 1, boots: 1})), 'Seen in 1 boot')
})

test('detail: 2024-01-01 itself is a set clock', () => {
  assert.match(stormDetail(storm({first_seen: CLOCK_NOT_SET_BEFORE, boots: 2})), /^First seen \d{4}-\d{2}-\d{2}, in 2 boots$/)
})

test('localDate: zero-padded local calendar date', () => {
  assert.equal(localDate(localEpoch(2026, 3, 9, 0)), '2026-03-09')
})
