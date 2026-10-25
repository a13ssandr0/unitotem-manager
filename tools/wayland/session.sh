#!/bin/sh
# Test machine (root, from ssh): runs the REAL compositor unit and the manager from a copy of the code, with the programs of a
# development tree ($W, see README.md) instead of installed packages.
#   session.sh start     stop X and the service, start unitotem-compositor and the manager (unit unitotem-wl-test)
#   session.sh stop      stop both and bring the normal X session back
# $W (default /var/lib/unitotem/wl): usr/ extra/ (programs and libraries of labwc, wlr-randr, wlopm, wayvnc, wlrctl, qt6-wayland),
# code/ (the manager tree), the unit in $W/unitotem-compositor.service.
W=${W:-/var/lib/unitotem/wl}
ARCH=$(ls -d $W/usr/lib/*-linux-gnu | head -1)
case "$1" in
start)
  systemctl stop unitotem-manager start-xorg unitotem-wl-test unitotem-compositor 2>/dev/null
  pkill -x labwc; pkill -x cage; pkill -9 -f 'python3 init.py'; pkill -9 -f 'cefpython3/subprocess'; sleep 2
  mkdir -p /run/systemd/system/unitotem-compositor.service.d
  cp $W/unitotem-compositor.service /run/systemd/system/unitotem-compositor.service
  mkdir -p /usr/share/unitotem/xdg/labwc; cp $W/labwc-rc.xml /usr/share/unitotem/xdg/labwc/rc.xml
  cat > /run/systemd/system/unitotem-compositor.service.d/dev.conf <<EOD
[Service]
Environment=LD_LIBRARY_PATH=$W/extra
Environment=PATH=$W/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=WLR_XWAYLAND=$W/usr/bin/Xwayland
ExecStart=
ExecStart=$W/usr/bin/labwc --merge-config $LABWC_ARGS
EOD
  systemctl daemon-reload; systemctl start unitotem-compositor
  systemctl reset-failed unitotem-wl-test 2>/dev/null
  systemd-run --unit=unitotem-wl-test --working-directory=${CODE:-$W/code} -p KillMode=process \
    -E WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0 -E LD_LIBRARY_PATH=$W/extra \
    -E PATH=$W/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -E QT_PLUGIN_PATH=$ARCH/qt6/plugins \
    -E PYTHONUNBUFFERED=1 $WLEXTRA_ARGS /usr/bin/python3 init.py
  ;;
stop)
  systemctl stop unitotem-wl-test unitotem-compositor 2>/dev/null; pkill -9 -f "cefpython3/subprocess"; sleep 1
  rm -f /run/systemd/system/unitotem-compositor.service /run/systemd/system/unitotem-compositor.service.d/dev.conf; systemctl daemon-reload
  systemctl start start-xorg; sleep 3; systemctl start unitotem-manager ;;
esac
