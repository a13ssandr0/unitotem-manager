#!/bin/sh
# ON the Pi: X11 (no compositor), standalone CEF, raf5 real draw counters at the window size.
pkill -x labwc; pkill -x cage; pkill -f "[w]lcef.py"; sleep 1; pkill -9 -f "[s]ubprocess --type"
systemctl start start-xorg; sleep 4
export DISPLAY=:0 XAUTHORITY=/run/unitotem-x11.auth
rm -rf /tmp/wlcef-profile
WLCEF_EXTRA="ozone-platform=x11,use-gl=angle,use-angle=gles${EXTRA:+,$EXTRA}" nohup python3 /tmp/wlt/wlcef.py about:blank > /tmp/wlt/cef-x.log 2>&1 &
i=0; until curl -s -m2 127.0.0.1:9223/json/version >/dev/null || [ $i -ge 40 ]; do sleep 1; i=$((i+1)); done; sleep 2
echo "### X11 [$EXTRA]"; timeout 100 python3 /tmp/vp/raf.py 0 1920 1080 'canvas2d|webgl2 default|webgl2 aa:false|webgl1 default|webgl1 aa:false'
pkill -f "[w]lcef.py"; sleep 2; pkill -9 -f "[s]ubprocess --type"
