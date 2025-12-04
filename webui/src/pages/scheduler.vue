<template>
  <v-container fluid>
    <v-row>
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
              <v-btn icon="mdi-login" variant="tonal" color="blue" size="small" title="Add to Playlist"></v-btn>
              <v-btn icon="mdi-download" variant="text" size="small" title="Download"></v-btn>
              <v-btn icon="mdi-delete" color="red" variant="text" size="small" title="Delete"></v-btn>
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
              v-for="file in mediaFiles"
              :key="file.id"
              class="py-0"
              @click="toggleSelection(file.id)"
              :active="selectedFiles.includes(file.id)"
            >
              <template v-slot:prepend>
                <v-checkbox-btn :model-value="selectedFiles.includes(file.id)" hide-details></v-checkbox-btn>
              </template>
              <v-list-item-title>{{ file.name }}</v-list-item-title>
              <v-list-item-subtitle>{{ file.duration }} - {{ file.size }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- Colonna di Destra: Placeholder -->
      <v-col cols="12" md="8">
        <v-card>
          <v-card-title>
            Details
          </v-card-title>
          <v-divider></v-divider>
          <v-card-text>
            <p class="text-grey">Select a file to see its details here.</p>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref } from 'vue';

// Dati di esempio
const mediaFiles = ref([
  { id: 1, name: 'Nature_Video.mp4', duration: '00:02:30', size: '58 MB' },
  { id: 2, name: 'Corporate_Presentation.mp4', duration: '00:05:12', size: '120 MB' },
  { id: 3, name: 'Advertisement.mp4', duration: '00:00:30', size: '12 MB' },
  { id: 4, name: 'Tutorial.mp4', duration: '00:10:00', size: '250 MB' },
  { id: 5, name: 'Background_Music.mp3', duration: '00:03:45', size: '5 MB' },
]);

const selectedFiles = ref([]);
const fileInput = ref(null);

const triggerFileUpload = () => fileInput.value.click();

const handleFileUpload = (event) => {
  const files = event.target.files;
  if (!files) return;
  for (const file of files) {
    console.log('File selected:', file.name);
    const newFile = {
      id: mediaFiles.value.length + 1 + Math.random(), // Usa random per evitare ID duplicati
      name: file.name,
      duration: 'N/A',
      size: `${(file.size / 1024 / 1024).toFixed(2)} MB`,
    };
    mediaFiles.value.unshift(newFile);
  }
};

const toggleSelection = (fileId) => {
  const index = selectedFiles.value.indexOf(fileId);
  if (index > -1) {
    selectedFiles.value.splice(index, 1);
  } else {
    selectedFiles.value.push(fileId);
  }
};

const selectAll = () => {
  selectedFiles.value = mediaFiles.value.map(file => file.id);
};

const deselectAll = () => {
  selectedFiles.value = [];
};
</script>

<style scoped>
.files-list :deep(.v-list-item) {
  padding-inline-start: 8px !important;
}

.selected-title-bar {
  background-color: rgba(41, 98, 255, 0.15); /* Blu (#2962FF) con 15% di opacità */
}
</style>
