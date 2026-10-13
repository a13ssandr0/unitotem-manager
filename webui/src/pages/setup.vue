<template>
  <v-app>
    <v-main>
      <v-container class="fill-height" fluid>
        <v-row align="center" justify="center" class="text-center">
          <v-col cols="12">
            <h1 class="text-h4 mb-10" style="max-width:520px; margin: 0 auto;">Welcome to UniTotem</h1>
            <v-card class="elevation-12 pa-4 d-inline-block text-left" width="520" max-width="100%" rounded="lg">
              <v-card-title>{{ hostname }} - {{ ip_addr }}</v-card-title>
              <v-card-subtitle class="mb-4">{{ os_vers }}</v-card-subtitle>

              <v-card-text v-if="done">
                <v-alert type="success" variant="tonal" class="mb-4">
                  The device is ready. You are the administrator <b>{{ username }}</b>.
                </v-alert>
                <v-alert v-if="done.os_password === 'set'" type="info" variant="tonal" class="mb-4">
                  The password of the terminal account <b>{{ done.os_account }}</b> has been set.
                </v-alert>
                <v-alert v-else-if="done.os_password === 'failed'" type="warning" variant="tonal" class="mb-4">
                  The password of the terminal account <b>{{ done.os_account }}</b> could not be set.
                  The terminal stays unusable until it is set from the device.
                </v-alert>
                <v-btn color="primary" block size="large" variant="elevated" @click="goHome">Continue</v-btn>
              </v-card-text>

              <v-card-text v-else>
                <p class="mb-4">
                  This device has no accounts yet. Enter the <b>claim code</b> shown on its screen (or, for a device
                  without a screen, in its log and in <code>/var/lib/unitotem/claim-code</code>) and choose the
                  administrator account.
                </p>
                <v-form @submit.prevent="submit">
                  <v-text-field label="Claim code" v-model="code" variant="outlined" autocomplete="off"
                                autocapitalize="characters" spellcheck="false" maxlength="32"
                                placeholder="XXXXX-XXXXX" name="claim-code"></v-text-field>
                  <v-text-field label="Administrator username" v-model="username" variant="outlined"
                                autocomplete="username" name="username" maxlength="32"></v-text-field>
                  <v-text-field label="Password" v-model="password" variant="outlined" type="password"
                                autocomplete="new-password" name="password" :maxlength="max_length"></v-text-field>
                  <v-text-field label="Repeat the password" v-model="repeat" variant="outlined" type="password"
                                autocomplete="new-password" name="password-repeat" :maxlength="max_length"></v-text-field>

                  <ul class="text-caption text-medium-emphasis mb-4 pl-4">
                    <li v-for="rule in rules" :key="rule">{{ rule }}</li>
                  </ul>

                  <template v-if="os_account">
                    <v-divider class="mb-4"></v-divider>
                    <p class="mb-2">
                      The web terminal logs in to the operating-system account <b>{{ os_account }}</b>
                      (separate from the web accounts). Its password:
                    </p>
                    <v-radio-group v-model="osMode" hide-details class="mb-2">
                      <v-radio label="Same as the administrator password" value="same"></v-radio>
                      <v-radio label="A different password" value="different"></v-radio>
                      <v-radio label="Leave it as it is (the terminal stays unusable)" value="skip"></v-radio>
                    </v-radio-group>
                    <v-text-field v-if="osMode === 'different'" label="Terminal account password"
                                  v-model="osPassword" variant="outlined" type="password"
                                  autocomplete="new-password" name="os-password" :maxlength="max_length"></v-text-field>
                  </template>

                  <v-alert v-if="errors.length" type="error" variant="tonal" density="compact" class="mb-2">
                    <div v-for="error in errors" :key="error">{{ error }}</div>
                  </v-alert>

                  <v-btn type="submit" color="primary" block size="large" variant="elevated" class="mt-4"
                         :loading="busy" :disabled="busy || waiting > 0">
                    {{ waiting > 0 ? `Try again in ${waiting} s` : 'Create administrator' }}
                  </v-btn>
                </v-form>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const state = window.__INITIAL_STATE__
const hostname = ref(state.hostname)
const ip_addr = ref(state.ip_addr)
const os_vers = ref(state.os_vers)
const rules = state.rules || []
const min_length = state.min_length
const max_length = state.max_length
const os_account = state.os_account

const code = ref('')
const username = ref('admin')
const password = ref('')
const repeat = ref('')
const osMode = ref(os_account ? 'same' : 'skip')
const osPassword = ref('')
const errors = ref([])
const busy = ref(false)
const waiting = ref(0)
const done = ref(null)

let timer = null
function startCountdown(seconds) {
  waiting.value = seconds
  clearInterval(timer)
  timer = setInterval(() => {
    waiting.value = Math.max(0, waiting.value - 1)
    if (waiting.value === 0) clearInterval(timer)
  }, 1000)
}
onBeforeUnmount(() => clearInterval(timer))

function check() {
  const problems = []
  if (!code.value.trim()) problems.push('Enter the claim code')
  if (!username.value.trim()) problems.push('Enter a username')
  if (password.value.length < min_length) problems.push(`The password must be at least ${min_length} characters long`)
  if (password.value.toLowerCase() === username.value.toLowerCase()) problems.push('The password must be different from the username')
  if (password.value !== repeat.value) problems.push('The two passwords do not match')
  if (os_account && osMode.value === 'different' && osPassword.value.length < min_length) {
    problems.push(`The terminal account password must be at least ${min_length} characters long`)
  }
  return problems
}

async function submit() {
  errors.value = check()
  if (errors.value.length) return
  busy.value = true
  try {
    const response = await fetch('/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        code: code.value,
        username: username.value,
        password: password.value,
        os_password: os_account ? osMode.value : 'skip',
        os_password_value: osMode.value === 'different' ? osPassword.value : null,
      }),
    })
    if (response.status === 404) {
      // somebody else claimed the device first: this page does not exist any more
      window.location.href = '/'
      return
    }
    const body = await response.json().catch(() => ({}))
    if (response.ok) {
      done.value = body
      return
    }
    errors.value = Array.isArray(body.detail) ? body.detail : ['The request was refused']
    if (response.status === 429 && body.retry_after) startCountdown(body.retry_after)
  } catch (err) {
    errors.value = ['Cannot reach the device']
  } finally {
    busy.value = false
  }
}

const goHome = () => { window.location.href = '/' }

onMounted(() => {
  document.title = hostname.value + ' - UniTotem Setup'
})
</script>
