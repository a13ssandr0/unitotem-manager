#!/bin/bash
# What it does: copies the CEF build scripts between this directory and the build server's work tree.
#   ./sync.sh push   repo -> server (default)    ./sync.sh pull   server -> repo
# Only the *.sh files and patches/ are transferred (never the trees, logs or automate-git.py). apply-patches.sh reads
# patches/ from its own directory, so the server needs it next to the scripts. Env: CEF_HOST, CEF_WORK.
set -eu
HOST=${CEF_HOST:?set CEF_HOST=user@build-server}
REMOTE=${CEF_WORK:?set CEF_WORK=<CEF work dir on the build server>}
HERE=$(cd "$(dirname "$0")" && pwd)
OPTS=(-av --include='*.sh' --include='patches/' --include='patches/***' --exclude='*' -e "ssh -o BatchMode=yes")
case "${1:-push}" in
  push) rsync "${OPTS[@]}" --exclude=sync.sh "$HERE/" "$HOST:$REMOTE/" ;;
  pull) rsync "${OPTS[@]}" --exclude=sync.sh "$HOST:$REMOTE/" "$HERE/" ;;
  *) echo "usage: $0 [push|pull]" >&2; exit 2 ;;
esac
