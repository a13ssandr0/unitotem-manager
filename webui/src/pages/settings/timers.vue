<template>
  <CardColumn>
    <div class="d-flex justify-center" style="width: 100%">
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
        no-data-text="No timers set"
      >
        <template v-slot:item.command="{ item }">
          {{ getActionTitle(item.command) }}<span v-if="item.output" class="text-medium-emphasis"> ({{ item.output }})</span>
        </template>
        <template v-slot:item.month="{ item }">
          {{ getMonthTitle(item.month) }}
        </template>
        <template v-slot:item.day_of_week="{ item }">
          {{ getDayOfWeekTitle(item.day_of_week) }}
        </template>
        <template v-slot:item.enabled="{ item }">
          <v-switch
            :model-value="item.enabled"
            color="primary"
            density="compact"
            hide-details
            :aria-label="item.enabled ? 'Disable timer' : 'Enable timer'"
            @update:model-value="setEnabled(item, $event)"
          ></v-switch>
        </template>
        <template v-slot:item.actions="{ item }">
          <v-btn variant="text" icon="mdi-pencil" color="yellow" class="mr-2" @click="editItem(item)" aria-label="Edit"></v-btn>
          <v-btn variant="text" icon="mdi-delete" color="red" @click="deleteItem(item)" aria-label="Delete"></v-btn>
        </template>
      </v-data-table>
    </v-card>
    </div>

    <v-dialog v-model="dialog" max-width="500px">
      <v-card>
        <v-card-title>
          <span class="text-h5">{{ formTitle }}</span>
        </v-card-title>

        <v-card-text>
          <v-container>
            <v-row>
              <v-col cols="12">
                <!-- A job whose command this version does not recognise keeps
                     its schedule editable, but its action must not be
                     rewritten into one of ours. -->
                <v-select
                  v-model="editedItem.command"
                  :items="commands"
                  item-title="title"
                  item-value="value"
                  label="Action"
                  variant="outlined"
                  :disabled="editedItem.known === false"
                  :hint="editedItem.known === false ? 'Set outside UniTotem: only the schedule can be changed' : undefined"
                  :persistent-hint="editedItem.known === false"
                ></v-select>
              </v-col>
              <v-col cols="12" sm="6" v-if="isScreenAction(editedItem.command)">
                <v-combobox v-model="editedItem.output" :items="outputNameList" label="Screen" variant="outlined"
                            hint="The name of the output, e.g. HDMI-1" persistent-hint></v-combobox>
              </v-col>
              <v-col cols="12" sm="6" v-if="isScreenAction(editedItem.command)">
                <v-select v-model="editedItem.mechanism" :items="mechanismItems" label="Switch with" variant="outlined"></v-select>
              </v-col>
              <!-- Comboboxes, not selects: cron slices are not limited to the
                   plain numbers offered here ('*/15', '1-5', '0,30' are all
                   valid and the backend now preserves them), and a select
                   would silently blank any job that uses one. -->
              <v-col cols="12" sm="6">
                <v-combobox
                  v-model="editedItem.hour"
                  :items="hours"
                  label="Hour"
                  variant="outlined"
                ></v-combobox>
              </v-col>
              <v-col cols="12" sm="6">
                <v-combobox
                  v-model="editedItem.minute"
                  :items="minutes"
                  label="Minute"
                  variant="outlined"
                ></v-combobox>
              </v-col>
              <v-col cols="12">
                <v-combobox
                  v-model="editedItem.day_of_month"
                  :items="daysOfMonth"
                  label="Day of the month"
                  variant="outlined"
                ></v-combobox>
              </v-col>
              <v-col cols="12">
                <v-combobox
                  v-model="editedItem.month"
                  :items="months"
                  item-title="title"
                  item-value="value"
                  :return-object="false"
                  label="Month"
                  variant="outlined"
                ></v-combobox>
              </v-col>
              <v-col cols="12">
                <v-combobox
                  v-model="editedItem.day_of_week"
                  :items="daysOfWeek"
                  item-title="title"
                  item-value="value"
                  :return-object="false"
                  label="Day of the week"
                  variant="outlined"
                ></v-combobox>
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
    <template #fab>
      <v-btn icon="mdi-plus" color="primary" size="large" elevation="6" @click="dialog = true" aria-label="Add timer"></v-btn>
    </template>
  </CardColumn>
</template>

<script setup>
import {api} from '@/api'
import CardColumn from '@/components/CardColumn.vue'
import { ref, computed } from 'vue';

