<template>
  <v-container fluid>
    <v-row align="start">
      <!-- Colonna di Sinistra: Files -->
      <v-col cols="12" md="4">
        <v-card>
          <v-card-title
            class="d-flex align-center transition-swing"
            :class="{ 'selected-title-bar': selectedFiles.length > 0 }"
          >
            <!-- Stato Normale -->
            <template v-if="selectedFiles.length === 0">
              <span>Files</span>
              <v-spacer></v-spacer>
              <v-btn color="green" @click="triggerFileUpload" prepend-icon="mdi-upload">
                Upload
              </v-btn>
            </template>

            <!-- Stato di Selezione -->
            <template v-else>
              <span class="text-subtitle-1">{{ selectedFiles.length }} selected</span>
              <v-spacer></v-spacer>
              <v-btn icon="mdi-login" variant="tonal" color="blue" size="small" title="Add to Playlist" @click="addSelected"></v-btn>
              <v-btn icon="mdi-download" variant="text" size="small" title="Download" @click="downloadSelected"></v-btn>
              <v-btn icon="mdi-delete" color="red" variant="text" size="small" title="Delete" @click="deleteSelected"></v-btn>
              <v-divider vertical class="mx-2"></v-divider>
              <v-btn icon="mdi-select-all" variant="text" size="small" @click="selectAll" title="Select All"></v-btn>
              <v-btn icon="mdi-select-off" variant="text" size="small" @click="deselectAll" title="Deselect All"></v-btn>
            </template>

            <input
              type="file"
              ref="fileInput"
              hidden
              multiple
              @change="handleFileUpload"
            />
          </v-card-title>

          <v-divider></v-divider>

          <v-list density="compact" class="files-list">
            <v-list-item
              v-for="(file, filename) in mediaFiles"
              :key="filename"
              class="py-0"
              @click="toggleSelection(filename)"
              :active="selectedFiles.includes(filename)"
            >
              <template v-slot:prepend>
                <v-checkbox-btn :model-value="selectedFiles.includes(filename)" hide-details></v-checkbox-btn>
              </template>
              <v-list-item-title>{{ file.filename }}</v-list-item-title>
              <v-list-item-subtitle>Duration: {{ file.duration }} | Size: {{ file.size }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>
          <v-divider></v-divider>
          <v-card-subtitle class="my-1">Used {{ disk_used }} of {{ disk_total }}</v-card-subtitle>
        </v-card>
      </v-col>

      <!-- Colonna di Destra: Playlist -->
      <v-col cols="12" md="8">
        <v-card>
          <v-card-title class="d-flex align-center">
            Playlist
            <v-spacer></v-spacer>
            <v-btn icon="mdi-cog" title="Settings" variant="text" density="compact" class="mr-4" @click="showSettingsDialog = true"></v-btn>
            <v-btn icon="mdi-arrow-left" title="Previous" @click="back" variant="text" density="compact"></v-btn>
            <v-btn icon title="Reload" @click="reload" variant="text" density="compact">
              <v-icon :class="{ 'rotate-once': isReloading }">mdi-reload</v-icon>
            </v-btn>
            <v-btn icon="mdi-arrow-right" title="Next" @click="next" variant="text" density="compact"></v-btn>
            <v-btn color="blue" prepend-icon="mdi-link-plus" class="ml-4" @click="openAddUrlDialog">
              Add URL
            </v-btn>
          </v-card-title>
          <v-divider></v-divider>

          <v-table density="compact" class="playlist-table">
            <thead>
              <tr>
                <th style="width: 50px;"></th>
                <th>URL</th>
                <th style="width: 100px;">Duration</th>
                <th style="width: 180px;"></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, index) in playlistItems"
                :key="item.uuid"
                @mouseenter="hoveredRow = item.uuid"
                @mouseleave="hoveredRow = null"
              >
                <td>
                  <v-icon v-if="hoveredRow === item.uuid">mdi-drag-horizontal</v-icon>
                  <v-icon v-else-if="item.media_type === 1">mdi-image</v-icon>
                  <v-icon v-else-if="item.media_type === 2">mdi-video</v-icon>
                  <v-icon v-else-if="item.media_type === 3">mdi-music</v-icon>
                  <v-icon v-else-if="item.url.startsWith('file:')">mdi-file</v-icon>
                  <v-icon v-else>mdi-link</v-icon>
                </td>
                <td style="max-width: 1px;">
                  <div v-if="item.name && item.url">
                    <div class="text-truncate font-weight-medium" style="font-size: 1.1em;">{{ item.name }}</div>
                    <div class="text-caption text-grey text-truncate">{{ item.url }}</div>
                  </div>
                  <div v-else class="text-truncate font-weight-medium" style="font-size: 1.1em;">
                    {{ item.url }}
                  </div>
                </td>
                <td>{{
                    item.duration === 0 ? 'Forever' : (() => {
                      let [h, m, s] = new Date(item.duration * 1000).toISOString().slice(11, 19).split(':');
                      if (h > 0) h += ' h'; else h = '';
                      if (m > 0) m += ' min'; else m = '';
                      return `${h} ${m} ${s} sec`;
                    })()
                  }}</td>
                <td class="d-flex align-center justify-end">
                  <v-switch v-model="item.enabled" hide-details color="primary" density="compact" class="mr-10"
                        @click="sendCommand('Scheduler/edit', {uuid: item.uuid, enabled: !item.enabled})"></v-switch>
                  <v-btn icon="mdi-delete" color="red" variant="text" size="small"
                        @click="sendCommand('Scheduler/delete', {uuid: item.uuid})"></v-btn>
                  <v-btn icon="mdi-pencil" color="yellow" variant="text" size="small" @click="openEditDialog(item)"></v-btn>
                  <v-btn icon="mdi-login" variant="text" size="small"
                        @click="sendCommand('Scheduler/goto', {'index': index})"></v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
          <v-divider class="mb-2"></v-divider>
        </v-card>
      </v-col>
    </v-row>

    <!-- Settings Dialog -->
    <v-dialog v-model="showSettingsDialog" max-width="400px">
      <v-card class="pa-2 pb-0">
        <v-card-title>Default Asset Duration</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="defaultDuration"
            label="Default duration"
            type="number"
            suffix="seconds"
            variant="outlined"
            :rules="[v => v > 0 || 'Duration must be positive']"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="showSettingsDialog = false">Annulla</v-btn>
          <v-btn color="primary" @click="saveSettings">Salva</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Add URL Dialog -->
    <v-dialog v-model="showAddUrlDialog" max-width="600px">
      <v-card>
        <v-card-title>{{ isEditing ? 'Edit Item' : 'Add URL' }}</v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="12">
              <v-text-field
                v-model="newUrlItem.name"
                label="Name"
                variant="outlined"
                density="compact"
              ></v-text-field>
            </v-col>
            <v-col cols="12">
              <v-text-field
                v-model="newUrlItem.url"
                label="URL"
                variant="outlined"
                density="compact"
                :rules="[v => !!v || 'URL is required']"
              ></v-text-field>
            </v-col>
            <v-col cols="12">
              <div class="d-flex align-center">
                <v-text-field
                  v-model.number="newUrlItem.duration"
                  label="Duration"
                  type="number"
                  suffix="seconds"
                  variant="outlined"
                  density="compact"
                  :rules="[v => v >= 0 || 'Duration must be positive']"
                  :disabled="isIndefinite"
                  hide-details="auto"
                  class="flex-grow-1"
                ></v-text-field>
                <v-checkbox
                  v-model="isIndefinite"
                  label="Display indefinitely"
                  hide-details
                  class="ml-2"
                  density="compact"
                ></v-checkbox>
              </div>
            </v-col>
            <v-col cols="12">
              <div class="text-subtitle-2 mb-2">Object fit (only for images and videos)</div>
              <div class="d-flex justify-space-between">
                <div class="d-flex flex-column align-center">
                  <input type="radio" class="btn-check" name="modal_fit_radio" id="modal_fit_contain" value="contain" autocomplete="off" v-model="newUrlItem.objectFit">
                  <label class="btn pa-0" for="modal_fit_contain">
                    <img class="border rounded" :style="{ backgroundColor: !newUrlItem.autoBackgroundColor ? newUrlItem.backgroundColor : 'black', objectFit: 'contain' }" width="136" height="76" src="data:image/svg+xml,%3Csvg%20style='font-size:%202rem;%20font-family:system-ui,-apple-system,%22Segoe%20UI%22,Roboto,%22Helvetica%20Neue%22,%22Noto%20Sans%22,%22Liberation%20Sans%22,Arial,sans-serif,%22Apple%20Color%20Emoji%22,%22Segoe%20UI%20Emoji%22,%22Segoe%20UI%20Symbol%22,%22Noto%20Color%20Emoji%22;%20-webkit-user-select:%20none;%20-moz-user-select:%20none;%20user-select:%20none;%20text-anchor:%20middle;'%20width='200'%20height='200'%20xmlns='http://www.w3.org/2000/svg'%3E%3Ctitle%3EPlaceholder%3C/title%3E%3Crect%20width='100%25'%20height='100%25'%20fill='%23555555'%3E%3C/rect%3E%3Ccircle%20cx='100'%20cy='100'%20r='100'%20fill='%23dee2e6'%3E%3C/circle%3E%3Ctext%20x='50%25'%20y='50%25'%20fill='%23868e96'%20dy='.3em'%3EContain%3C/text%3E%3C/svg%3E">
                  </label>
                </div>
                <div class="d-flex flex-column align-center">
                  <input type="radio" class="btn-check" name="modal_fit_radio" id="modal_fit_cover" value="cover" autocomplete="off" v-model="newUrlItem.objectFit">
                  <label class="btn pa-0" for="modal_fit_cover">
                    <img class="border rounded" style="object-fit: cover;" width="136" height="76" src="data:image/svg+xml,%3Csvg%20style='font-size:%202rem;%20font-family:system-ui,-apple-system,%22Segoe%20UI%22,Roboto,%22Helvetica%20Neue%22,%22Noto%20Sans%22,%22Liberation%20Sans%22,Arial,sans-serif,%22Apple%20Color%20Emoji%22,%22Segoe%20UI%20Emoji%22,%22Segoe%20UI%20Symbol%22,%22Noto%20Color%20Emoji%22;%20-webkit-user-select:%20none;%20-moz-user-select:%20none;%20user-select:%20none;%20text-anchor:%20middle;'%20width='200'%20height='200'%20xmlns='http://www.w3.org/2000/svg'%3E%3Ctitle%3EPlaceholder%3C/title%3E%3Crect%20width='100%25'%20height='100%25'%20fill='%23555555'%3E%3C/rect%3E%3Ccircle%20cx='100'%20cy='100'%20r='100'%20fill='%23dee2e6'%3E%3C/circle%3E%3Ctext%20x='50%25'%20y='50%25'%20fill='%23868e96'%20dy='.3em'%3ECover%3C/text%3E%3C/svg%3E">
                  </label>
                </div>
                <div class="d-flex flex-column align-center">
                  <input type="radio" class="btn-check" name="modal_fit_radio" id="modal_fit_fill" value="fill" autocomplete="off" v-model="newUrlItem.objectFit">
                  <label class="btn pa-0" for="modal_fit_fill">
                    <img class="border rounded" style="object-fit: fill;" width="136" height="76" src="data:image/svg+xml,%3Csvg%20style='font-size:%202rem;%20font-family:system-ui,-apple-system,%22Segoe%20UI%22,Roboto,%22Helvetica%20Neue%22,%22Noto%20Sans%22,%22Liberation%20Sans%22,Arial,sans-serif,%22Apple%20Color%20Emoji%22,%22Segoe%20UI%20Emoji%22,%22Segoe%20UI%20Symbol%22,%22Noto%20Color%20Emoji%22;%20-webkit-user-select:%20none;%20-moz-user-select:%20none;%20user-select:%20none;%20text-anchor:%20middle;'%20width='200'%20height='200'%20xmlns='http://www.w3.org/2000/svg'%3E%3Ctitle%3EPlaceholder%3C/title%3E%3Crect%20width='100%25'%20height='100%25'%20fill='%23555555'%3E%3C/rect%3E%3Ccircle%20cx='100'%20cy='100'%20r='100'%20fill='%23dee2e6'%3E%3C/circle%3E%3Ctext%20x='50%25'%20y='50%25'%20fill='%23868e96'%20dy='.3em'%3EFill%3C/text%3E%3C/svg%3E">
                  </label>
                </div>
              </div>
            </v-col>
            <v-col cols="12">
              <div class="d-flex align-center">
                <v-menu :close-on-content-click="false" location="bottom start">
                  <template v-slot:activator="{ props }">
                    <v-text-field
                      v-model="newUrlItem.backgroundColor"
                      label="Background color (only for images and videos)"
                      variant="outlined"
                      density="compact"
                      :disabled="newUrlItem.autoBackgroundColor"
                      class="flex-grow-1"
                      v-bind="props"
                      readonly
                    >
                      <template v-slot:prepend-inner>
                        <div
                          :style="{
                            backgroundColor: newUrlItem.backgroundColor,
                            width: '24px',
                            height: '24px',
                            border: '1px solid #ccc',
                            borderRadius: '4px'
                          }"
                        ></div>
                      </template>
                    </v-text-field>
                  </template>
                  <v-color-picker
                    v-model="newUrlItem.backgroundColor"
                    mode="hex"
                    :modes="['hex', 'rgb', 'hsl']"
                  ></v-color-picker>
                </v-menu>
                <v-checkbox
                  v-model="newUrlItem.autoBackgroundColor"
                  label="Auto (images only)"
                  hide-details
                  class="ml-4 mb-6"
                ></v-checkbox>
              </div>
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="newUrlItem.enableDate"
                label="Enable date"
                type="datetime-local"
                variant="outlined"
                density="compact"
              ></v-text-field>
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="newUrlItem.disableDate"
                label="Disable date"
                type="datetime-local"
                variant="outlined"
                density="compact"
              ></v-text-field>
            </v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="showAddUrlDialog = false">Annulla</v-btn>
          <v-btn color="primary" @click="saveUrlItem">Salva</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script setup>
