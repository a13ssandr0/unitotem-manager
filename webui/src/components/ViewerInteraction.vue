<!--
  How a window answers the people who touch it: the on-screen keyboard, the interactive screen (a home page, with the
  playlist as a screensaver after an idle time) and the language its web pages are told to show.
-->
<template>
  <div>
    <div class="text-subtitle-2 mb-2">On-screen keyboard</div>
    <ViewerKeyboard :win="win" class="mb-6"/>

    <div class="text-subtitle-2 mb-2">Interactive screen</div>
    <div class="d-flex flex-wrap ga-3 align-center mb-6">
      <v-switch :model-value="saverOn" label="Enabled" density="compact" hide-details color="primary" :disabled="locked"
                @update:model-value="v => setScreensaver({enabled: !!v})"></v-switch>
      <template v-if="saverOn">
        <v-text-field :model-value="win.screensaver.home_url" label="Home page" placeholder="https://"
                      density="compact" variant="outlined" hide-details min-width="260" :disabled="locked"
                      @change="e => setScreensaver({home_url: e.target.value}, true)"></v-text-field>
        <v-text-field :model-value="win.screensaver.idle_timeout" label="Idle time (s)" type="number" min="5"
                      max="86400" density="compact" variant="outlined" hide-details min-width="160" :disabled="locked"
                      @change="e => setScreensaver({idle_timeout: Number(e.target.value)}, true)"></v-text-field>
      </template>
    </div>

    <div class="text-subtitle-2 mb-2">Page language</div>
    <v-autocomplete :items="contentLanguages" :model-value="win.language || ''" label="Language"
                    density="compact" variant="outlined" hide-details class="keyboard-field" :disabled="!canSetLanguage"
                    @update:model-value="v => setLanguage(v ?? '')"></v-autocomplete>
  </div>
</template>

<script setup>
// The server refuses an enabled screensaver without a home page: the switch then only opens the fields (see screensaverChange)
import {computed, ref} from 'vue'
import {api, canCall} from '@/api'
import keyboardIndex from '@/keyboard/index.json'
import {contentLanguageItems, keyboardLanguageItems, screensaverChange} from './ViewerCore.js'

const props = defineProps({
  win: {type: Object, required: true},
})

const currentUser = api.Settings.Security.current_user.use()
const locked = computed(() => !canCall('Viewers/set_screensaver', currentUser.value))
const canSetLanguage = computed(() => canCall('Viewers/set_language', currentUser.value))
// the same languages as the keyboard's (the pages are told a language, the keyboard is one)
const contentLanguages = contentLanguageItems(keyboardLanguageItems(keyboardIndex))

// the switch as the user left it, per window, until the home page is given
const saverDraft = ref({})
const saverOn = computed(() => props.win.screensaver.enabled || !!saverDraft.value[props.win.key])

function setScreensaver(patch, resync = false) {
  const {draft, request} = screensaverChange(props.win, patch, !!saverDraft.value[props.win.key])
  saverDraft.value = {...saverDraft.value, [props.win.key]: draft}
  if (!request) return
  const ids = {viewer_id: props.win.viewer_id, window_id: props.win.window_id}
  api.Viewers.set_screensaver({...ids, ...request})
  if (resync) setTimeout(() => api.Viewers.screensaver(ids), 500)
}

function setLanguage(language) {
  api.Viewers.set_language({viewer_id: props.win.viewer_id, window_id: props.win.window_id, language})
}
</script>

<style scoped>
.keyboard-field { width: 220px; max-width: 220px; }
</style>
