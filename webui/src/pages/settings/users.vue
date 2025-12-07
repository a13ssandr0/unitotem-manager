<script setup lang="ts">
import {ref, watch, computed} from 'vue'

const dialog = ref(false)
const passwordDialog = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const selectedUser = ref(null)

const users = ref([
  {id: 1, username: 'user1', permissions: {scheduler: false, audio: false, power: false, admin: false}},
  {id: 2, username: 'user2', permissions: {scheduler: false, audio: true, power: true, admin: false}},
  {id: 3, username: 'user3', permissions: {scheduler: true, audio: true, power: true, admin: true}}
])

const permissions = ref(['Scheduler', 'Audio', 'Power', 'Admin'])

const passwordsMatch = computed(() => {
  return newPassword.value === confirmPassword.value && newPassword.value.trim() !== '';
});

const passwordErrorMessages = computed(() => {
  if (confirmPassword.value && newPassword.value !== confirmPassword.value) {
    return 'Passwords do not match';
  }
  return undefined;
});

// Watch for changes in users' permissions
watch(users, (currentUsers) => {
  currentUsers.forEach(user => {
    if (user.permissions.admin) {
      // If admin is checked, ensure other permissions are also checked
      user.permissions.scheduler = true;
      user.permissions.audio = true;
      user.permissions.power = true;
    }
  });
}, {deep: true});

function addUser() {
  if (newUsername.value.trim()) {
    const newUser = {
      id: users.value.length > 0 ? Math.max(...users.value.map(u => u.id)) + 1 : 1,
      username: newUsername.value,
      permissions: {scheduler: false, audio: false, power: false, admin: false}
    };
    users.value.push(newUser);
    newUsername.value = ''; // Reset
    dialog.value = false; // Close dialog
  }
}

function openPasswordDialog(user) {
  selectedUser.value = user;
  newPassword.value = '';
  confirmPassword.value = '';
  passwordDialog.value = true;
}

function changePassword() {
  if (selectedUser.value && passwordsMatch.value) {
    console.log(`Changing password for ${selectedUser.value.username} to ${newPassword.value}`);
    // Here you would typically make an API call to update the password
    passwordDialog.value = false;
  }
}
</script>

<template>
  <div>
    <div class="d-flex justify-center">
      <v-card
        class="ma-4"
        max-width="1000"
        width="100%"
        :title="String($route.name)"
      >
        <v-table>
          <thead>
          <tr>
            <th class="text-left">Username</th>
            <th v-for="permission in permissions" :key="permission" class="text-center">{{ permission }}</th>
            <th class="text-right"></th>
          </tr>
          </thead>
          <tbody>
          <tr v-for="user in users" :key="user.id">
            <td>{{ user.username }}</td>
            <td v-for="permission in permissions" :key="permission">
              <div class="d-flex justify-center">
                <v-checkbox
                  v-model="user.permissions[permission.toLowerCase()]"
                  :disabled="permission.toLowerCase() !== 'admin' && user.permissions.admin"
                  :class="{ 'darker-disabled-checkbox': permission.toLowerCase() !== 'admin' && user.permissions.admin }"
                  color="primary"
                  hide-details
                ></v-checkbox>
              </div>
            </td>
            <td class="text-right">
              <v-btn variant="text" icon="mdi-pencil" color="yellow" class="mr-2" aria-label="Edit" @click="openPasswordDialog(user)"></v-btn>
              <v-btn variant="text" icon="mdi-delete" color="red" aria-label="Delete"></v-btn>
            </td>
          </tr>
          </tbody>
        </v-table>
      </v-card>
    </div>

    <v-dialog v-model="dialog" max-width="400px">
      <v-card>
        <v-card-title class="mt-2 ml-2">
          <span class="text-h5">Add User</span>
        </v-card-title>
        <v-card-text class="pb-0">
          <v-text-field
            v-model="newUsername"
            label="Username"
            required
            variant="outlined"
            @keyup.enter="addUser"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="dialog = false">
            Cancel
          </v-btn>
          <v-btn color="primary" variant="text" @click="addUser">
            Add
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="passwordDialog" max-width="400px">
      <v-card>
        <v-card-title class="mt-2 ml-2">
          <span class="text-h5">Change Password for {{ selectedUser?.username }}</span>
        </v-card-title>
        <v-card-text class="pb-0">
          <v-text-field
            v-model="newPassword"
            label="New Password"
            required
            variant="outlined"
            type="password"
            class="mb-2"
          ></v-text-field>
          <v-text-field
            v-model="confirmPassword"
            label="Confirm Password"
            required
            variant="outlined"
            type="password"
            @keyup.enter="changePassword"
            :error-messages="passwordErrorMessages"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="passwordDialog = false">
            Cancel
          </v-btn>
          <v-btn color="primary" variant="text" @click="changePassword" :disabled="!passwordsMatch">
            Save
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-btn
      class="ma-4"
      position="fixed"
      location="bottom right"
      icon="mdi-account-plus"
      color="primary"
      @click="dialog = true"
      aria-label="Add user"
    ></v-btn>
  </div>
</template>

<style scoped>
.darker-disabled-checkbox :deep(.mdi-checkbox-marked) {
  color: rgb(var(--v-theme-primary)) !important;
}
</style>
