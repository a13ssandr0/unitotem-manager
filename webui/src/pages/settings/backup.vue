<template>
  <div class="d-flex flex-column align-center pa-4">
    <div class="w-100" style="max-width: 1050px;">
      <div class="actions-grid">
        <v-card class="d-flex flex-column">
          <v-card-title>Backup</v-card-title>
          <v-card-text>
            Create a backup of the selected settings and data.
            <div class="mt-2">
              <v-checkbox v-for="part in parts" :key="part.key" v-model="backupSelection" :value="part.key"
                          :label="part.label" hide-details density="compact"></v-checkbox>
            </div>
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="primary" :loading="backupLoading" :disabled="!backupSelection.length" @click="doBackup">Backup</v-btn>
          </v-card-actions>
        </v-card>

        <v-card class="d-flex flex-column">
          <v-card-title>Restore</v-card-title>
          <v-card-text>
            Restore settings and data from a backup file.
            <v-file-input v-model="restoreFile" label="Backup file" accept=".zip"
                          variant="outlined" density="compact" class="mt-2" hide-details></v-file-input>
            <div class="mt-2">
              <v-checkbox v-for="part in parts" :key="part.key" v-model="restoreSelection" :value="part.key"
                          :label="part.label" hide-details density="compact"></v-checkbox>
            </div>
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="secondary" :loading="restoreLoading" :disabled="!restoreFile || !restoreSelection.length"
                   @click="doRestore">Restore</v-btn>
          </v-card-actions>
        </v-card>

        <v-card class="d-flex flex-column">
          <v-card-title>Reset</v-card-title>
          <v-card-text>
            Reset the selected settings and data to factory defaults. This action is irreversible.
            <div class="mt-2">
              <v-checkbox v-for="part in resettable" :key="part.key" v-model="resetSelection" :value="part.key"
                          :label="part.label" hide-details density="compact"></v-checkbox>
            </div>
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="error" :disabled="!resetSelection.length" @click="resetDialog = true">Reset</v-btn>
          </v-card-actions>
        </v-card>
      </div>

      <v-snackbar v-model="snackbar" :color="snackbarColor" timeout="3000">{{ snackbarText }}</v-snackbar>
    </div>
  </div>

  <v-dialog v-model="resetDialog" max-width="400">
    <v-card class="pa-2 pb-0 rounded-lg">
      <v-card-title class="text-h5">Reset the selected parts?</v-card-title>
      <v-card-text>
        {{ resetLabels }} will go back to factory defaults. This cannot be undone.
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn text @click="resetDialog = false">Cancel</v-btn>
        <v-btn color="error" text :loading="resetLoading" @click="doReset">Reset</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

// What this server can back up, restore and reset: the core's parts plus whatever the installed plugins add
const parts = ref([])
const resettable = computed(() => parts.value.filter(p => p.reset))
const backupSelection = ref([])
const restoreSelection = ref([])
const resetSelection = ref([])

const backupLoading = ref(false)
const restoreFile = ref(null)
const restoreLoading = ref(false)
const resetDialog = ref(false)
const resetLoading = ref(false)
const resetLabels = computed(() => resettable.value.filter(p => resetSelection.value.includes(p.key)).map(p => p.label).join(', '))

const snackbar = ref(false)
const snackbarText = ref('')
const snackbarColor = ref('success')

function notify(text, color = 'success') {
  snackbarText.value = text
  snackbarColor.value = color
  snackbar.value = true
}

onMounted(async () => {
  try {
    const resp = await fetch('/backup/parts', {credentials: 'include'})
    if (!resp.ok) return
    parts.value = await resp.json()
    // Backups include everything but what is big or slow (the uploaded files); a restore starts with nothing selected
    // so nothing is overwritten by accident
    backupSelection.value = parts.value.filter(p => p.default_in_backup).map(p => p.key)
  } catch (e) {
    notify('Cannot read what can be backed up: ' + e, 'error')
  }
})

async function doBackup() {
  backupLoading.value = true
  try {
    const query = backupSelection.value.map(key => 'parts=' + encodeURIComponent(key)).join('&')
    const resp = await fetch('/backup?' + query, {credentials: 'include'})
    if (!resp.ok) { notify('Backup failed: ' + resp.statusText, 'error'); return }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers.get('content-disposition') || ''
    a.download = cd.match(/filename="([^"]+)"/)?.[1] || 'backup.zip'
    a.click()
    URL.revokeObjectURL(url)
    notify('Backup downloaded')
  } catch (e) {
    notify('Backup error: ' + e, 'error')
  } finally {
    backupLoading.value = false
  }
}

async function doRestore() {
  if (!restoreFile.value) return
  restoreLoading.value = true
  try {
    const fd = new FormData()
    fd.append('backup_file', restoreFile.value)
    restoreSelection.value.forEach(key => fd.append('parts', key))
    const resp = await fetch('/backup', {method: 'POST', body: fd, credentials: 'include'})
    if (resp.ok) {
      notify('Restore completed')
    } else {
      const detail = await resp.json().catch(() => ({detail: resp.statusText}))
      notify('Restore failed: ' + (detail.detail || resp.statusText), 'error')
    }
  } catch (e) {
    notify('Restore error: ' + e, 'error')
  } finally {
    restoreLoading.value = false
  }
}

async function doReset() {
  resetLoading.value = true
  try {
    const query = resetSelection.value.map(key => 'parts=' + encodeURIComponent(key)).join('&')
    const resp = await fetch('/backup?' + query, {method: 'DELETE', credentials: 'include'})
    resetDialog.value = false
    if (resp.ok) {
      notify('Reset complete')
    } else {
      const detail = await resp.json().catch(() => ({detail: resp.statusText}))
      notify('Reset failed: ' + (detail.detail || resp.statusText), 'error')
    }
  } catch (e) {
    notify('Reset error: ' + e, 'error')
  } finally {
    resetLoading.value = false
  }
}
</script>

<style scoped>
.actions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

.actions-grid :deep(.v-card-actions) {
  align-items: center;
}

.actions-grid :deep(.v-selection-control) {
  min-height: 0 !important;
  height: 36px;
}
</style>
