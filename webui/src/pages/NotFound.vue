<template>
  <v-container class="fill-height" fluid>
    <v-row align="center" justify="center">
      <v-col cols="12" sm="8" md="6">
        <v-card class="text-center pa-8" elevation="4">
          <h1 class="text-h1 font-weight-bold text-blue">404</h1>
          <img :src="`data:image/png;base64,${img404}`" alt="404 Not Found" class="mt-4" style="height: 20rem" />
          <h2 class="text-h4 mt-4">Page Not Found</h2>
          <p class="mt-4">The page you are looking for does not exist.</p>
          <v-btn to="/" color="primary" class="mt-8">
            Go to Homepage
          </v-btn>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
// noinspection JSFileReferences
import img404 from '@/components/404.js';
</script>

<style scoped>
.text-h1 {
  font-size: 6rem !important;
}
</style>
