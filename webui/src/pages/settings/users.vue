<template>
  <CardColumn>
    <div class="d-flex justify-center" style="width: 100%">
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
            <th v-for="permission in permissions" :key="permission" class="text-center">{{ permission === 'Admin' ? 'Admin (sudo)' : permission }}</th>
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
              <v-btn variant="text" icon="mdi-delete" color="red" aria-label="Delete" @click="openDeleteDialog(username)"></v-btn>
            </td>
          </tr>
          </tbody>
        </v-table>
        <v-card-text class="pt-2">
          <v-switch :model-value="loginUserList.show" color="primary" density="compact" hide-details
                    label="Show the list of users on the login page"
                    @update:model-value="v => api.Settings.Security.set_login_user_list({show: !!v})"></v-switch>
        </v-card-text>
      </v-card>
    </div>

    <v-dialog v-model="dialog" max-width="400px">
      <v-card>
        <v-card-title class="mt-2 ml-2">
          <span class="text-h5">Add User</span>
        </v-card-title>
        <v-card-text class="pb-0 dialog-text">
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
            :maxlength="passwordSpec?.max_length"
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
            :maxlength="passwordSpec?.max_length"
            :error-messages="passwordErrorMessages"
            @keyup.enter="addUser"
          ></v-text-field>
          <PasswordChecklist class="pb-2" :spec="passwordSpec" :password="newPassword" :username="newUsername"
                             :confirm="confirmPassword" @valid="v => addValid = v"></PasswordChecklist>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="dialog = false">
            Cancel
          </v-btn>
          <v-btn color="primary" variant="text" @click="addUser" :disabled="!addValid || !newUsername.trim()">
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
        <v-card-text class="pb-0 dialog-text">
          <v-text-field
            v-model="newPassword"
            label="New Password"
            required
            variant="outlined"
            :type="showPassword ? 'text' : 'password'"
            :append-inner-icon="showPassword ? 'mdi-eye' : 'mdi-eye-off'"
            @click:append-inner="showPassword = !showPassword"
            :maxlength="passwordSpec?.max_length"
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
            :maxlength="passwordSpec?.max_length"
            :error-messages="passwordErrorMessages"
          ></v-text-field>
          <PasswordChecklist class="pb-2" :spec="passwordSpec" :password="newPassword" :username="selectedUser || ''"
                             :confirm="confirmPassword" @valid="v => changeValid = v"></PasswordChecklist>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="passwordDialog = false">
            Cancel
          </v-btn>
          <v-btn color="primary" variant="text" @click="changePassword" :disabled="!changeValid">
            Save
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deleteDialog" max-width="400px">
      <v-card>
        <v-card-title class="mt-2 ml-2">
          <span class="text-h5">Delete {{ deleteTarget }}?</span>
        </v-card-title>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" autofocus @click="deleteDialog = false">
            Cancel
          </v-btn>
          <v-btn color="error" variant="text" @click="confirmDelete">
            Delete
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <template #fab>
      <v-btn icon="mdi-account-plus" color="primary" size="large" elevation="6" @click="openAddUserDialog" aria-label="Add user"></v-btn>
    </template>
  </CardColumn>
</template>

<script setup>
import {ref, computed} from 'vue'
import {api} from '@/api'
import CardColumn from '@/components/CardColumn.vue'
import PasswordChecklist from '@/components/PasswordChecklist.vue'

const dialog = ref(false)
const passwordDialog = ref(false)
const deleteDialog = ref(false)
// kept after the dialog closes so the title does not change during the closing animation
const deleteTarget = ref('')
const newUsername = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)
const selectedUser = ref(null)
// set by the checklists: every rule met and the passwords match
const addValid = ref(false)
const changeValid = ref(false)

// The server's password policy spec; the checklist and the maxlength come from it
const passwordSpec = api.Settings.Security.password_policy.use({pick: 'rules', initial: null})

const loginUserList = api.Settings.Security.login_user_list.use({initial: {show: true}})

const users = ref({})

const permissions = ref(['Scheduler', 'Audio', 'Power', 'Admin'])

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
  if (newUsername.value.trim() && addValid.value) {
    api.Settings.Security.add_user({username: newUsername.value, password: newPassword.value});
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
  if (selectedUser.value && changeValid.value) {
    api.Settings.Security.set_user_password({username: selectedUser.value, password: newPassword.value});
    passwordDialog.value = false;
  }
}

function openDeleteDialog(username) {
  deleteTarget.value = username;
  deleteDialog.value = true;
}

function confirmDelete() {
  api.Settings.Security.delete_user({username: deleteTarget.value});
  deleteDialog.value = false;
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
  api.Settings.Security.set_user_permissions({username: username, perms: user.perms});
}

api.Settings.Security.users.on(data => {
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
}, {init: true})

</script>


<style scoped>
.darker-disabled-checkbox :deep(.mdi-checkbox-marked) {
  color: rgb(var(--v-theme-primary)) !important;
}
.dialog-text {
  overflow-x: hidden;
  min-width: 0;
}
</style>
