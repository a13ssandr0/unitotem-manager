#!/bin/sh
# ON the Pi: labwc (fullscreen rule) + a native Wayland client; prints the client's own fps.
pkill -x cage; pkill -x labwc; pkill -f "[w]lcef.py"; sleep 1; pkill -9 -f "[s]ubprocess --type"
W=/tmp/wl; AL=$W/usr/lib/aarch64-linux-gnu
mkdir -p /run/wl-xdg; chmod 700 /run/wl-xdg
CMD="$1"
openvt -c 1 -f -s -- sh -c "exec env XDG_RUNTIME_DIR=/run/wl-xdg LIBSEAT_BACKEND=builtin LD_LIBRARY_PATH=$AL:$AL/weston WLR_XWAYLAND=$W/usr/bin/Xwayland PATH=$W/usr/bin:\$PATH labwc -C /tmp/wlt/labwc-cfg -s '$CMD' > /tmp/wlt/native.log 2>&1"
sleep 14; cat /tmp/wlt/client.log | head -6
