#!/bin/sh
# Runs the whole CEF Views test suite ON a board (root), in one go. Works in two modes, detected by itself:
#   image mode  the board runs the image: unitotem-compositor.service is active. The manager is stopped for the run and started again
#               at the end (trap); the compositor unit is never touched. Uses WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0 and the
#               system python3-cefpython3 (CEFPY=system) unless CEFPY=dev (the tree in $W/cefdev, put there by deploy.sh).
#   X mode      the board is on the X session (development tree in $W): compositor.sh starts our own labwc and stops it at the end.
# Env: W=/var/lib/unitotem/wl  CEFPY=system|dev (default: system in image mode, dev in X mode)
#      HOTPLUG=OUTPUT  also unplug/re-plug that output in outputs.py (not for the last output of a Pi: it cannot be disabled)
#      SECONDS_PERF=15  PERF_GPU=pi|vaapi|none (default: from the machine)  OUTPUT=NAME (default: the first output, for perf)
#      SKIP="outputs views ceftests perf"  to skip parts
W=${W:-/var/lib/unitotem/wl}; export W
T=$W/tests; export WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0
unset XDG_RUNTIME_DIR
export LD_LIBRARY_PATH=$W/extra
WLR=$W/usr/bin/wlr-randr
if systemctl is-active --quiet unitotem-compositor && ! systemctl is-active --quiet start-xorg; then
  MODE=image; CEFPY=${CEFPY:-system}
  systemctl stop unitotem-manager
  trap 'systemctl start unitotem-manager' EXIT
else
  MODE=x; CEFPY=${CEFPY:-dev}
  sh $T/compositor.sh start >/dev/null
  trap 'sh $T/compositor.sh stop >/dev/null' EXIT
fi
[ "$CEFPY" = dev ] && export PYTHONPATH=$W/cefdev
echo "== mode=$MODE cefpy=$CEFPY host=$(hostname) arch=$(uname -m) $(date -Is)"
python3 -c "from cefpython3 import cefpython as c; print('module', c.__file__, c.__version__, 'SetFullscreenOnDisplay' if hasattr(c.Window,'SetFullscreenOnDisplay') else 'NO-API')"
OUT=${OUTPUT:-$($WLR --json | python3 -c "import json,sys; print(sorted(o['name'] for o in json.load(sys.stdin) if o['enabled'])[0])")}
echo "== outputs:"; $WLR --json | python3 -c "import json,sys; [print(' ',o['name'],o['enabled'],[(m['width'],m['height']) for m in o['modes'] if m['current']]) for o in json.load(sys.stdin)]"
GPU=${PERF_GPU:-$(python3 - <<'PY'
import os
d=open('/proc/device-tree/model').read() if os.path.exists('/proc/device-tree/model') else ''
print('pi' if 'Raspberry' in d else 'vaapi')
PY
)}
skip() { case " $SKIP " in *" $1 "*) return 0;; esac; return 1; }
cd /tmp
if ! skip outputs; then echo "== outputs.py"; timeout 300 python3 $T/outputs.py ${HOTPLUG:+--hotplug $HOTPLUG} 2>&1 | grep -v "dbus\|^$\|DevTools\|Vulkan"; fi
if [ -n "$HOTPLUG" ] && ! skip observer; then echo "== observer_hotplug.py $HOTPLUG"; timeout 300 python3 $T/observer_hotplug.py $HOTPLUG 2>&1 | grep -v "dbus\|^$\|DevTools\|Vulkan"; fi
if ! skip views; then echo "== views_test.py"; (cd $W/fork/unittests && timeout 300 python3 views_test.py 2>&1 | grep -v "dbus\|^$\|DevTools\|Vulkan" | tail -n 12); fi
if ! skip ceftests && [ -x $W/ceftests/ceftests ]; then echo "== ceftests (Views suites)"; (cd $W/ceftests && timeout 900 ./ceftests --no-sandbox --ozone-platform=wayland --gtest_filter="ViewsWindowTest.*:ViewsBrowserViewTest.*:ViewsPanelTest.*" 2>&1 | grep "FAILED\|PASSED\|tests ran\|WindowFullscreenOnDisplay\|DisplayObserver"); fi
if ! skip perf; then
  echo "== perf (Views window against a DevTools window, $OUT, gpu=$GPU)"
  for pg in webgl2 canvas2d idle; do for m in views devtools; do
    timeout 120 python3 $T/perf.py --mode $m --gpu $GPU --page $pg --output $OUT --seconds ${SECONDS_PERF:-15} 2>&1 | grep '^{'
    pkill -x subprocess; sleep 3
  done; done
fi
echo "== toplevels left: $($W/usr/bin/wlrctl toplevel list | wc -l)"
