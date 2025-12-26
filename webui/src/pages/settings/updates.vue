<template>
  <div class="d-flex justify-center flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <div class="d-flex align-center">
        <v-card-subtitle>
          <span>{{ updates }}</span>
          <v-menu v-if="packages.length > 0" location="bottom">
            <template v-slot:activator="{ props }">
              <v-btn v-bind="props" variant="text" icon="mdi-information-outline"></v-btn>
            </template>
            <v-card>
              <v-table>
                <thead>
                  <tr>
                    <th class="text-left">Package</th>
                    <th class="text-left">Installed</th>
                    <th class="text-left">Available</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in packages" :key="item.name">
                    <td><strong>{{ item.name }}</strong></td>
                    <td>{{ item.installed }}</td>
                    <td>{{ item.available }}</td>
                  </tr>
                </tbody>
              </v-table>
            </v-card>
          </v-menu>
        </v-card-subtitle>
        <v-spacer></v-spacer>
        <v-btn variant="text" color="primary" prepend-icon="mdi-refresh">Check updates</v-btn>
        <v-btn
          variant="text"
          color="red"
          prepend-icon="mdi-progress-upload"
          :disabled="packages.length === 0"
        >Apply updates</v-btn>
      </div>
    </v-card>
    <v-card
      class="ma-4 mt-0"
      max-width="1000"
      width="100%"
      title="Logs"
    >
      <v-card-text>
        <div class="bg-black pa-4 rounded log-container">
          <div v-if="logs.length === 0">No logs available</div>
          <div v-else>
            <div
              v-for="(log, index) in logs"
              :key="index"
              :class="{ 'text-red': log.type === 'stderr' }"
            >
              {{ log.text }}
            </div>
          </div>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import {computed, ref} from 'vue'

const packages = ref([])

const updates = computed(() => {
  const count = packages.value.length
  if (count === 0) {
    return 'All packages are up to date'
  }
  if (count === 1) {
    return '1 update available'
  }
  return `${count} updates available`
})

const logs = ref([
  { type: 'stdout', text: 'Reading package lists... Done' },
  { type: 'stdout', text: 'Building dependency tree... Done' },
  { type: 'stderr', text: 'E: Could not open lock file /var/lib/dpkg/lock - open (13: Permission denied)' },
  { type: 'stdout', text: 'All packages are up to date.' },
])
</script>

<style scoped>
.log-container {
  font-family: monospace;
}
</style>
