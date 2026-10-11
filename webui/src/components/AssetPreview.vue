<template>
  <FloatingWindow v-model="open" :title="title" icon="mdi-eye" :width="720" :height="480">
    <div class="preview-pane d-flex align-center justify-center" :style="{background: asset?.bg_color || '#000'}">
      <img v-if="kind === 'image'" :src="src" :style="{objectFit: fit}" class="preview-media" alt="">
      <video v-else-if="kind === 'video'" :src="src" controls autoplay loop class="preview-media" :style="{objectFit: fit}"></video>
      <audio v-else-if="kind === 'audio'" :src="src" controls autoplay loop></audio>
      <iframe v-else-if="kind === 'pdf'" :src="src" class="preview-media" title="PDF preview"></iframe>
      <template v-else>
        <iframe :src="src" class="preview-media" title="Web preview" referrerpolicy="no-referrer"></iframe>
        <div class="preview-note text-caption">
          Some sites refuse to be shown inside another page: if this stays blank, the site does not allow it.
        </div>
      </template>
    </div>
  </FloatingWindow>
</template>

<script setup>
/**
 * Preview of an asset in a floating window: what it looks like, played here in the browser (not on the screen).
 * `asset` is a playlist item; for a file in the uploads list pass {url: 'file:name', media_type: ...}.
 */
import {computed} from 'vue'
import FloatingWindow from '@/components/FloatingWindow.vue'

const open = defineModel({type: Boolean, default: false})
const props = defineProps({asset: Object})

const FITS = ['contain', 'cover', 'fill']
const url = computed(() => props.asset?.url ?? '')
const isFile = computed(() => url.value.startsWith('file:'))
const name = computed(() => url.value.replace(/^file:/, ''))
const kind = computed(() => {
  if (isFile.value && name.value.toLowerCase().endsWith('.pdf')) return 'pdf'
  switch (props.asset?.media_type) {
    case 1: return 'image'
    case 2: return 'video'
    case 3: return 'audio'
    default: return 'web'
  }
})
const src = computed(() => isFile.value ? '/uploaded/' + encodeURIComponent(name.value) : url.value)
const fit = computed(() => FITS[props.asset?.fit ?? 0] ?? 'contain')
const title = computed(() => props.asset?.name || name.value || 'Preview')
</script>

<style scoped>
.preview-pane {
  width: 100%;
  height: 100%;
  position: relative;
}
.preview-media {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
img.preview-media, video.preview-media {
  background: transparent;
}
.preview-note {
  position: absolute;
  left: 8px;
  bottom: 4px;
  color: #bbb;
  pointer-events: none;
}
</style>
