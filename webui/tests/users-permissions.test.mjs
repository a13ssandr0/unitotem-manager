// node --test tests/*.test.mjs
// The permission names of the Users page must be the server's UserPerms (manager/utils/models/user.py) and the
// ones of the generated API types (src/api.d.ts): a permission added on one side only fails here.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const read = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8')

// class UserPerms(str, Enum): one `name = "value"` line per member
const pyEnum = read('../../manager/utils/models/user.py').match(/class UserPerms\(str, Enum\):\n([\s\S]*?)\n\n/)?.[1]
const serverPerms = [...(pyEnum ?? '').matchAll(/^\s+(\w+) = "(\w+)"$/gm)].map(m => m[2])

// the generated type of set_user_permissions: perms: ("scheduler" | "power" | "audio" | "admin")[]
const dts = read('../src/api.d.ts').match(/set_user_permissions: ApiCall<\{ username: string; perms: \(([^)]*)\)\[\] \}>/)?.[1]
const typedPerms = [...(dts ?? '').matchAll(/"(\w+)"/g)].map(m => m[1])

// const permissions = ref(['Scheduler', 'Audio', 'Power', 'Admin']) in the Users page, lowercased for the checkboxes
const vue = read('../src/pages/settings/users.vue').match(/const permissions = ref\(\[([^\]]*)\]\)/)?.[1]
const pagePerms = [...(vue ?? '').matchAll(/'(\w+)'/g)].map(m => m[1].toLowerCase())

test('the parsers find the permissions in every source', () => {
  assert.ok(serverPerms.length >= 4, `UserPerms not found: ${JSON.stringify(serverPerms)}`)
  assert.ok(typedPerms.length >= 4, `api.d.ts set_user_permissions not found: ${JSON.stringify(typedPerms)}`)
  assert.ok(pagePerms.length >= 4, `users.vue permissions not found: ${JSON.stringify(pagePerms)}`)
})

test('the Users page lists exactly the server permissions', () => {
  assert.deepEqual([...pagePerms].sort(), [...serverPerms].sort())
  assert.equal(new Set(pagePerms).size, pagePerms.length, 'no duplicates')
})

test('the generated API types list exactly the server permissions', () => {
  assert.deepEqual([...typedPerms].sort(), [...serverPerms].sort())
})
