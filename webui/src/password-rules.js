/**
 * The password rules shown in the web UI: a DOM-free mirror of manager/utils/passwords.py (`failed_ids`, rules_spec).
 * The server sends the spec (`rules_spec()`), the page evaluates the same rules as the person types, and the server
 * still decides. Tests: webui/tests/password-rules.test.mjs (checked against manager/tests/password_vectors.json).
 *
 * - `evaluate(spec, password, username, confirm)`: one row `{id, label, ok}` per `spec.rules` entry, in spec order,
 *   each computed on its own; with `confirm` given, a last `match` row.
 * - `failedIds(spec, password, username)`: the first failing id in server order, [] when the password is fine.
 * - `isValid(spec, password, username, confirm)`: every row ok and no control character.
 */

const encoder = new TextEncoder()

// Server order: the first entry that fails is the one reported
const ORDER = ['max_length', 'control', 'min_length', 'classes', 'repeat', 'username']

// ASCII-only lower-casing, as PAM does: non-ASCII letters are left as they are
const asciiLower = s => s.replace(/[A-Z]/g, c => c.toLowerCase())

const hasControl = s => /[\u0000-\u001f\u007f]/.test(s)

/** Every rule's result, keyed by id (true = ok). Computed for all known ids whatever the spec lists. */
function checks(spec, password, username) {
  const bytes = encoder.encode(password)
  const chars = Array.from(password)
  const user = Array.from(username)
  const result = {}

  result.max_length = !(chars.length > spec.max_length || bytes.length > spec.pam_max_bytes)
  result.control = !hasControl(password)
  result.min_length = bytes.length >= spec.min_length

  // Each byte belongs to one class; anything not ASCII letter or digit (space, symbols, UTF-8) is 'other'
  const seen = new Set()
  for (const b of bytes) {
    if (b >= 0x61 && b <= 0x7a) seen.add('lower')
    else if (b >= 0x41 && b <= 0x5a) seen.add('upper')
    else if (b >= 0x30 && b <= 0x39) seen.add('digit')
    else seen.add('other')
  }
  result.classes = seen.size >= spec.min_classes

  // A run of more than max_repeat identical bytes
  let run = 0
  let previous = -1
  result.repeat = true
  for (const b of bytes) {
    run = b === previous ? run + 1 : 1
    previous = b
    if (run > spec.max_repeat) { result.repeat = false; break }
  }

  // The username (or its reverse) inside the password; only for usernames of at least username_min_len characters
  if (user.length >= spec.username_min_len) {
    const pw = asciiLower(password)
    const name = asciiLower(username)
    const reversed = asciiLower(user.reverse().join(''))
    result.username = !pw.includes(name) && !pw.includes(reversed)
  } else {
    result.username = true
  }

  return result
}

/** One row per spec.rules entry, in spec order; a row whose id is unknown to the evaluator is ok. */
export function evaluate(spec, password, username = '', confirm = undefined) {
  const pw = typeof password === 'string' ? password : ''
  const ok = typeof password === 'string' ? checks(spec, password, username) : {}
  const rows = spec.rules.map(rule => ({
    id: rule.id,
    label: rule.text,
    ok: typeof password === 'string' ? (ok[rule.id] ?? true) : false,
  }))
  if (confirm !== undefined) {
    rows.push({id: 'match', label: 'Passwords match', ok: confirm === pw && pw.length > 0})
  }
  return rows
}

/** The first failing id in server order; [] when fine; ['type'] for a non-string password. */
export function failedIds(spec, password, username = '') {
  if (typeof password !== 'string') return ['type']
  const ok = checks(spec, password, username)
  const failed = ORDER.find(id => ok[id] === false)
  return failed === undefined ? [] : [failed]
}

/** True when every row is ok and the password has no control character. */
export function isValid(spec, password, username = '', confirm = undefined) {
  if (typeof password !== 'string') return false
  return evaluate(spec, password, username, confirm).every(row => row.ok) && checks(spec, password, username).control
}
