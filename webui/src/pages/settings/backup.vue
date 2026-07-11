<template>
  <div class="d-flex flex-column align-center pa-4">
    <div class="w-100" style="max-width: 1050px;">
      <div class="actions-grid">
        <v-card class="d-flex flex-column">
          <v-card-title>Backup</v-card-title>
          <v-card-text>
            Create a backup of all application settings and data.
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-checkbox v-model="backupIncludeFiles" label="Include uploaded files" hide-details color="primary"></v-checkbox>
            <v-spacer></v-spacer>
            <v-btn color="primary" :loading="backupLoading" @click="doBackup">Backup</v-btn>
          </v-card-actions>
        </v-card>

        <v-card class="d-flex flex-column">
          <v-card-title>Restore</v-card-title>
          <v-card-text>
            Restore settings and data from a backup file.
            <v-file-input v-model="restoreFile" label="Backup file" accept=".zip"
                          variant="outlined" density="compact" class="mt-2" hide-details></v-file-input>
            <div class="mt-2">
              <v-checkbox v-model="restoreConfig" label="Playlists &amp; Users" hide-details density="compact"></v-checkbox>
              <v-checkbox v-model="restoreHostname" label="Hostname" hide-details density="compact"></v-checkbox>
              <v-checkbox v-model="restoreAudio" label="Audio device" hide-details density="compact"></v-checkbox>
              <v-checkbox v-model="restoreNetplan" label="Network (netplan)" hide-details density="compact"></v-checkbox>
              <v-checkbox v-model="restoreUploaded" label="Uploaded files" hide-details density="compact"></v-checkbox>
            </div>
          </v-card-text>
          <v-spacer></v-spacer>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="secondary" :loading="restoreLoading" :disabled="!restoreFile" @click="doRestore">Restore</v-btn>
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
            <v-btn color="error" @click="resetDialog = true">Reset</v-btn>
          </v-card-actions>
        </v-card>
      </div>

      <v-snackbar v-model="snackbar" :color="snackbarColor" timeout="3000">{{ snackbarText }}</v-snackbar>
    </div>
  </div>

  <v-dialog v-model="resetDialog" max-width="400">
    <v-card class="pa-2 pb-0 rounded-lg">
      <v-card-title class="text-h5">Factory reset?</v-card-title>
      <v-card-text>All playlists, users and uploaded files will be deleted. This cannot be undone.</v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn text @click="resetDialog = false">Cancel</v-btn>
        <v-btn color="error" text :loading="resetLoading" @click="doReset">Reset</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { ref } from 'vue'

const backupIncludeFiles = ref(false)
const backupLoading = ref(false)

const restoreFile = ref(null)
const restoreConfig = ref(true)
const restoreHostname = ref(false)
const restoreAudio = ref(false)
const restoreNetplan = ref(false)
const restoreUploaded = ref(false)
const restoreLoading = ref(false)

const resetDialog = ref(false)
const resetLoading = ref(false)

const snackbar = ref(false)
const snackbarText = ref('')
const snackbarColor = ref('success')

function notify(text, color = 'success') {
  snackbarText.value = text
  snackbarColor.value = color
  snackbar.value = true
}

async function doBackup() {
  backupLoading.value = true
  try {
    const resp = await fetch('/backup?include_uploaded=' + backupIncludeFiles.value, {credentials: 'include'})
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
    fd.append('CONFIG', restoreConfig.value)
    fd.append('def_audio_dev', restoreAudio.value)
    fd.append('hostname', restoreHostname.value)
    fd.append('netplan', restoreNetplan.value)
    fd.append('uploaded', restoreUploaded.value)
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
    const resp = await fetch('/backup', {method: 'DELETE', credentials: 'include'})
    resetDialog.value = false
    if (resp.ok) {
      notify('Factory reset complete')
    } else {
      notify('Reset failed: ' + resp.statusText, 'error')
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
