<template>
  <div class="d-flex flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      title="Hostname"
    >
      <v-card-text>
        <v-row align="center">
          <v-col cols="6">
            <v-text-field
              v-model="hostname"
              label="Hostname"
              variant="outlined"
              density="compact"
              hide-details
            ></v-text-field>
          </v-col>
          <v-col cols="auto">
            <v-btn
              @click="applyHostname"
              :disabled="!isHostnameChanged"
              color="primary"
              variant="text"
            >
              Apply
            </v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="!backend_running"
      type="warning"
      class="ma-4 mt-2"
      max-width="1000"
      width="100%"
    >
      Network backend not running, Netplan configuration will be unavailable.
    </v-alert>

    <v-card class="ma-4 mt-2" max-width="1000" width="100%" :disabled="!backend_running">
      <v-card-title class="d-flex align-center">
        Netplan configuration
        <a href="https://netplan.io/reference/" target="_blank" rel="noopener noreferrer" class="text-primary">
          <v-icon size="small" class="ml-2">mdi-help-circle</v-icon>
        </a>
        <v-spacer></v-spacer>
        <v-switch
          v-if="has_wireless"
          v-model="is_wireless_enabled"
          @update:model-value="toggleWifi"
          color="primary"
          hide-details
          density="compact"
          :label="'Wireless ' + (is_wireless_enabled ? 'on' : 'off')"
          class="mr-4"
        ></v-switch>
        <v-menu v-if="has_wireless" v-model="wifiMenu" :close-on-content-click="false" location="bottom end">
          <template v-slot:activator="{ props }">
            <v-btn :disabled="!is_wireless_enabled" v-bind="props" :color="is_wireless_enabled?'primary':''" append-icon="mdi-menu-down">
              <v-icon>mdi-wifi</v-icon>
            </v-btn>
          </template>
          <v-card min-width="300">
            <v-progress-linear indeterminate color="primary"></v-progress-linear>
            <div v-if="!is_wireless_enabled" class="text-center pa-4 text-grey">
              Wireless disabled
            </div>
            <div v-else-if="wifiNetworks.length === 0" class="text-center pa-4 text-grey">
              Scanning...
            </div>
            <v-list v-else>
              <v-list-item v-for="(net, i) in wifiNetworks" :key="i">
                <template v-slot:prepend>
                  <v-icon :icon="getSignalIcon(net.strength, net.flags)"></v-icon>
                </template>
                <v-list-item-title class="d-flex">
                  <span>{{ net.ssid }}</span>
                  <v-spacer></v-spacer>
                  <v-chip size="x-small" class="mr-auto">{{ getBand(net.frequency) }}</v-chip>
                </v-list-item-title>
                <v-list-item-subtitle>{{ net.hw_address }}</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-card>
        </v-menu>
      </v-card-title>
      <v-row>
        <v-col cols="3">
          <v-list density="compact" style="max-height: 400px; overflow-y: auto;">
            <v-list-item
              v-for="file in yamlFiles"
              :key="file"
              @click="switchFile(file)"
              :class="{ 'v-list-item--active': selectedFile === file }"
            >
              <v-list-item-title>
                <span v-if="isFileDirtyByName(file)">• </span>{{ file }}
                <v-tooltip activator="parent" location="bottom">{{ file }}</v-tooltip>
              </v-list-item-title>
              <template v-slot:append>
                <v-btn icon="mdi-delete" size="small" variant="text" color="red"
                       @click.stop="requestDeleteFile(file)"></v-btn>
              </template>
            </v-list-item>
          </v-list>
        </v-col>
        <v-col cols="9" style="position: relative">
          <template v-if="selectedFile">
            <div style="border: 1px solid #ccc; border-radius: 4px; overflow: hidden;">
              <MonacoEditor
                v-model="fileContent"
                language="yaml"
                :options="{ theme: theme.global.current.value.dark ? 'vs-dark' : 'vs', automaticLayout: true }"
                style="height: 400px"
                @editorDidMount="onEditorMounted"
              />
            </div>
            <div
              v-if="!isEditorReady"
              class="d-flex flex-column align-center justify-center"
              style="position: absolute; top: 0; left: 0; width: 100%; height: 400px; z-index: 10;"
            >
              <p class="mb-3">Loading editor...</p>
              <v-progress-circular
                color="primary"
                indeterminate
                :size="54"
                :width="5"
              ></v-progress-circular>
            </div>
          </template>
          <div v-else class="d-flex align-center justify-center" style="height: 400px;">
            <p>Select a file to edit.</p>
          </div>
        </v-col>
      </v-row>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn color="secondary" @click="discardFileChanges" :disabled="!isFileDirty">Discard</v-btn>
        <v-btn color="primary" :disabled="!isFileDirty" @click="saveChanges">Save</v-btn>
        <v-btn color="warning" :disabled="!isFileDirty" @click="applyChanges">Apply</v-btn>
      </v-card-actions>
    </v-card>

    <!-- Dialogs -->
    <v-dialog v-model="dialogDelete" max-width="450px">
      <v-card>
        <v-card-title class="text-h5">Delete file</v-card-title>
        <v-card-text>
          Are you sure you want to delete <strong>{{ fileToDelete }}</strong>? This action cannot be undone.
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="closeDeleteDialog">Cancel</v-btn>
          <v-btn color="red darken-1" text @click="confirmDeleteFile">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="dialogCreate" max-width="500px">
      <v-card>
        <v-card-title>Create new file</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newFileName"
            label="File name"
            variant="outlined"
            suffix=".yaml"
            autofocus
            @keyup.enter="confirmCreateFile"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="closeCreateDialog">Cancel</v-btn>
          <v-btn color="primary" @click="confirmCreateFile">Create</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="dialogLeave" max-width="500px">
      <v-card>
        <v-card-title class="text-h5">Unsaved changes</v-card-title>
        <v-card-text>You have unsaved changes. Are you sure you want to leave?</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="confirmLeave(false)">Cancel</v-btn>
          <v-btn color="warning" text @click="confirmLeave(true)">Leave</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-btn
      class="ma-4"
      position="fixed"
      location="bottom right"
      icon="mdi-file-document-plus"
      color="primary"
      @click="dialogCreate = true"
      aria-label="New file"
    ></v-btn>
  </div>