import {ref, computed} from 'vue';

// Dati di esempio per i file
const mediaFiles = ref({});
const disk_used = ref('')
const disk_total = ref('')

const selectedFiles = ref([]);
const fileInput = ref(null);

// Dati di esempio per la playlist
const playlistItems = ref([]);

const hoveredRow = ref(null);
const isReloading = ref(false);
const showSettingsDialog = ref(false);
const defaultDuration = ref(30);

// Add URL Dialog State
const showAddUrlDialog = ref(false);
const isEditing = ref(false);
const newUrlItem = ref({
  uuid: null,
  name: '',
  url: '',
  duration: 30,
  objectFit: 'contain',
  backgroundColor: '#000000',
  autoBackgroundColor: true,
  enableDate: null,
  disableDate: null
});

const previousDuration = ref(30);

const isIndefinite = computed({
  get: () => newUrlItem.value.duration === 0,
  set: (val) => {
    if (val) {
      if (newUrlItem.value.duration > 0) previousDuration.value = newUrlItem.value.duration;
      newUrlItem.value.duration = 0;
    } else {
      newUrlItem.value.duration = previousDuration.value > 0 ? previousDuration.value : 30;
    }
  }
});

const sendCommand = window.sendCommand;

setInitCommands("Scheduler/asset","Scheduler/current","Scheduler/file","Settings/default_duration")

