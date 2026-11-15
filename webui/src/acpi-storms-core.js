// The wording of the ACPI storm alerts on the Info page: no DOM, tested by node --test (tests/acpi-storms-core.test.mjs).
// A record is one entry of Settings/AcpiStorms/list: {name, first_seen, last_seen, rate, boots, masked_now, seen_now}.
// `first_seen` is epoch seconds as the device clock said: before 2024-01-01 the clock was not set, so no date is shown.

// Epoch seconds of 2024-01-01T00:00:00Z: an earlier first_seen comes from a device clock that was not set
export const CLOCK_NOT_SET_BEFORE = 1704067200

const pad = n => String(n).padStart(2, '0')

// Local calendar date as YYYY-MM-DD (the device's own clock is local time for the person reading it)
export function localDate(epochSeconds) {
  const d = new Date(epochSeconds * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// `ACPI event gpe03 masked: 10238 events/s`; `cannot be masked` when this boot saw the event and did not mask it
export function stormTitle(storm) {
  const state = !storm.masked_now && storm.seen_now ? 'cannot be masked' : 'masked'
  return `ACPI event ${storm.name} ${state}: ${storm.rate} events/s`
}

// `First seen 2026-11-16, in 3 boots`; `Seen in 1 boot` when the clock was not set
export function stormDetail(storm) {
  const boots = `${storm.boots} ${storm.boots === 1 ? 'boot' : 'boots'}`
  if (!(storm.first_seen >= CLOCK_NOT_SET_BEFORE)) return `Seen in ${boots}`
  return `First seen ${localDate(storm.first_seen)}, in ${boots}`
}
