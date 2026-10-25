# Wayland test tooling

The kiosk runs labwc (Wayland) only; see "THE KIOSK RUNS WAYLAND" in CLAUDE.md. This directory is for trying code on a board without
building an image.

`session.sh start|stop` (root, from ssh) runs the REAL `unitotem-compositor.service` (a copy of `debian/unitotem-viewer.unitotem-compositor.service`
plus a drop-in that points it at a development tree) and the manager (`init.py` of a copy of the code, as the transient unit `unitotem-wl-test`). `stop` brings the
board's normal session back. It expects `$W` (default `/var/lib/unitotem/wl`) with:

- `usr/`, `extra/`: the Debian trixie debs of `labwc`, `wlrctl`, `wlr-randr`, `wlopm`, `wayvnc`, `qt6-wayland` (and `xwayland`, labwc wants it) extracted with `dpkg-deb -x`
  for the board's architecture, and in `extra/` the shared libraries they need that the board does not have (every `.so` of their `usr/lib/<arch>` whose name is missing
  under the system's `/usr/lib/<arch>`). Download them with a trixie container: `apt-get install --download-only labwc:<arch> ...` after `dpkg --add-architecture <arch>`.
- `code/`: a copy of the `manager/` tree (with `static/` built by `npm run build`, and `plugins/unitotem_viewer/static/bootsplash/` from `plymouth/unitotem/*.png`).
- `unitotem-compositor.service`, `labwc-rc.xml`: the files of `debian/` (`labwc-rc.xml` is also copied to `/usr/share/unitotem/xdg/labwc/rc.xml`).
- the patched `libcef.so` (CEF patches in `tools/cef-server/patches`) bind-mounted over the system's, until a wheel has it.

`gbm-noscanout.c` is the LD_PRELOAD proof of the V3D finding (linear scanout buffers), kept for reference. The benchmarks and tracers are in `tools/gfx-bench/`.