onWSMessage = (data) => {
  switch (data.target) {
    case "Scheduler/asset":
      playlistItems.value = data.items;
      break;
    case "Scheduler/current":
      break;
    case "Scheduler/file":
      mediaFiles.value = data.files;
      disk_used.value = data.disk_used;
      disk_total.value = data.disk_total;
      break;
    case "Settings/default_duration":
      defaultDuration.value = data.duration;
      break;
  }
}


const reload = () => {
  if (isReloading.value) return;
  isReloading.value = true;
  setTimeout(() => {
    isReloading.value = false;
  }, 500); // Durata dell'animazione
};

const back = () => {sendCommand('Scheduler/back')};
const next = () => {sendCommand('Scheduler/next')};

const triggerFileUpload = () => fileInput.value.click();

const handleFileUpload = async (event) => {
  const files = event.target.files;
  if (!files) return;

  for (const file of files) {
    const formData = new FormData();
    formData.append('files', file);

    try {
      const response = await fetch(`https://${location.host.split(':')[0]}/api/scheduler/upload`, {
        method: 'POST',
        body: formData,
        credentials: "include"
      });

      if (!response.ok || response.status !== 201) {
        console.error(`Failed to upload ${file.name}: ${response.statusText}`);
        // Optionally handle error (e.g., show a notification)
      } else {
        console.log(`Uploaded ${file.name} successfully`);
        // Optionally refresh the file list or handle success
      }
    } catch (error) {
      console.error(`Error uploading ${file.name}:`, error);
    }
  }

  // Clear the input so the same file can be selected again if needed
  event.target.value = '';
};