</template>

<script setup>
import {ref, computed, reactive, onMounted, onBeforeUnmount, watch} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import MonacoEditor from 'vue-monaco-cdn'
import { useTheme } from 'vuetify'

const theme = useTheme()

const hostname = ref('')
const originalHostname = ref('')

const yamlFiles = ref([])
const selectedFile = ref(null)
const fileContent = ref('')

const isEditorReady = ref(false)

const backend_running = ref(false);

// Dialog states
const dialogDelete = ref(false)
const fileToDelete = ref(null)
const dialogCreate = ref(false)
const newFileName = ref('')
const dialogLeave = ref(false)
let resolveLeave = () => {}

// WiFi state
const loadingWifi = ref(false)
const has_wireless = ref(false)
const is_wireless_enabled = ref(false)
const wifiNetworks = ref([])
const wifiMenu = ref(false)

// Content states
const originalFileContents = reactive(new Map())
const unsavedChanges = reactive(new Map())

const isHostnameChanged = computed(() => hostname.value !== originalHostname.value)

const isFileDirty = computed(() => {
  if (!selectedFile.value) return false
  const original = originalFileContents.get(selectedFile.value)
  return original !== fileContent.value
})

const hasUnsavedChanges = computed(() => isHostnameChanged.value || unsavedChanges.size > 0 || isFileDirty.value)

const onEditorMounted = () => {
  isEditorReady.value = true
}

watch(selectedFile, (newVal, oldVal) => {
  if (newVal && !oldVal) {
    isEditorReady.value = false
  }
})

const sendCommand = window.sendCommand;
window.setInitCommands("Settings/hostname", "Settings/Netplan/getFile", "Settings/has_wireless")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/hostname":
      hostname.value = data.hostname;
      originalHostname.value = data.hostname;
      break;
    case "Settings/Netplan/getFile":
      if (data.error) {
        backend_running.value = false;
      } else {
        backend_running.value = true;
        yamlFiles.value = Object.keys(data.files);
        originalFileContents.clear();

        Object.entries(data.files).forEach(([filename, content]) => {
          originalFileContents.set(filename, content);
        });
        if (selectedFile.value === null) switchFile(yamlFiles.value[0]);
      }
      break;
    case "Settings/has_wireless":
      has_wireless.value = data.wireless
      if (data.wireless)
        sendCommand('Settings/is_wireless_enabled');
      break;
    case "Settings/is_wireless_enabled":
      is_wireless_enabled.value = data.enabled
      break;
    case "Settings/get_wireless_networks":
      wifiNetworks.value = data.wifis;
      loadingWifi.value = false;
      break;
  }
}


