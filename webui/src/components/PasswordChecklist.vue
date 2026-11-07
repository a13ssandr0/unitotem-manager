<template>
  <v-list v-if="rows.length" role="list" aria-live="polite" density="compact" bg-color="transparent"
          class="pa-0 pw-checklist">
    <v-list-item v-for="row in rows" :key="row.id" role="listitem"
                 :class="['pw-row', row.ok ? 'pw-met' : 'pw-unmet']">
      <template #prepend>
        <v-icon class="pw-icon" :icon="row.ok ? 'mdi-check-circle' : 'mdi-circle-outline'"
                :color="row.ok ? 'success' : undefined" size="small"></v-icon>
      </template>
      <v-list-item-title class="text-body-2">
        {{ row.label }}<span class="pw-sr">: {{ row.ok ? 'met' : 'not met' }}</span>
      </v-list-item-title>
    </v-list-item>
  </v-list>
</template>

<script setup>
/**
 * Live checklist of the password rules: one row per rule of the server spec, met or not met as the person types.
 * The server still decides; this only shows the same rules (see ../password-rules.js).
 * Emits `valid` (boolean) at mount and every time the validity changes. Without a spec nothing is shown and the
 * password is reported as not valid.
 */
import {computed, watch} from 'vue'
import {evaluate, isValid} from '@/password-rules.js'

const props = defineProps({
  spec: {type: Object, default: null},
  password: {type: String, default: ''},
  username: {type: String, default: ''},
  // undefined: no confirmation row; a string: adds the 'Passwords match' row
  confirm: {type: String, default: undefined},
})

const emit = defineEmits(['valid'])

const hasSpec = computed(() => Array.isArray(props.spec?.rules))

const rows = computed(() => hasSpec.value ? evaluate(props.spec, props.password, props.username, props.confirm) : [])

const valid = computed(() => hasSpec.value && isValid(props.spec, props.password, props.username, props.confirm))

watch(valid, value => emit('valid', value), {immediate: true})
</script>

<style scoped>
.pw-checklist {
  background: transparent;
}
.pw-row {
  min-height: 28px;
  height: auto;
  padding-inline: 0;
  align-items: flex-start;
  overflow: visible;
}
.pw-row :deep(.v-list-item__content) {
  min-width: 0;
  overflow: visible;
}
.pw-row :deep(.v-list-item-title) {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  overflow-wrap: anywhere;
}
.pw-unmet .pw-icon {
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.6;
}
.pw-icon {
  transition: color 0.2s ease, transform 0.2s ease;
}
.pw-met .pw-icon {
  animation: pw-pop 0.2s ease-out;
}
@keyframes pw-pop {
  from { transform: scale(0.6); }
  to { transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  .pw-icon, .pw-met .pw-icon { transition: none; animation: none; }
}
.pw-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