const toggleSelection = (filename) => {
  const index = selectedFiles.value.indexOf(filename);
  if (index > -1) {
    selectedFiles.value.splice(index, 1);
  } else {
    selectedFiles.value.push(filename);
  }
};

const selectAll = () => {
  selectedFiles.value = Object.keys(mediaFiles.value);
};

const deselectAll = () => {
  selectedFiles.value = [];
};

const addSelected = () => {
  sendCommand('Scheduler/add_file', {items: [... selectedFiles.value]});
  deselectAll();
}

const deleteSelected = () => {
  sendCommand('Scheduler/delete_file', {files: [... selectedFiles.value]});
  deselectAll();
}

const downloadSelected = () => {
  /*for (const filename of selectedFiles.value){
    let link = document.createElement("a");
    link.href = 'https://localhost/uploaded/' + filename;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }*/
  deselectAll();
  alert("NOT IMPLEMENTED YET!")
}

const saveSettings = () => {
  // Qui andrà la logica per salvare le impostazioni
  sendCommand('Settings/default_duration', {duration: defaultDuration.value});
  showSettingsDialog.value = false;
};

const openAddUrlDialog = () => {
  isEditing.value = false;
  newUrlItem.value = {
    uuid: null,
    name: '',
    url: '',
    duration: defaultDuration.value,
    objectFit: 'contain',
    backgroundColor: null,
    autoBackgroundColor: true,
    enableDate: null,
    disableDate: null
  };
  previousDuration.value = defaultDuration.value;
  showAddUrlDialog.value = true;
};

