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
                    <td>{{ item.old_version }}</td>
                    <td>{{ item.new_version }}</td>
                  </tr>
                </tbody>
              </v-table>
            </v-card>
          </v-menu>
        </v-card-subtitle>
        <v-spacer></v-spacer>
        <v-btn variant="text" color="primary" :prepend-icon="status === 'update' ? 'mdi-loading mdi-spin' : 'mdi-refresh'" @click="sendCommand('Settings/Update/update')"
                :disabled="status !== null">Check updates</v-btn>
        <v-btn variant="text" color="red" :prepend-icon="status === 'upgrade' ? 'mdi-loading mdi-spin' : 'mdi-progress-upload'" @click="sendCommand('Settings/Update/upgrade')"
               :disabled="status !== null || packages.length === 0">Apply updates</v-btn>
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
            <div v-for="(log, index) in logs" :key="index" :class="{ 'text-red': !log[0] }">{{ log[1] }}</div>
            <div v-if="returncode===0" class="text-green">Process terminated with code {{ returncode }}</div>
            <div v-else-if="returncode!==null" class="text-red">Process failed with code {{ returncode }}</div>
          </div>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import {computed, ref} from 'vue'

const status = ref(null);
const returncode = ref(0);

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

const logs = ref([])


const sendCommand = window.sendCommand;

window.setInitCommands("Settings/Update/list", "Settings/Update/status")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Update/list":
      packages.value = data.updates;
      break;
    case "Settings/Update/status":
      status.value = data.status;
      returncode.value = data.returncode;
      logs.value = data.log;
      break;
    case 'Settings/Update/start':
      logs.value = [];
      break;
    case 'Settings/Update/progress':
      logs.value.push([data.is_stdout, data.data]);
      break;
    case 'Settings/Update/end':
      status.value = null;
      returncode.value = data.returncode;
      break;
  }
}

</script>

<style scoped>
.log-container {
  font-family: monospace;
}
:deep(.v-btn--disabled) {
  opacity: 0.6;
}
</style>
