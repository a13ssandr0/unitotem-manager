#!/bin/bash
# Build the cefpython3 wheels (Python 3.10-3.14, x86_64 + aarch64, proprietary codecs) on the build server.
#
#   tools/build-cef-wheels.sh start      # copy the sources, start the build DETACHED (systemd unit, survives a lost connection)
#   tools/build-cef-wheels.sh status
#   tools/build-cef-wheels.sh fetch      # rsync the wheels to vendor/cefpython-wheels/
#
# The CEF distributions are the ones built from source on the server (/nas/cef-build, automate-git.py with
# proprietary_codecs=true ffmpeg_branding=Chrome; x64 native, arm64 cross with Chromium's sysroot). The aarch64 wheels are
# CROSS compiled inside the container (gcc-aarch64-linux-gnu + multiarch libraries), see tools/cef-wheels-inner.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${BUILD_HOST:-alessandro@supercompiler}"
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=15)
RSYNC=(rsync -e "ssh -o BatchMode=yes" --rsync-path="sudo rsync")     # the server is a VM: alessandro uses sudo for docker and systemd
SERVER_DIR=/nas/cef-wheels
DISTRIB=/nas/cef-build/work/chromium/src/cef/binary_distrib

case "${1:-}" in
start)
    "${SSH[@]}" "$HOST" "sudo mkdir -p $SERVER_DIR/src $SERVER_DIR/out $SERVER_DIR/tmp"
    # cefpython3/ in the checkout may hold the artifacts of an older local build (stale libcef.so, extension modules): only __init__.py goes
    "${RSYNC[@]}" -a --delete --delete-excluded --exclude build --exclude .git --include cefpython3/__init__.py --exclude "cefpython3/*" "$REPO/vendor/cefpython/" "$HOST:$SERVER_DIR/src/"
    "${RSYNC[@]}" -a "$REPO/tools/cef-wheels-inner.sh" "$HOST:$SERVER_DIR/inner.sh"
    "${SSH[@]}" "$HOST" "sudo rm -f $SERVER_DIR/DONE $SERVER_DIR/FAILED $SERVER_DIR/out/*.whl; sudo systemd-run --unit=cef-wheels --collect --working-directory=$SERVER_DIR \
        bash -c 'docker run --rm -v $SERVER_DIR/src:/work/src -v $DISTRIB:/work/distrib:ro -v $SERVER_DIR/out:/work/out \
            -v $SERVER_DIR/tmp:/work/tmp -v $SERVER_DIR/inner.sh:/inner.sh:ro -e WHEEL_VERSION=${WHEEL_VERSION:-147.0+codecs} \
            -e PYTHONS=\"${PYTHONS:-3.10 3.11 3.12 3.13 3.14}\" -e ARCHES=\"${ARCHES:-x86_64 aarch64}\" \
            debian:trixie bash /inner.sh > $SERVER_DIR/build.log 2>&1 && touch $SERVER_DIR/DONE || touch $SERVER_DIR/FAILED'"
    ;;
status)
    "${SSH[@]}" "$HOST" "ls $SERVER_DIR/DONE $SERVER_DIR/FAILED 2>/dev/null; systemctl is-active cef-wheels; grep '^===' $SERVER_DIR/build.log | tail -5; ls $SERVER_DIR/out"
    ;;
fetch)
    mkdir -p "$REPO/vendor/cefpython-wheels"
    rsync -av -e "ssh -o BatchMode=yes" "$HOST:$SERVER_DIR/out/" "$REPO/vendor/cefpython-wheels/"
    ;;
*) echo "usage: $0 start|status|fetch" >&2; exit 2 ;;
esac
