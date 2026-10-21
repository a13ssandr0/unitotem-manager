#!/bin/sh
# One command from the development machine: put the tests (and, with DEV=1, the dev build of cefpython3 and the ceftests binary if
# present) on a board and run board-suite.sh there. usage: [DEV=1] [HOTPLUG=DP-2] [SKIP="perf"] run-suite.sh HOST x86_64|aarch64
# The board is left as it was found (X session back, or the manager started again in image mode).
HERE=$(cd "$(dirname "$0")" && pwd)
H=$1; ARCH=$2
REPO=$(cd "$HERE/../.." && pwd)
$HERE/deploy.sh $H $ARCH $( [ -n "$DEV" ] && echo full || echo tests ) >/dev/null || exit 1
tar -C $REPO/vendor/cefpython/unittests -c . | $HERE/ssh.sh $H 'rm -rf /var/lib/unitotem/wl/fork/unittests; mkdir -p /var/lib/unitotem/wl/fork/unittests && cd /var/lib/unitotem/wl/fork/unittests && tar -x'
CT=${CEFTESTS_PKG:-/tmp/ceftests-pkg-$ARCH}
[ -d "$CT" ] && tar -C "$CT" -c . | $HERE/ssh.sh $H 'rm -rf /var/lib/unitotem/wl/ceftests; mkdir -p /var/lib/unitotem/wl/ceftests && cd /var/lib/unitotem/wl/ceftests && tar -x'
$HERE/ssh.sh $H "env HOTPLUG='$HOTPLUG' SKIP='$SKIP' CEFPY='$CEFPY' OUTPUT='$OUTPUT' sh /var/lib/unitotem/wl/tests/board-suite.sh" 2>&1 | tee ${LOG:-/tmp/board-suite-$H.log}
