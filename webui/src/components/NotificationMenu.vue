<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom end">
    <template v-slot:activator="{ props }">
      <v-btn v-bind="props" icon title="Notifications" class="notification-bell">
        <v-badge :model-value="unread.length > 0" :content="unread.length > 99 ? '99+' : unread.length"
                 :color="worstUnread" class="notification-badge">
          <v-icon>{{ unread.length ? 'mdi-bell' : 'mdi-bell-outline' }}</v-icon>
        </v-badge>
      </v-btn>
    </template>

    <v-card min-width="320" max-width="420" class="notification-menu">
      <v-card-title class="d-flex align-center text-subtitle-1 py-2">
        Notifications
        <v-spacer></v-spacer>
        <v-btn v-if="items.some(n => !n.persistent)" size="small" variant="text" class="notification-clear"
               @click="api.Notifications.dismiss_all()">Clear all</v-btn>
      </v-card-title>
      <v-divider></v-divider>

      <div v-if="!items.length" class="pa-4 text-medium-emphasis notification-empty">No notifications</div>
      <v-list v-else lines="three" density="compact" class="py-0" style="max-height: 60vh; overflow-y: auto">
        <v-list-item v-for="n in items" :key="n.id" class="notification-item" :class="{unread: !n.read}"
                     :data-level="n.level" :data-title="n.title">
          <template v-slot:prepend>
            <v-icon :color="levelOf(n).color" class="mr-3">{{ levelOf(n).icon }}</v-icon>
          </template>
          <v-list-item-title class="text-wrap" :class="{'font-weight-bold': !n.read}">{{ n.title }}</v-list-item-title>
          <v-list-item-subtitle v-if="n.text" class="text-wrap notification-text">{{ n.text }}</v-list-item-subtitle>
          <v-progress-linear v-if="n.progress != null" class="my-1 notification-progress" height="8" rounded color="primary"
                             :indeterminate="n.progress < 0" :model-value="Math.max(n.progress, 0)"></v-progress-linear>
          <div class="text-caption text-medium-emphasis">
            <span v-if="n.progress != null && n.progress >= 0">{{ Math.round(n.progress) }}% · </span>{{ ago(n.updated) }}
            <a v-if="n.link" href="#" class="ml-2 notification-link" @click.prevent="follow(n.link)">Open</a>
          </div>
          <template v-slot:append v-if="!n.persistent">
            <v-btn icon size="small" variant="text" title="Dismiss" class="notification-dismiss"
                   @click="api.Notifications.dismiss({ids: [n.id]})">
              <v-icon>mdi-close</v-icon>
            </v-btn>
          </template>
        </v-list-item>
      </v-list>
    </v-card>
  </v-menu>
</template>

<script setup>
/**
 * The bell of the navbar: what the node wants its users to know (Notifications/list, pushed whenever it changes).
 * Everything shown is plain text, rendered escaped; the server already filtered what this user may see.
 */
import {computed, onUnmounted, ref, watch} from 'vue'
import {useRouter} from 'vue-router'
import {api} from '@/api'

const router = useRouter()
const items = api.Notifications.list.use({pick: 'notifications', initial: []})
const open = ref(false)
const now = ref(Date.now() / 1000)
let timer = null

const LEVELS = {
  info: {icon: 'mdi-information', color: 'info'},
  warning: {icon: 'mdi-alert', color: 'warning'},
  error: {icon: 'mdi-alert-circle', color: 'error'},
}
const levelOf = (n) => LEVELS[n.level] ?? LEVELS.info

const unread = computed(() => items.value.filter(n => !n.read))
const worstUnread = computed(() => {
  const levels = unread.value.map(n => n.level)
  return levels.includes('error') ? 'error' : levels.includes('warning') ? 'warning' : 'primary'
})

// What was on screen while the menu was open counts as read once it closes
watch(open, (isOpen) => {
  if (isOpen) {
    now.value = Date.now() / 1000
    // the times are relative: keep them honest while somebody is looking, no need otherwise
    timer = setInterval(() => { now.value = Date.now() / 1000 }, 30000)
  } else {
    clearInterval(timer)
    if (unread.value.length) api.Notifications.mark_read({ids: unread.value.map(n => n.id)})
  }
})
onUnmounted(() => clearInterval(timer))

function ago(epoch) {
  const seconds = Math.max(0, Math.round(now.value - epoch))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.floor(hours / 24)} d ago`
}

function follow(link) {
  // The server only accepts a path of this web UI, and so does this: an address is never followed
  if (typeof link === 'string' && link.startsWith('/') && !link.startsWith('//')) {
    open.value = false
    router.push(link)
  }
}
</script>
