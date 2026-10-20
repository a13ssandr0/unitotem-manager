#!/bin/sh
# usage: ssh.sh HOST command...   (password login, never a graphical prompt)
H=$1; shift
exec env -u DISPLAY -u SSH_ASKPASS SSH_ASKPASS_REQUIRE=never sshpass -p unitotem ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR root@$H "$@"
