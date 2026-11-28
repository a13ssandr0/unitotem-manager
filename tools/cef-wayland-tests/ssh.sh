#!/bin/sh
# usage: UT_PASSWORD_FILE=FILE ssh.sh HOST command...   (root password read from FILE, mode 0600; never a graphical prompt)
: "${UT_PASSWORD_FILE:?file holding the root password of the board}"
H=$1; shift
exec env -u DISPLAY -u SSH_ASKPASS SSH_ASKPASS_REQUIRE=never sshpass -f "$UT_PASSWORD_FILE" ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR root@$H "$@"
