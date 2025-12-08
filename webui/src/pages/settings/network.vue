<script setup lang="ts">
import {ref, computed, reactive, onMounted, onBeforeUnmount} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import MonacoEditor from 'vue-monaco-cdn'

const hostname = ref('Unitotem')
const originalHostname = ref(hostname.value)

const yamlFiles = ref(['network.yaml', 'another.yaml'])
const selectedFile = ref<string | null>(null)
const fileContent = ref('')

// Dialog states
const dialogDelete = ref(false)
const fileToDelete = ref<string | null>(null)
const dialogCreate = ref(false)
const newFileName = ref('')
const dialogLeave = ref(false)
let resolveLeave: (confirm: boolean) => void = () => {
}

// WiFi state
const loadingWifi = ref(false)
const wifiNetworks = ref<any[]>([])
const wifiMenu = ref(false)

// Content states
const originalFileContents = reactive(new Map<string, string>())
const unsavedChanges = reactive(new Map<string, string>())

const isHostnameChanged = computed(() => hostname.value !== originalHostname.value)

const isFileDirty = computed(() => {
  if (!selectedFile.value) return false
  const original = originalFileContents.get(selectedFile.value)
  return original !== fileContent.value
})

const hasUnsavedChanges = computed(() => isHostnameChanged.value || unsavedChanges.size > 0 || isFileDirty.value)

const beforeWindowUnload = (e: BeforeUnloadEvent) => {
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

const confirmLeave = (confirm: boolean) => {
  resolveLeave(confirm)
  dialogLeave.value = false
}

const isFileDirtyByName = (file: string): boolean => {
  return unsavedChanges.has(file) || (file === selectedFile.value && isFileDirty.value)
}

const applyHostname = () => {
  originalHostname.value = hostname.value
  console.log('Hostname applied:', hostname.value)
}

const switchFile = (file: string) => {
  if (selectedFile.value && isFileDirty.value) {
    unsavedChanges.set(selectedFile.value, fileContent.value)
  }
  loadFileContent(file)
}

const loadFileContent = async (file: string) => {
  selectedFile.value = file
  if (unsavedChanges.has(file)) {
    fileContent.value = unsavedChanges.get(file) as string
    return
  }
  const response = `# Mock content for ${file}\nhostname: unitotem-default`
  fileContent.value = response
  originalFileContents.set(file, response)
}

const requestDeleteFile = (file: string) => {
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

const openCreateDialog = () => {
  dialogCreate.value = true
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
    fileContent.value = originalFileContents.get(selectedFile.value) as string
    unsavedChanges.delete(selectedFile.value)
  }
}

const scanWifi = () => {
  loadingWifi.value = true
  wifiNetworks.value = []
  setTimeout(() => {
    wifiNetworks.value = [
      {ssid: 'WiFi-Network-1', mac: '00:1B:44:11:3A:B7', signal: -45, band: '5GHz', security: true},
      {ssid: 'WiFi-Network-2', mac: '00:1B:44:11:3A:B8', signal: -75, band: '2.4GHz', security: false},
      {ssid: 'WiFi-Network-3', mac: '00:1B:44:11:3A:B9', signal: -85, band: '2.4GHz', security: true},
    ]
    loadingWifi.value = false
  }, 2000)
}

const getSignalIcon = (signal: number, security: boolean): string => {
  let icon = 'mdi-wifi-strength-'
  if (signal > -50) icon += '4'
  else if (signal > -70) icon += '3'
  else if (signal > -80) icon += '2'
  else icon += '1'
  if (security) icon += '-lock'
  return icon
}

if (yamlFiles.value.length > 0) {
  switchFile(yamlFiles.value[0])
}
</script>

<template>
  <div class="d-flex flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
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

    <v-card class="ma-4 mt-0" max-width="1000" width="100%">
      <v-card-title class="d-flex align-center">
        Netplan configuration
        <a href="https://netplan.io/reference/" target="_blank" rel="noopener noreferrer" class="text-primary">
          <v-icon size="small" class="ml-2">mdi-help-circle</v-icon>
        </a>
        <v-spacer></v-spacer>
        <v-menu v-model="wifiMenu" :close-on-content-click="false" location="bottom end">
          <template v-slot:activator="{ props }">
            <v-btn v-bind="props" @click="scanWifi" append-icon="mdi-menu-down">
              <v-icon>mdi-wifi</v-icon>
            </v-btn>
          </template>
          <v-card min-width="300">
            <v-progress-linear indeterminate color="primary"></v-progress-linear>
            <div v-if="wifiNetworks.length === 0" class="text-center pa-4 text-grey">
              Scanning...
            </div>
            <v-list v-else>
              <v-list-item v-for="(net, i) in wifiNetworks" :key="i">
                <template v-slot:prepend>
                  <v-icon :icon="getSignalIcon(net.signal, net.security)"></v-icon>
                </template>
                <v-list-item-title class="d-flex">
                  <span>{{ net.ssid }}</span>
                  <v-spacer></v-spacer>
                  <v-chip size="x-small" class="mr-auto">{{ net.band }}</v-chip>
                </v-list-item-title>
                <v-list-item-subtitle>{{ net.mac }}</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-card>
        </v-menu>
      </v-card-title>
      <v-row>
        <v-col cols="3">
          <v-list density="compact">
            <v-list-item
              v-for="file in yamlFiles"
              :key="file"
              @click="switchFile(file)"
              :class="{ 'v-list-item--active': selectedFile === file }"
            >
              <v-list-item-title>
                <span v-if="isFileDirtyByName(file)">• </span>{{ file }}
              </v-list-item-title>
              <template v-slot:append>
                <v-btn icon="mdi-delete" size="small" variant="text" color="red"
                       @click.stop="requestDeleteFile(file)"></v-btn>
              </template>
            </v-list-item>
          </v-list>
        </v-col>
        <v-col cols="9">
          <MonacoEditor
            v-if="selectedFile"
            v-model="fileContent"
            language="yaml"
            :options="{ theme: 'vs-dark', automaticLayout: true }"
            style="height: 400px"
          />
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

<style scoped>
.v-list-item--active {
  background-color: rgba(0, 0, 0, 0.1);
}
</style>
