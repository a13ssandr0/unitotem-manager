<template>
  <v-app>
    <v-main>
      <v-container class="fill-height" fluid>
        <v-row align="center" justify="center" class="text-center">
          <v-col cols="12">
            <h1 class="text-h4 mb-10" style="width:450px; margin: 0 auto;">Welcome to UniTotem</h1>
            <v-card class="elevation-12 pa-4 d-inline-block" width="450" rounded="lg">
              <v-card-title>{{ hostname }} - {{ ip_addr }}</v-card-title>
              <v-card-subtitle class="mb-4">{{ os_vers }}</v-card-subtitle>
              <v-card-text>
                <v-form @submit.prevent="handleLogin">
                  <v-select v-if="usersList.length" label="Username" name="username" v-model="username" :items="usersList" variant="outlined"></v-select>
                  <v-text-field v-else label="Username" name="username" v-model="username" autocomplete="username" variant="outlined"></v-text-field>
                  <v-text-field id="password" label="Password" name="password" type="password" v-model="password" variant="outlined"></v-text-field>
                  <v-checkbox v-model="rememberMe" name="remember_me" label="Remember me" checked hide-details class="my-2"></v-checkbox>
                  <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-2">{{ error }}</v-alert>
                  <v-btn type="submit" color="primary" block size="large" variant="elevated" class="mt-4">Log In</v-btn>
                </v-form>
              </v-card-text>
            </v-card>
            <div class="mt-4 text-grey" style="width:450px; margin: 0 auto;">
              <a href="https://github.com/a13ssandr0/unitotem" target="_blank" rel="noopener noreferrer"
                 class="text-high-emphasis">
                <v-icon size="small">mdi-github</v-icon>
                <span>Unitotem</span>
              </a>
              {{ unitotem_version }} by a13ssandr0
            </div>
          </v-col>
        </v-row>
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useStorage } from '@vueuse/core'

const username = useStorage('last_user', '', localStorage)
const password = ref('')
const rememberMe = ref(true)
const hostname = ref(window.__INITIAL_STATE__.hostname)
const ip_addr = ref(window.__INITIAL_STATE__.ip_addr)
const os_vers = ref(window.__INITIAL_STATE__.os_vers)
const unitotem_version = ref(window.__INITIAL_STATE__.ut_vers)
const usersList = ref([])
const error = ref('')

const handleLogin = () => {
  const formData = new URLSearchParams()
  formData.append('username', username.value)
  formData.append('password', password.value)
  if (rememberMe.value) {
    formData.append('remember_me', 'on')
  }

  fetch(`/auth/token`, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: formData,
    credentials: "include"
  }).then((resp) => {
    if (resp.status === 401) {
      error.value = 'Wrong user name or password'
    } else if (resp.status === 429) {
      error.value = 'Too many attempts, try again in a minute'
    } else if (!resp.ok) {
      error.value = 'Login failed (' + resp.status + ')'
    } else {
      window.location.href = window.__INITIAL_STATE__.src || '/';
    }
  }).catch(err => {
    console.error('Login failed', err)
    error.value = 'Cannot reach the node'
  })
}

onMounted(() => {
  document.title = hostname.value + ' - UniTotem Login'

  fetch(`/login/users`).then(res => res.ok ? res.json() : []).then(users => {
    usersList.value = users
    if (users.length && !users.includes(username.value)){
      username.value = users[0]
    }
  }).catch(() => {})
})
</script>
