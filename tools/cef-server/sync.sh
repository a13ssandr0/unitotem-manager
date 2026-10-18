#!/bin/bash
# What it does: copies the CEF build scripts between this directory and the build server's work tree.
#   ./sync.sh push   repo -> server (default)    ./sync.sh pull   server -> repo
# Only the *.sh files are transferred (never the trees, logs or automate-git.py). Env: CEF_HOST, CEF_WORK.
set -eu
HOST=${CEF_HOST:-alessandro@supercompiler}
REMOTE=${CEF_WORK:-/nas/cef-build/work}
HERE=$(cd "$(dirname "$0")" && pwd)
OPTS=(-av --include='*.sh' --exclude='*' -e "ssh -o BatchMode=yes")
case "${1:-push}" in
  push) rsync "${OPTS[@]}" --exclude=sync.sh "$HERE/" "$HOST:$REMOTE/" ;;
  pull) rsync "${OPTS[@]}" --exclude=sync.sh "$HOST:$REMOTE/" "$HERE/" ;;
  *) echo "usage: $0 [push|pull]" >&2; exit 2 ;;
esac