const beforeWindowUnload = (e) => {
  if (hasUnsavedChanges.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onMounted(() => window.addEventListener('beforeunload', beforeWindowUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeWindowUnload))

onBeforeRouteLeave((to, from, next) => {
  if (hasUnsavedChanges.value) {
    dialogLeave.value = true
    resolveLeave = next
  } else {
    next()
  }
})

const confirmLeave = (confirm) => {
  resolveLeave(confirm)
  dialogLeave.value = false
}

const isFileDirtyByName = (file) => {
  return unsavedChanges.has(file) || (file === selectedFile.value && isFileDirty.value)
}

const applyHostname = () => {
  originalHostname.value = hostname.value
  console.log('Hostname applied:', hostname.value)
}

const switchFile = (file) => {
  if (selectedFile.value && isFileDirty.value) {
    unsavedChanges.set(selectedFile.value, fileContent.value)
  }
  loadFileContent(file)
}

const loadFileContent = async (file) => {
  selectedFile.value = file
  if (unsavedChanges.has(file)) {
    fileContent.value = unsavedChanges.get(file)
    return
  }
  fileContent.value = originalFileContents.get(file)
}

const requestDeleteFile = (file) => {
  fileToDelete.value = file
  dialogDelete.value = true
}

const confirmDeleteFile = () => {
  if (!fileToDelete.value) return
  const file = fileToDelete.value
  const index = yamlFiles.value.indexOf(file)
  if (index > -1) {
    yamlFiles.value.splice(index, 1)
    originalFileContents.delete(file)
    unsavedChanges.delete(file)
    if (selectedFile.value === file) {
      selectedFile.value = null
      fileContent.value = ''
    }
  }
  console.log('Deleted file:', file)
  closeDeleteDialog()
}

const closeDeleteDialog = () => {
  dialogDelete.value = false
  fileToDelete.value = null
}

const closeCreateDialog = () => {
  dialogCreate.value = false
  newFileName.value = ''
}

const confirmCreateFile = () => {
  if (!newFileName.value.trim()) return
  const finalName = `${newFileName.value.trim()}.yaml`
  if (yamlFiles.value.includes(finalName)) {
    console.error('File already exists')
    return
  }
  yamlFiles.value.push(finalName)
  closeCreateDialog()
  switchFile(finalName)
}

const saveChanges = () => {
  if (!selectedFile.value || !isFileDirty.value) return
  const currentFile = selectedFile.value
  originalFileContents.set(currentFile, fileContent.value)
  unsavedChanges.delete(currentFile)
  console.log(`Saving ${currentFile}...`, fileContent.value)
}

const applyChanges = () => {
  if (!selectedFile.value || !isFileDirty.value) return
  saveChanges()
  console.log(`Applying changes for ${selectedFile.value}...`)
}

const discardFileChanges = () => {
  if (selectedFile.value) {
    fileContent.value = originalFileContents.get(selectedFile.value)
    unsavedChanges.delete(selectedFile.value)
  }
}

let wifi_upd_timer = null;
watch(wifiMenu, () => {
  if (wifiMenu.value && is_wireless_enabled.value) {
    sendCommand('Settings/get_wireless_networks');
    loadingWifi.value = true
    wifi_upd_timer=setInterval(sendCommand, 5000, 'Settings/get_wireless_networks')
  } else if (wifi_upd_timer !== null) {
    clearInterval(wifi_upd_timer);
  }
})

const toggleWifi = (val) => {
  sendCommand('Settings/set_wireless_enabled', {enabled: val})
}

const getSignalIcon = (strength, flags) => {
  let icon = 'mdi-wifi-strength-'
  if (strength > 75) icon += '4'
  else if (strength > 50) icon += '3'
  else if (strength > 25) icon += '2'
  else icon += '1'
  // noinspection JSBitwiseOperatorUsage
  if (flags & 1) icon += '-lock'
  return icon
}

const getBand = (frequency) => {
  if (frequency >= 5925) return '6 GHz'
  if (frequency >= 5000) return '5 GHz'
  if (frequency >= 2400) return '2.4 GHz'
  return `${frequency} MHz`
}

if (yamlFiles.value.length > 0) {
  switchFile(yamlFiles.value[0])
}
</script>

<style scoped>
.v-list-item--active {
  background-color: rgba(0, 0, 0, 0.1);
}
</style>