const openEditDialog = (item) => {
  isEditing.value = true;

  // Map fit value to string
  let fitValue = 'contain';
  if (item.fit === 1) fitValue = 'cover';
  else if (item.fit === 2) fitValue = 'fill';

  newUrlItem.value = {
    uuid: item.uuid,
    name: item.name || '',
    url: item.url || '',
    duration: item.duration,
    objectFit: fitValue,
    backgroundColor: item.bg_color || '#000000',
    autoBackgroundColor: item.bg_color === null,
    enableDate: item.ena_date,
    disableDate: item.dis_date
  };

  if (item.duration > 0) {
    previousDuration.value = item.duration;
  } else {
    previousDuration.value = defaultDuration.value;
  }

  showAddUrlDialog.value = true;
};

const saveUrlItem = () => {
  if (!newUrlItem.value.url) return; // Basic validation

  // Map fit string back to integer
  let fitInt = 0;
  if (newUrlItem.value.objectFit === 'cover') fitInt = 1;
  else if (newUrlItem.value.objectFit === 'fill') fitInt = 2;

  // Prepare payload
  const payload = {
    uuid: newUrlItem.value.uuid,
    name: newUrlItem.value.name,
    url: newUrlItem.value.url,
    duration: newUrlItem.value.duration,
    fit: fitInt,
    bg_color: newUrlItem.value.autoBackgroundColor ? null : newUrlItem.value.backgroundColor,
    ena_date: newUrlItem.value.enableDate,
    dis_date: newUrlItem.value.disableDate
  };

  if (isEditing.value) {
    sendCommand('Scheduler/edit', payload);
  } else {
    sendCommand('Scheduler/add_url', {items: [payload]});
  }

  showAddUrlDialog.value = false;
};
</script>

<style scoped>
.files-list :deep(.v-list-item) {
  padding-inline-start: 0 !important;
}

.selected-title-bar {
  background-color: rgba(41, 98, 255, 0.15);
}

.playlist-table {
  table-layout: fixed;
  width: 100%;
}

.playlist-table :deep(.v-table__wrapper) {
  overflow: visible;
}

.truncate-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 1px;
}

.rotate-once {
  animation: rotate-once 0.5s linear;
}

@keyframes rotate-once {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* Stili per i radio button personalizzati */
.btn-check {
  position: absolute;
  clip: rect(0, 0, 0, 0);
  pointer-events: none;
}

.btn-check:checked + .btn img {
  border-color: #0d6efd !important;
  border-width: 3px !important;
}
</style>
