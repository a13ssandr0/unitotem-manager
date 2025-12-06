<template>
  <v-app>
    <v-main>
      <v-container class="fill-height" fluid>
        <v-row align="center" justify="center" class="text-center">
          <v-col cols="12">
            <h1 class="text-h4 mb-10" style="width:450px; margin: 0 auto;">Welcome to UniTotem</h1>
            <v-card class="elevation-12 pa-4 d-inline-block" width="450" rounded="lg">
              <v-card-title class="mb-4">UniTotem 3.0.0</v-card-title>
              <v-card-text>
                <v-form>
                  <v-text-field label="Username" name="login" type="text" v-model="username" variant="outlined"></v-text-field>
                  <v-text-field id="password" label="Password" name="password" type="password" v-model="password" variant="outlined"></v-text-field>
                  <v-checkbox v-model="rememberMe" label="Remember me" checked hide-details class="my-2"></v-checkbox>
                  <v-btn color="primary" block size="large" variant="elevated" class="mt-4">Log In</v-btn>
                </v-form>
              </v-card-text>
            </v-card>
            <div class="mt-4 text-grey" style="width:450px; margin: 0 auto;">{{ hostname }} - {{ ip_addr }}</div>
          </v-col>
        </v-row>
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const username = ref('admin')
const password = ref('')
const rememberMe = ref(true)
const hostname = ref('hostname')
const ip_addr = ref('192.168.1.1')

onMounted(() => {
  document.title = hostname.value + ' - UniTotem Login'
})
</script>
