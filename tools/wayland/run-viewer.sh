#!/bin/sh
# Runs the real manager+viewer on a Wayland compositor on a test machine (as root, from an ssh session).
#   run-viewer.sh start [labwc|cage]    stop X and the manager, start the compositor on VT1 with a copy of the code
#   run-viewer.sh stop                  stop it and bring the normal X session back
# Needs W (default /var/lib/unitotem/wl): the debs of labwc, cage, qt6-wayland extracted (see tools/wayland/README.md),
# the missing shared libraries copied into $W/extra, and the code to run in $W/code (rsync of manager/).
W=${W:-/var/lib/unitotem/wl}
ARCH=$(ls -d $W/usr/lib/*-linux-gnu | head -1)
export XDG_RUNTIME_DIR=/run/wl-xdg LIBSEAT_BACKEND=builtin
export LD_LIBRARY_PATH=$W/extra
export PATH=$W/usr/bin:$PATH
case "$1" in
start)
  systemctl stop unitotem-manager start-xorg 2>&1
  mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR; : > $W/run.log
  COMP=${2:-labwc}
  INNER="cd $W/code && UNITOTEM_VIEWER_PLATFORM=wayland QT_PLUGIN_PATH=$ARCH/qt6/plugins PYTHONUNBUFFERED=1 python3 init.py"
  if [ "$COMP" = cage ]; then RUN="cage -s -- sh -c '$INNER'"; else RUN="labwc -C $W/labwc -s \"sh -c '$INNER'\""; fi
  openvt -c 1 -f -s -- sh -c "exec env XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR LIBSEAT_BACKEND=builtin LD_LIBRARY_PATH=$LD_LIBRARY_PATH WLR_XWAYLAND=$W/usr/bin/Xwayland PATH=$PATH $RUN > $W/run.log 2>&1" ;;
stop)
  pkill -x labwc; pkill -x cage; pkill -x python3; sleep 2; pkill -9 -x python3; pkill -9 -f "cefpython3.*--type"
  rm -rf $XDG_RUNTIME_DIR
  systemctl start start-xorg; sleep 3; systemctl start unitotem-manager ;;
esac
