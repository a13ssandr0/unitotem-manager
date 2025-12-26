<template>
  <div class="d-flex flex-column align-center pa-4">
    <div class="w-100" style="max-width: 1000px;">
      <div class="actions-grid">
        <v-card class="d-flex flex-column">
          <v-card-title>Backup</v-card-title>
          <v-card-text>
            Create a backup of all application settings and data.
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-checkbox v-model="includeFiles" label="Include uploaded files" hide-details color="primary"></v-checkbox>
            <v-spacer></v-spacer>
            <v-btn color="primary">Backup</v-btn>
          </v-card-actions>
        </v-card>

        <v-card class="d-flex flex-column">
          <v-card-title>Restore</v-card-title>
          <v-card-text>
            Restore settings and data from a backup file.
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="secondary">Restore</v-btn>
          </v-card-actions>
        </v-card>

        <v-card class="d-flex flex-column">
          <v-card-title>Reset</v-card-title>
          <v-card-text>
            Reset all settings to factory defaults. This action is irreversible.
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="error">Reset</v-btn>
          </v-card-actions>
        </v-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const includeFiles = ref(false)
</script>

<style scoped>
.actions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 16px;
}

.actions-grid :deep(.v-card-actions) {
  align-items: center;
}

.actions-grid :deep(.v-selection-control) {
  min-height: 0 !important;
  height: 36px; /* Match button height */
}
</style>
