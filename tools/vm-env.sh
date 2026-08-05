#!/bin/sh
# Shared settings for the test-VM helper scripts. Sourced, not executed.
#
# See VM-TESTING.md for the full procedure this belongs to.

# The VM has a static DHCP reservation on libvirt's 'default' network, so it is
# reachable directly by IP and the manager keeps its real default ports (80/443)
# with no port forwarding anywhere. Override with VM_HOST if needed.
VM_HOST="${VM_HOST:-192.168.122.50}"
VM_USER="${VM_USER:-root}"

# Key-based login, so nothing ever has to prompt for the root password
# (which is 'unitotem'). Lives outside this repo together with the disk images.
VM_KEY="${VM_KEY:-$HOME/Sviluppo/unitotem-system/work/vm-test/vm_test_key}"
VM_KNOWN_HOSTS="${VM_KNOWN_HOSTS:-$HOME/Sviluppo/unitotem-system/work/vm-test/known_hosts}"

# Where the working copy is deployed inside the VM. NOT /usr/share/unitotem-manager:
# the kiosk image's root filesystem is read-only, while /var is an overlay on the
# DATA partition and is writable and persistent. A systemd unit override points
# the service here (see VM-TESTING.md, "One-time bootstrap").
VM_APP_DIR="${VM_APP_DIR:-/var/unitotem-manager}"
VM_VENV="${VM_VENV:-/var/unitotem-venv}"

# rsync is not part of the kiosk image and cannot be apt-installed onto a
# read-only root; it is unpacked into /var instead (see VM-TESTING.md).
VM_RSYNC_PATH="${VM_RSYNC_PATH:-/var/opt/rsync/usr/bin/rsync}"

# ssh must NEVER pop up a graphical password prompt: on a KDE desktop a missing
# key or an unknown host makes ssh spawn ksshaskpass, which steals focus and
# blocks automation until someone clicks it. Refusing askpass entirely turns
# those cases into an immediate, visible error instead.
export SSH_ASKPASS_REQUIRE=never
unset SSH_ASKPASS

VM_SSH_OPTS="-i $VM_KEY -o UserKnownHostsFile=$VM_KNOWN_HOSTS -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"
