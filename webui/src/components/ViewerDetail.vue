<!--
  Everything about one window, opened from its name in the Viewers matrix: its sound, how people interact with it, its
  maintenance, and its layout (area and panes, with the RSS ticker of the window for administrators).
  The window is read from the page's state on every render, so the dialog always shows what the server has.
-->
<template>
  <v-dialog :model-value="modelValue" max-width="960" scrollable :fullscreen="display.smAndDown.value"
            @update:model-value="v => emit('update:modelValue', v)">
    <v-card v-if="win">
      <v-card-title class="d-flex align-center ga-2 pt-4">
        <v-icon>mdi-monitor</v-icon>
        <span>{{ win.hostname }}</span>
        <span class="text-medium-emphasis">{{ win.screen_label }}</span>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)"></v-btn>
      </v-card-title>
      <v-card-text>
        <div class="d-flex flex-wrap align-center ga-2 text-caption text-medium-emphasis mb-2">
          <span>#{{ win.window_id }} {{ win.width }}x{{ win.height }}</span>
          <span>{{ win.ip }}</span>
          <v-chip v-for="(screen, i) in win.screens" :key="i" size="x-small" variant="outlined">
            {{ screen.name }} {{ screen.width }}×{{ screen.height }}
          </v-chip>
        </div>

        <h3 class="text-h6 mt-4 mb-3">Audio</h3>
        <v-select :items="outputItems" :model-value="win.audio_output ?? ''" label="Sound output" density="compact"
                  variant="outlined" hide-details min-width="240" :disabled="!canAudio || !outputs.length"
                  @update:model-value="v => setAudio(v || null)"></v-select>

        <v-divider class="my-6"></v-divider>
        <h3 class="text-h6 mb-3">Interaction</h3>
        <ViewerInteraction :win="win"/>

        <v-divider class="my-6"></v-divider>
        <h3 class="text-h6 mb-3">Maintenance</h3>
        <ViewerMaintenance :win="win"/>

        <v-divider class="my-6"></v-divider>
        <h3 class="text-h6 mb-3">Layout and panes</h3>
        <WindowLayout :win="win" :playlists="playlists" :can-edit="canConfigureLayout"/>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<script setup>
import {computed} from 'vue'
import {useDisplay} from 'vuetify'
import {api, canCall} from '@/api'
import WindowLayout from '@/components/WindowLayout.vue'
import ViewerInteraction from './ViewerInteraction.vue'
import ViewerMaintenance from './ViewerMaintenance.vue'

const props = defineProps({
  modelValue: {type: Boolean, default: false},
  win: {type: Object, default: null},                 // one window (see buildWindows in ViewerCore.js)
  playlists: {type: Array, default: () => []},
  outputs: {type: Array, default: () => []},          // the sound outputs of the window's host
})
const emit = defineEmits(['update:modelValue'])

const display = useDisplay()
const currentUser = api.Settings.Security.current_user.use()
const canAudio = computed(() => canCall('Viewers/set_audio_output', currentUser.value))
const canConfigureLayout = computed(() => canCall('Viewers/set_area', currentUser.value))

const outputItems = computed(() => [
  {title: 'Default', value: ''},
  ...props.outputs.map(o => ({title: o.description, value: o.description})),
])

function setAudio(device) {
  api.Viewers.set_audio_output({viewer_id: props.win.viewer_id, window_id: props.win.window_id, device})
}
</script>
