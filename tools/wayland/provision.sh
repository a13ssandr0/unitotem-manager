#!/bin/sh
# Prepares a test board for tools/wayland/session.sh: the Wayland stack of the kiosk extracted from the Debian trixie debs into
# $W (default /var/lib/unitotem/wl) on the board, plus the code tree. Nothing is installed on the board, no dpkg change.
#   tools/wayland/provision.sh HOST ARCH      ARCH = amd64 | arm64     (root, password unitotem, see CLAUDE.md for the ssh rules)
# Needs docker on this machine (the debs are downloaded in a trixie container, multiarch, no qemu), sshpass, zstd.
set -e
HOST=$1; ARCH=$2; W=${W:-/var/lib/unitotem/wl}
[ -n "$HOST" ] && [ -n "$ARCH" ] || { echo "usage: $0 HOST amd64|arm64" >&2; exit 2; }
case $ARCH in amd64) TRIPLET=x86_64-linux-gnu;; arm64) TRIPLET=aarch64-linux-gnu;; *) echo "bad arch" >&2; exit 2;; esac
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
SSH="env -u DISPLAY -u SSH_ASKPASS SSH_ASKPASS_REQUIRE=never sshpass -p unitotem ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR root@$HOST"
docker run --rm -v "$WORK:/out" debian:trixie sh -c "
  set -e
  dpkg --add-architecture $ARCH; apt-get update -qq
  apt-get install -y --download-only -o Dir::Cache::archives=/out labwc:$ARCH wlrctl:$ARCH qt6-wayland:$ARCH xwayland:$ARCH wlr-randr:$ARCH wlopm:$ARCH wayvnc:$ARCH >/dev/null
  chmod -R a+r /out"
mkdir "$WORK/root"
for d in "$WORK"/*_"$ARCH".deb "$WORK"/*_all.deb; do dpkg-deb -x "$d" "$WORK/root"; done
rm -rf "$WORK/root/usr/share/doc" "$WORK/root/usr/share/man" "$WORK/root/usr/share/locale"
tar -C "$WORK/root" -cf - . | zstd -3 -T0 -q | $SSH "mkdir -p $W && cd $W && zstd -d | tar x"
# the libraries the board does not have go to extra/ (everything else of the system is used as it is)
$SSH "cd $W && mkdir -p extra && A=usr/lib/$TRIPLET && for f in \$(cd \$A; find . -maxdepth 1 -name '*.so*'); do [ -e /usr/lib/$TRIPLET/\$f ] || cp -a \$A/\$f extra/; done; ls extra | wc -l"
# code, base configuration, unit and the session script
tar -C "$REPO/manager" --exclude __pycache__ --exclude tests -cf - . | $SSH "rm -rf $W/code && mkdir -p $W/code && tar -C $W/code -xf -"
tar -C "$REPO/plymouth/unitotem" -cf - $(cd "$REPO/plymouth/unitotem"; ls wordmark-*.png spinner-*.png) | $SSH "mkdir -p $W/code/plugins/unitotem_viewer/static/bootsplash && tar -C $W/code/plugins/unitotem_viewer/static/bootsplash -xf -"
tar -C "$REPO" -cf - debian/labwc-rc.xml debian/unitotem-viewer.unitotem-compositor.service tools/wayland/session.sh | $SSH "cd $W && tar -xf - && mv debian/labwc-rc.xml labwc-rc.xml && mv debian/unitotem-viewer.unitotem-compositor.service unitotem-compositor.service && mv tools/wayland/session.sh session.sh && rmdir debian tools/wayland tools"
echo "provisioned $HOST ($ARCH) in $W"
