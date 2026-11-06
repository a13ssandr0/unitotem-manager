// node --test webui/tests   (no dependencies: the password rules are plain ES modules)
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

import { evaluate, failedIds, isValid } from '../src/password-rules.js'

// The server's rules_spec() (manager/utils/passwords.py) with the numbers of the PAM vectors
const vectors = JSON.parse(fs.readFileSync(new URL('../../manager/tests/password_vectors.json', import.meta.url), 'utf8'))
const policy = vectors.policy

const spec = {
  min_length: policy.min_length,
  max_length: policy.max_length,
  min_classes: policy.min_classes,
  max_repeat: policy.max_repeat,
  username_min_len: policy.username_min_len,
  pam_max_bytes: policy.pam_max_bytes,
  rules: [
    {id: 'min_length', text: 'At least 8 characters'},
    {id: 'classes', text: 'At least 2 of: lowercase, uppercase, digits, symbols'},
    {id: 'repeat', text: 'No character more than 3 times in a row'},
    {id: 'username', text: 'Does not contain the username'},
    {id: 'max_length', text: 'At most 128 characters'},
  ],
}

// The vectors are the spec: no forbidden-word rule exists
const vectorSpec = spec

// Characters (code points), not UTF-16 units: the rule is on what the person sees
const charLength = s => Array.from(s).length
// Vectors PAM accepts with more than 128 characters: the manager refuses them on purpose
const expectedFailed = v => (charLength(v.password) > 128 && v.ok) ? ['max_length'] : v.failed
const expectedOk = v => (charLength(v.password) > 128 && v.ok) ? false : v.ok

test('the vectors: failedIds matches PAM for every vector', () => {
  for (const v of vectors.vectors) {
    assert.deepEqual(failedIds(vectorSpec, v.password, v.username), expectedFailed(v), JSON.stringify(v))
  }
})

test('the vectors: isValid matches PAM for every vector', () => {
  for (const v of vectors.vectors) {
    assert.equal(isValid(vectorSpec, v.password, v.username), expectedOk(v), JSON.stringify(v))
  }
})

test('the vectors: evaluate flags the first failed rule', () => {
  for (const v of vectors.vectors) {
    if (v.failed.length === 0) continue
    const first = v.failed[0]
    const rows = evaluate(vectorSpec, v.password, v.username)
    if (!rows.some(r => r.id === first)) continue
    const bad = rows.filter(r => !r.ok).map(r => r.id)
    assert.ok(bad.includes(first), `${first} not failing in ${JSON.stringify(v)}`)
  }
})

test('evaluate: one row per rule in spec order, each independent', () => {
  const rows = evaluate(spec, 'aa')
  assert.deepEqual(rows.map(r => r.id), ['min_length', 'classes', 'repeat', 'username', 'max_length'])
  assert.deepEqual(rows.map(r => r.ok), [false, false, true, true, true])
  assert.equal(rows[0].label, 'At least 8 characters')
})

test('evaluate: empty password fails length and classes only', () => {
  const rows = evaluate(spec, '', 'bob')
  assert.deepEqual(rows.map(r => r.ok), [false, false, true, true, true])
  assert.deepEqual(failedIds(spec, ''), ['min_length'])
  assert.equal(isValid(spec, ''), false)
})

test('evaluate: confirm adds a match row', () => {
  const rows = evaluate(spec, 'Abcdef12', 'bob', 'Abcdef12')
  assert.equal(rows.length, 6)
  assert.deepEqual(rows[5], {id: 'match', label: 'Passwords match', ok: true})
  assert.equal(evaluate(spec, 'Abcdef12', 'bob', 'Abcdef13')[5].ok, false)
  assert.equal(isValid(spec, 'Abcdef12', 'bob', 'Abcdef13'), false)
  assert.equal(isValid(spec, 'Abcdef12', 'bob', 'Abcdef12'), true)
})

test('evaluate: empty confirm of an empty password does not match', () => {
  assert.equal(evaluate(spec, '', '', '')[5].ok, false)
})

test('evaluate: no confirm, no match row', () => {
  assert.equal(evaluate(spec, 'Abcdef12').length, 5)
})

test('bytes: multi-byte characters count as UTF-8 bytes for min_length and other class', () => {
  // 3 characters, 6 bytes: below min_length
  assert.deepEqual(failedIds(spec, 'èèè'), ['min_length'])
  // 4 characters, 8 bytes: ok length, one class (other) only
  assert.deepEqual(failedIds(spec, 'èèèè'), ['classes'])
  assert.equal(isValid(spec, 'èèèèa1'), true)
})

test('repeat: a run of more than max_repeat identical bytes', () => {
  assert.deepEqual(failedIds(spec, 'Abcaaaa1'), ['repeat'])
  assert.deepEqual(failedIds(spec, 'Abcaaa1x'), [])
})

test('username: substring or reverse, only from username_min_len', () => {
  assert.deepEqual(failedIds(spec, 'x1BOBBYxAb', 'bobby'), ['username'])
  assert.deepEqual(failedIds(spec, 'xbobbyAb1', 'BOBBY'), ['username'])
  assert.deepEqual(failedIds(spec, 'x1Bobxyz9Q', 'bobby'), [])
  // reversed: 'simo' reversed is 'omis'
  assert.deepEqual(failedIds(spec, 'Ab1omisd', 'simo'), ['username'])
  // 'ann' is shorter than username_min_len (4): no username rule
  assert.deepEqual(failedIds(spec, 'Ann1ann9', 'ann'), [])
})

test('username: a short username (bob) is not checked', () => {
  assert.deepEqual(failedIds(spec, 'xBOBxAb1', 'bob'), [])
})

test('max_length: 128 characters and 511 bytes', () => {
  // 'cd' repeated: no run longer than max_repeat
  assert.deepEqual(failedIds(spec, 'Ab1' + 'cd'.repeat(62) + 'e'), [])
  assert.deepEqual(failedIds(spec, 'Ab1' + 'cd'.repeat(62) + 'ef'), ['max_length'])
  // 130 characters of 3 bytes = 390 bytes: fine by bytes, refused by characters
  assert.deepEqual(failedIds(spec, 'è'.repeat(130)), ['max_length'])
  // 127 emoji = 127 characters, 508 bytes: fine; 128 emoji = 512 bytes: refused by bytes (PAM buffer)
  assert.deepEqual(failedIds(spec, '😀'.repeat(127)), ['classes'])
  assert.deepEqual(failedIds(spec, '😀'.repeat(128)), ['max_length'])
})

test('control characters are refused, not a row', () => {
  assert.deepEqual(failedIds(spec, 'Abcdef1\n'), ['control'])
  assert.equal(isValid(spec, 'Abcdef1\n'), false)
  assert.equal(evaluate(spec, 'Abcdef1\n').every(r => r.ok), true)
})

test('unknown row ids are ok', () => {
  const extra = {...spec, rules: [...spec.rules, {id: 'future_rule', text: 'Later'}]}
  assert.equal(evaluate(extra, 'Abcdef12').at(-1).ok, true)
})

test('non-string password', () => {
  assert.deepEqual(failedIds(spec, 12345678), ['type'])
  assert.equal(isValid(spec, null), false)
})
