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
                  This device has no accounts yet. Create the administrator account. It is a normal administrator
                  account: its username and password are the ones of the operating system and of this interface,
                  and it also gets SSH and terminal access, with sudo. After setup, the root account is locked to
                  SSH keys.
                </p>
                <v-form @submit.prevent="submit">
                  <v-text-field label="Administrator username" v-model="username" variant="outlined"
                                autocomplete="username" name="username" maxlength="32"></v-text-field>
                  <v-text-field label="Password" v-model="password" variant="outlined" type="password"
                                autocomplete="new-password" name="password" :maxlength="max_length"></v-text-field>
                  <v-text-field label="Repeat the password" v-model="repeat" variant="outlined" type="password"
                                autocomplete="new-password" name="password-repeat" :maxlength="max_length"></v-text-field>

                  <PasswordChecklist class="mb-4" :spec="spec" :password="password" :username="username"
                                     :confirm="repeat" @valid="v => passwordValid = v"></PasswordChecklist>

                  <v-alert v-if="errors.length" type="error" variant="tonal" density="compact" class="mb-2">
                    <div v-for="error in errors" :key="error">{{ error }}</div>
                  </v-alert>

                  <v-btn type="submit" color="primary" block size="large" variant="elevated" class="mt-4"
                         :loading="busy" :disabled="busy || !canSubmit">
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
import { computed, onMounted, ref } from 'vue'
import PasswordChecklist from '@/components/PasswordChecklist.vue'

const state = window.__INITIAL_STATE__
const hostname = ref(state.hostname)
const ip_addr = ref(state.ip_addr)
const os_vers = ref(state.os_vers)
// the password policy spec from the server (min_length, max_length, rules: [{id, text}])
const spec = state.rules || null
const max_length = spec?.max_length

const username = ref('admin')
const password = ref('')
const repeat = ref('')
const errors = ref([])
const busy = ref(false)
const done = ref(null)
// set by the checklist: every password rule met and the two passwords match
const passwordValid = ref(false)
const canSubmit = computed(() => passwordValid.value && username.value.trim() !== '')

function check() {
  const problems = []
  if (!username.value.trim()) problems.push('Enter a username')
  return problems
}

async function submit() {
  errors.value = check()
  if (errors.value.length || !canSubmit.value) return
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
