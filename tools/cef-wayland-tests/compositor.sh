#!/bin/sh
# Starts/stops ONLY the compositor (the real unitotem-compositor.service from the development tree, see tools/wayland/README.md and
# session.sh) on a board, without the manager, so that a test program can be the only Wayland client. Run as root on the board.
#   compositor.sh start     stop X and the service, start labwc
#   compositor.sh stop      stop labwc, bring the normal X session back (ALWAYS run it when the tests are over)
# Test programs: export WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0 and do NOT set XDG_RUNTIME_DIR.
W=${W:-/var/lib/unitotem/wl}
case "$1" in
start)
  systemctl stop unitotem-manager start-xorg unitotem-wl-test unitotem-compositor 2>/dev/null
  pkill -x labwc; pkill -x cage; pkill -9 -f '[c]efpython3/subprocess'; sleep 2
  mkdir -p /run/systemd/system/unitotem-compositor.service.d /usr/share/unitotem/xdg/labwc
  cp $W/unitotem-compositor.service /run/systemd/system/unitotem-compositor.service
  cp $W/labwc-rc.xml /usr/share/unitotem/xdg/labwc/rc.xml
  cat > /run/systemd/system/unitotem-compositor.service.d/dev.conf <<EOD
[Service]
Environment=LD_LIBRARY_PATH=$W/extra
Environment=PATH=$W/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=WLR_XWAYLAND=$W/usr/bin/Xwayland
ExecStart=
ExecStart=$W/usr/bin/labwc --merge-config
EOD
  systemctl daemon-reload; systemctl start unitotem-compositor; sleep 3
  ls /run/unitotem/xdg/wayland-0 ;;
stop)
  systemctl stop unitotem-compositor 2>/dev/null; pkill -9 -f '[c]efpython3/subprocess'; sleep 1
  rm -f /run/systemd/system/unitotem-compositor.service /run/systemd/system/unitotem-compositor.service.d/dev.conf; systemctl daemon-reload
  systemctl start start-xorg; sleep 3; systemctl start unitotem-manager ;;
esac
