#!/bin/sh
# usage: COMP=cage|labwc [EXTRAENV="K=V ..."] wl-run2.sh start URL | stop   (ON the Pi; cage/labwc extracted in /tmp/wl)
W=/tmp/wl; LOG=/tmp/wlt/wl.log; ARCHLIB=$(ls -d $W/usr/lib/*-linux-gnu | head -1)
case "$1" in
start)
  systemctl stop unitotem-manager start-xorg 2>&1; pkill -x picom; sleep 1
  mkdir -p /run/wl-xdg; chmod 700 /run/wl-xdg; : > $LOG
  CMD="env WLCEF_EXTRA=\"$WLCEF_EXTRA\" python3 /tmp/wlt/wlcef.py $2"
  if [ "${COMP:-cage}" = weston ]; then
    AL=$W/usr/lib/aarch64-linux-gnu
    cat > /tmp/wlt/weston-start.sh <<EOS
export XDG_RUNTIME_DIR=/run/wl-xdg LIBSEAT_BACKEND=builtin LD_LIBRARY_PATH=$AL:$AL/weston
export WESTON_MODULE_MAP="drm-backend.so=$AL/libweston-14/drm-backend.so;gl-renderer.so=$AL/libweston-14/gl-renderer.so;kiosk-shell.so=$AL/weston/kiosk-shell.so;libexec_weston.so=$AL/weston/libexec_weston.so"
$W/usr/bin/weston --backend=drm --shell=kiosk --socket=wl-test --idle-time=0 > /tmp/wlt/weston.log 2>&1 &
sleep 5
WAYLAND_DISPLAY=wl-test $CMD > $LOG 2>&1
EOS
    openvt -c 1 -f -s -- sh /tmp/wlt/weston-start.sh; exit 0
  fi
  if [ "${COMP:-cage}" = labwc ]; then RUN="labwc -C /tmp/wlt/labwc-cfg -s '$CMD'"; else RUN="cage -s -- $CMD"; fi
  openvt -c 1 -f -s -- sh -c "exec env XDG_RUNTIME_DIR=/run/wl-xdg LIBSEAT_BACKEND=builtin LD_LIBRARY_PATH=$ARCHLIB WLR_XWAYLAND=$W/usr/bin/Xwayland PATH=$W/usr/bin:\$PATH $EXTRAENV $RUN > $LOG 2>&1" ;;
stop)
  pkill -x cage; pkill -x labwc; pkill -f "[w]lcef.py"; sleep 2; pkill -9 -f "[w]lcef.py"; pkill -9 -f "[s]ubprocess --type"; rm -rf /tmp/wlcef-profile /run/wl-xdg
  systemctl start start-xorg; sleep 3; systemctl start unitotem-manager ;;
esac
