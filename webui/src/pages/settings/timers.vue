<template>
  <div class="d-flex flex-column align-center">
    <v-card
      class="ma-4"
      max-width="1000"
      width="100%"
      :title="String($route.name)"
    >
      <v-data-table
        :headers="headers"
        :items="timers"
        class="elevation-1"
      >
        <template v-slot:item.command="{ item }">
          {{ getActionTitle(item.command) }}
        </template>
        <template v-slot:item.month="{ item }">
          {{ getMonthTitle(item.month) }}
        </template>
        <template v-slot:item.dayOfWeek="{ item }">
          {{ getDayOfWeekTitle(item.dayOfWeek) }}
        </template>
        <template v-slot:item.actions="{ item }">
          <v-btn variant="text" icon="mdi-pencil" color="yellow" class="mr-2" @click="editItem(item)" aria-label="Edit"></v-btn>
          <v-btn variant="text" icon="mdi-delete" color="red" @click="deleteItem(item)" aria-label="Delete"></v-btn>
        </template>
      </v-data-table>
    </v-card>

    <v-dialog v-model="dialog" max-width="500px">
      <v-card>
        <v-card-title>
          <span class="text-h5">{{ formTitle }}</span>
        </v-card-title>

        <v-card-text>
          <v-container>
            <v-row>
              <v-col cols="12">
                <v-select
                  v-model="editedItem.command"
                  :items="commands"
                  item-title="title"
                  item-value="value"
                  label="Action"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="12" sm="6">
                <v-select
                  v-model="editedItem.hour"
                  :items="hours"
                  label="Hour"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="12" sm="6">
                <v-select
                  v-model="editedItem.minute"
                  :items="minutes"
                  label="Minute"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="12">
                <v-select
                  v-model="editedItem.dayOfMonth"
                  :items="daysOfMonth"
                  label="Day of the month"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="12">
                <v-select
                  v-model="editedItem.month"
                  :items="months"
                  item-title="title"
                  item-value="value"
                  label="Month"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="12">
                <v-select
                  v-model="editedItem.dayOfWeek"
                  :items="daysOfWeek"
                  item-title="title"
                  item-value="value"
                  label="Day of the week"
                  variant="outlined"
                ></v-select>
              </v-col>
            </v-row>
          </v-container>
        </v-card-text>

        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="blue-darken-1" variant="text" @click="close">Cancel</v-btn>
          <v-btn color="blue-darken-1" variant="text" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-btn
      class="ma-4"
      position="fixed"
      location="bottom right"
      icon="mdi-plus"
      color="primary"
      @click="dialog = true"
      aria-label="Add user"
    ></v-btn>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';

const headers = ref([
  { title: 'Action', key: 'command' },
  { title: 'Hour', key: 'hour' },
  { title: 'Minute', key: 'minute' },
  { title: 'Day of the month', key: 'dayOfMonth' },
  { title: 'Month', key: 'month' },
  { title: 'Day of the week', key: 'dayOfWeek' },
  { title: '', key: 'actions', sortable: false },
]);

const commands = [
  { title: 'Shutdown', value: 'pwr' },
  { title: 'Reboot', value: 'reb' },
];

const hours = ['*', ...Array.from({ length: 24 }, (_, i) => i)];
const minutes = ['*', ...Array.from({ length: 60 }, (_, i) => i)];
const daysOfMonth = ['*', ...Array.from({ length: 31 }, (_, i) => i + 1)];
const months = [
  { title: '*', value: '*' },
  { title: 'January', value: 1 },
  { title: 'February', value: 2 },
  { title: 'March', value: 3 },
  { title: 'April', value: 4 },
  { title: 'May', value: 5 },
  { title: 'June', value: 6 },
  { title: 'July', value: 7 },
  { title: 'August', value: 8 },
  { title: 'September', value: 9 },
  { title: 'October', value: 10 },
  { title: 'November', value: 11 },
  { title: 'December', value: 12 },
];
const daysOfWeek = [
  { title: '*', value: '*' },
  { title: 'Monday', value: 1 },
  { title: 'Tuesday', value: 2 },
  { title: 'Wednesday', value: 3 },
  { title: 'Thursday', value: 4 },
  { title: 'Friday', value: 5 },
  { title: 'Saturday', value: 6 },
  { title: 'Sunday', value: 0 },
];

const defaultItem = {
  command: commands[0].value,
  hour: hours[0],
  minute: minutes[0],
  dayOfMonth: daysOfMonth[0],
  month: months[0].value,
  dayOfWeek: daysOfWeek[0].value,
  enabled: true,
};

const timers = ref([
  {
    command: 'reb',
    hour: '0',
    minute: '0',
    dayOfMonth: '*',
    month: '*',
    dayOfWeek: '*',
    enabled: true,
  },
]);

const dialog = ref(false);
const editedIndex = ref(-1);
const editedItem = ref({ ...defaultItem });

const formTitle = computed(() => (editedIndex.value === -1 ? 'New Timer' : 'Edit Timer'));

function getActionTitle(value) {
  const item = commands.find(i => i.value === value);
  return item ? item.title : value;
}

function getMonthTitle(value) {
  const item = months.find(i => i.value === value);
  return item ? item.title : value;
}

function getDayOfWeekTitle(value) {
  const item = daysOfWeek.find(i => i.value === value);
  return item ? item.title : value;
}

function editItem(item) {
  editedIndex.value = timers.value.indexOf(item);
  editedItem.value = Object.assign({}, item);
  dialog.value = true;
}

function deleteItem(item) {
  const index = timers.value.indexOf(item);
  confirm('Are you sure you want to delete this item?') && timers.value.splice(index, 1);
}

function close() {
  dialog.value = false;
  editedItem.value = { ...defaultItem };
  editedIndex.value = -1;
}

function save() {
  const item = { ...editedItem.value };
  delete item.enabled;
  if (editedIndex.value > -1) {
    sendCommand('Settings/Cron/editJob', item);
  } else {
    delete item.uuid;
    sendCommand('Settings/Cron/addJob', item);
  }
  close();
}

const sendCommand = window.sendCommand;
window.setInitCommands("Settings/Cron/getJobs")

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Cron/getJobs":
      timers.value = data.jobs;
      break;
  }
}

</script>


<style scoped>

</style>
