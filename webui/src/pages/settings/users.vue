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
          <tr v-for="(userData, username) in users" :key="username">
            <td>{{ username }}</td>
            <td v-for="permission in permissions" :key="permission">
              <div class="d-flex justify-center">
                <v-checkbox
                  v-model="userData.perms"
                  :value="permission.toLowerCase()"
                  :disabled="permission.toLowerCase() !== 'admin' && userData.perms.includes('admin')"
                  :class="{ 'darker-disabled-checkbox': permission.toLowerCase() !== 'admin' && userData.perms.includes('admin') }"
                  color="primary"
                  hide-details
                  @change="onPermissionChange(username)"
                ></v-checkbox>
              </div>
            </td>
            <td class="text-right">
              <v-btn variant="text" icon="mdi-pencil" color="yellow" class="mr-2" aria-label="Edit" @click="openPasswordDialog(username)"></v-btn>
              <v-btn variant="text" icon="mdi-delete" color="red" aria-label="Delete" @click="sendCommand('Settings/Security/delUser', {user: username})"></v-btn>
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
            class="mb-2"
          ></v-text-field>
          <v-text-field
            v-model="newPassword"
            label="Password"
            required
            variant="outlined"
            :type="showPassword ? 'text' : 'password'"
            :append-inner-icon="showPassword ? 'mdi-eye' : 'mdi-eye-off'"
            @click:append-inner="showPassword = !showPassword"
            class="mb-2"
          ></v-text-field>
          <v-text-field
            v-model="confirmPassword"
            label="Confirm Password"
            required
            variant="outlined"
            :type="showConfirmPassword ? 'text' : 'password'"
            :append-inner-icon="showConfirmPassword ? 'mdi-eye' : 'mdi-eye-off'"
            @click:append-inner="showConfirmPassword = !showConfirmPassword"
            :error-messages="passwordErrorMessages"
            @keyup.enter="addUser"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="dialog = false">
            Cancel
          </v-btn>
          <v-btn color="primary" variant="text" @click="addUser" :disabled="!passwordsMatch || !newUsername.trim()">
            Add
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="passwordDialog" max-width="400px">
      <v-card>
        <v-card-title class="mt-2 ml-2">
          <span class="text-h5">Change Password for {{ selectedUser }}</span>
        </v-card-title>
        <v-card-text class="pb-0">
          <v-text-field
            v-model="newPassword"
            label="New Password"
            required
            variant="outlined"
            :type="showPassword ? 'text' : 'password'"
            :append-inner-icon="showPassword ? 'mdi-eye' : 'mdi-eye-off'"
            @click:append-inner="showPassword = !showPassword"
            class="mb-2"
          ></v-text-field>
          <v-text-field
            v-model="confirmPassword"
            label="Confirm Password"
            required
            variant="outlined"
            :type="showConfirmPassword ? 'text' : 'password'"
            :append-inner-icon="showConfirmPassword ? 'mdi-eye' : 'mdi-eye-off'"
            @click:append-inner="showConfirmPassword = !showConfirmPassword"
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
      @click="openAddUserDialog"
      aria-label="Add user"
    ></v-btn>
  </div>
</template>

<script setup>
import {ref, computed} from 'vue'

const dialog = ref(false)
const passwordDialog = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)
const selectedUser = ref(null)

const users = ref({})

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

function openAddUserDialog() {
  newUsername.value = '';
  newPassword.value = '';
  confirmPassword.value = '';
  showPassword.value = false;
  showConfirmPassword.value = false;
  dialog.value = true;
}

function addUser() {
  if (newUsername.value.trim() && passwordsMatch.value) {
    sendCommand('Settings/Security/addUser', {username: newUsername.value, password: newPassword.value});
    newUsername.value = ''; // Reset
    newPassword.value = '';
    confirmPassword.value = '';
    dialog.value = false; // Close dialog
  }
}

function openPasswordDialog(username) {
  selectedUser.value = username;
  newPassword.value = '';
  confirmPassword.value = '';
  showPassword.value = false;
  showConfirmPassword.value = false;
  passwordDialog.value = true;
}

function changePassword() {
  if (selectedUser.value && passwordsMatch.value) {
    sendCommand('Settings/Security/setUserPass', {username: selectedUser.value, password: newPassword.value});
    passwordDialog.value = false;
  }
}

function onPermissionChange(username) {
  const user = users.value[username];
  if (user.perms.includes('admin')) {
    // If admin is checked, ensure other permissions are also checked
    const allPerms = permissions.value.map(p => p.toLowerCase());
    allPerms.forEach(p => {
      if (!user.perms.includes(p)) {
        user.perms.push(p);
      }
    });
  }
  sendCommand('Settings/Security/setUserPerms', {username: username, perms: user.perms});
}

const sendCommand = window.sendCommand;

onWSOpen = (e) => {
  sendCommand("Settings/Security/getUsers")
}

if (isWSReady()) onWSOpen()

onWSMessage = (data) => {
  switch (data.target) {
    case "Settings/Security/getUsers":
      users.value = data.users;
      // Enforce admin permissions logic on load
      for (const username in users.value) {
        const user = users.value[username];
        if (user.perms.includes('admin')) {
          const allPerms = permissions.value.map(p => p.toLowerCase());
          allPerms.forEach(p => {
            if (!user.perms.includes(p)) {
              user.perms.push(p);
            }
          });
        }
      }
      break;
  }
}

</script>


<style scoped>
.darker-disabled-checkbox :deep(.mdi-checkbox-marked) {
  color: rgb(var(--v-theme-primary)) !important;
}
</style>