const headers = ref([
  { title: 'Action', key: 'command' },
  { title: 'Hour', key: 'hour' },
  { title: 'Minute', key: 'minute' },
  { title: 'Day of the month', key: 'day_of_month' },
  { title: 'Month', key: 'month' },
  { title: 'Day of the week', key: 'day_of_week' },
  { title: 'Enabled', key: 'enabled', sortable: false },
  { title: '', key: 'actions', sortable: false },
]);

const commands = [
  { title: 'Shutdown', value: 'pwr' },
  { title: 'Reboot', value: 'reb' },
  { title: 'Switch screen on', value: 'scr_on' },
  { title: 'Switch screen off', value: 'scr_off' },
];
const isScreenAction = (value) => value === 'scr_on' || value === 'scr_off';
const mechanismItems = [
  { title: 'Automatic', value: 'auto' },
  { title: 'Display output', value: 'x' },
  { title: 'DDC/CI', value: 'ddc' },
  { title: 'CEC', value: 'cec' },
  { title: 'Wake-on-LAN', value: 'wol' },
  { title: 'Serial', value: 'serial' },
];
const outputNames = api.Settings.Display.Screens.screens.use({pick: 'screens', initial: []});
const outputNameList = computed(() => outputNames.value.map(s => s.name));

// Every option value is a STRING, because a cron field is a string: the
// backend now reports the raw slice ('*', '0', '*/15') instead of coercing it
// to an int, which used to throw away wildcards and ranges outright. Mixing
// the two here would break the === lookups below - a numeric 1 would not match
// the '1' that comes back - and the table would show bare numbers where it
// should show month and day names.
const hours = ['*', ...Array.from({ length: 24 }, (_, i) => String(i))];
const minutes = ['*', ...Array.from({ length: 60 }, (_, i) => String(i))];
const daysOfMonth = ['*', ...Array.from({ length: 31 }, (_, i) => String(i + 1))];
const months = [
  { title: '*', value: '*' },
  { title: 'January', value: '1' },
  { title: 'February', value: '2' },
  { title: 'March', value: '3' },
  { title: 'April', value: '4' },
  { title: 'May', value: '5' },
  { title: 'June', value: '6' },
  { title: 'July', value: '7' },
  { title: 'August', value: '8' },
  { title: 'September', value: '9' },
  { title: 'October', value: '10' },
  { title: 'November', value: '11' },
  { title: 'December', value: '12' },
];
const daysOfWeek = [
  { title: '*', value: '*' },
  { title: 'Monday', value: '1' },
  { title: 'Tuesday', value: '2' },
  { title: 'Wednesday', value: '3' },
  { title: 'Thursday', value: '4' },
  { title: 'Friday', value: '5' },
  { title: 'Saturday', value: '6' },
  { title: 'Sunday', value: '0' },
];

const defaultItem = {
  command: commands[0].value,
  hour: hours[0],
  minute: minutes[0],
  day_of_month: daysOfMonth[0],
  month: months[0].value,
  day_of_week: daysOfWeek[0].value,
  output: null,
  mechanism: 'auto',
};

// Filled from Settings/Timers/getJobs. Starts empty: it used to be seeded with a
// fake "reboot at 00:00" row that looked exactly like a real timer until the
// first backend reply replaced it, so the page claimed a schedule that did not
// exist.
const timers = ref([]);

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
  // The list is not touched here: the backend answers every mutation with a
  // fresh getJobs, and the listener below rewrites it. Splicing locally without
  // ever sending the command - which is what this did - made a deletion look
  // like it worked until the next refresh brought the timer back.
  if (confirm('Are you sure you want to delete this item?')) {
    api.Settings.Timers.delete_job({ uuid: item.uuid });
  }
}

function setEnabled(item, state) {
  api.Settings.Timers.set_job_enabled({ uuid: item.uuid, state: Boolean(state) });
}

function close() {
  dialog.value = false;
  editedItem.value = { ...defaultItem };
  editedIndex.value = -1;
}

function save() {
  const item = { ...editedItem.value };
  delete item.enabled;
  delete item.known;
  if (!isScreenAction(item.command)) {
    // only screen timers have an output and a mechanism
    delete item.output;
    delete item.mechanism;
  }
  if (editedIndex.value > -1) {
    api.Settings.Timers.edit_job(item);
  } else {
    delete item.uuid;
    api.Settings.Timers.add_job(item);
  }
  close();
}

api.Settings.Timers.jobs.on(data => {
  timers.value = data.jobs;
}, {init: true})

</script>


<style scoped>

</style>
