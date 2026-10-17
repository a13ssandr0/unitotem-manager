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
                <v-btn color="primary" block size="large" variant="elevated" @click="goHome">Continue</v-btn>
              </v-card-text>

              <v-card-text v-else>
                <p class="mb-4">
                  This device has no accounts yet. Choose the administrator account: its username and password are
                  the ones of the operating system, and the ones to log in to this interface.
                </p>
                <v-form @submit.prevent="submit">
                  <v-text-field label="Administrator username" v-model="username" variant="outlined"
                                autocomplete="username" name="username" maxlength="32"></v-text-field>
                  <v-text-field label="Password" v-model="password" variant="outlined" type="password"
                                autocomplete="new-password" name="password" :maxlength="max_length"></v-text-field>
                  <v-text-field label="Repeat the password" v-model="repeat" variant="outlined" type="password"
                                autocomplete="new-password" name="password-repeat" :maxlength="max_length"></v-text-field>

                  <ul class="text-caption text-medium-emphasis mb-4 pl-4">
                    <li v-for="rule in rules" :key="rule">{{ rule }}</li>
                  </ul>

                  <v-alert v-if="errors.length" type="error" variant="tonal" density="compact" class="mb-2">
                    <div v-for="error in errors" :key="error">{{ error }}</div>
                  </v-alert>

                  <v-btn type="submit" color="primary" block size="large" variant="elevated" class="mt-4"
                         :loading="busy" :disabled="busy">
                    Create administrator
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
import { onMounted, ref } from 'vue'

const state = window.__INITIAL_STATE__
const hostname = ref(state.hostname)
const ip_addr = ref(state.ip_addr)
const os_vers = ref(state.os_vers)
const rules = state.rules || []
const min_length = state.min_length
const max_length = state.max_length

const username = ref('admin')
const password = ref('')
const repeat = ref('')
const errors = ref([])
const busy = ref(false)
const done = ref(null)

function check() {
  const problems = []
  if (!username.value.trim()) problems.push('Enter a username')
  if (password.value.length < min_length) problems.push(`The password must be at least ${min_length} characters long`)
  if (password.value.toLowerCase() === username.value.toLowerCase()) problems.push('The password must be different from the username')
  if (password.value !== repeat.value) problems.push('The two passwords do not match')
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
        username: username.value,
        password: password.value,
      }),
    })
    if (response.status === 404) {
      // the device already has an administrator: this page does not exist any more
      window.location.href = '/'
      return
    }
    const body = await response.json().catch(() => ({}))
    if (response.ok) {
      done.value = body
      return
    }
    errors.value = Array.isArray(body.detail) ? body.detail : ['The request was refused']
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
