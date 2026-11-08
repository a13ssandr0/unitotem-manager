<!--
  The on-screen keyboard of one window (touch screens): on or off, where its button is, and its layout (language and
  arrangement). Administrators only: the controls are disabled for the others, the server refuses them too.
-->
<template>
  <div class="d-flex flex-wrap ga-3 align-center">
    <v-switch :model-value="win.keyboard.enabled" label="Enabled" density="compact" hide-details color="primary" :disabled="locked"
              @update:model-value="v => setKeyboard({enabled: !!v})"></v-switch>
    <v-select :items="KEYBOARD_POSITIONS" :model-value="win.keyboard.button_position" label="Button position"
              density="compact" variant="outlined" hide-details class="keyboard-field" :disabled="locked || !win.keyboard.enabled"
              @update:model-value="v => setKeyboard({button_position: v})"></v-select>
    <v-autocomplete :items="keyboardLanguages" :model-value="language" label="Keyboard language"
                    density="compact" variant="outlined" hide-details class="keyboard-field"
                    :disabled="locked || !win.keyboard.enabled"
                    @update:model-value="v => v && setKeyboard({layout: keyboardLayout(keyboardIndex, v, undefined)})">
      <template #item="{props, item}">
        <v-list-item v-bind="props" :title="item.raw.title" :subtitle="item.raw.value"></v-list-item>
      </template>
    </v-autocomplete>
    <v-select :items="variantItems(keyboardIndex, language)" :model-value="keyboardVariant(win, keyboardIndex)" label="Key arrangement"
              density="compact" variant="outlined" hide-details class="keyboard-field"
              :disabled="locked || !win.keyboard.enabled || languageVariants(keyboardIndex, language).length < 2"
              @update:model-value="v => setKeyboard({layout: keyboardLayout(keyboardIndex, language, v)})"></v-select>
  </div>
</template>

<script setup>
// fixed width on purpose: the column must not grow or shrink with the name that is selected (.keyboard-field)
import {computed} from 'vue'
import {api, canCall} from '@/api'
import keyboardIndex from '@/keyboard/index.json'
import {
  KEYBOARD_POSITIONS, keyboardLanguage, keyboardLanguageItems, keyboardLayout, keyboardVariant, languageVariants, variantItems,
} from './ViewerCore.js'

const props = defineProps({
  win: {type: Object, required: true},
})

const currentUser = api.Settings.Security.current_user.use()
const locked = computed(() => !canCall('Viewers/set_keyboard', currentUser.value))

// The languages of the keyboard data, by name; the layout of the window is one language and one of its arrangements
const keyboardLanguages = keyboardLanguageItems(keyboardIndex)
const language = computed(() => keyboardLanguage(props.win))

// the whole keyboard setting is sent with each change: the values the window has, with the one that changed
function setKeyboard(patch) {
  api.Viewers.set_keyboard({viewer_id: props.win.viewer_id, window_id: props.win.window_id, ...props.win.keyboard, ...patch})
}
</script>

<style scoped>
.keyboard-field { width: 220px; max-width: 220px; }
</style>
