#!/bin/sh
# usage: deploy.sh HOST ARCH [quick]
# Copies the dev build of cefpython3 (from the build server, /nas/wl-dev/pkg-ARCH-3.13) and these test scripts to
# /var/lib/unitotem/wl/cefdev on a board (use PYTHONPATH=/var/lib/unitotem/wl/cefdev). "quick" sends only the extension module and
# the subprocess (not libcef.so and the resources). The boards have no rsync: tar over ssh.
H=$1; ARCH=$2; Q=$3
HERE=$(cd "$(dirname "$0")" && pwd)
CACHE=${CACHE:-/tmp/cefdev-cache-$ARCH}
mkdir -p $CACHE
rsync -a --delete -e "ssh -o BatchMode=yes" alessandro@supercompiler:/nas/wl-dev/pkg-$ARCH-3.13/cefpython3/ $CACHE/cefpython3/ || exit 1
cp $HERE/../../vendor/cefpython/cefpython3/__init__.py $CACHE/cefpython3/
if [ "$Q" = quick ]; then
  tar -C $CACHE -c cefpython3/__init__.py cefpython3/cefpython_py313.so cefpython3/subprocess | $HERE/ssh.sh $H 'cd /var/lib/unitotem/wl/cefdev && tar -x'
else
  tar -C $CACHE -c . | $HERE/ssh.sh $H 'rm -rf /var/lib/unitotem/wl/cefdev; mkdir -p /var/lib/unitotem/wl/cefdev && cd /var/lib/unitotem/wl/cefdev && tar -x'
fi
tar -C $HERE -c . | $HERE/ssh.sh $H 'mkdir -p /var/lib/unitotem/wl/tests && cd /var/lib/unitotem/wl/tests && tar -x'
