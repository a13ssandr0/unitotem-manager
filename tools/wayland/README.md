# Viewer on Wayland (experimental, branch `viewer-wayland`)

`run-viewer.sh start [labwc|cage]` runs the real manager + viewer on a Wayland compositor on a test machine (root, from ssh; stops X and the
service; `stop` brings the normal session back). It expects `$W` (default `/var/lib/unitotem/wl`) with:

- `usr/`, `extra/`: the Debian trixie debs of `labwc`, `cage`, `qt6-wayland`, `wlrctl` (and `xwayland`) extracted (`dpkg-deb -x`) for the
  target architecture, plus in `extra/` the shared libraries they need that the image does not have (everything in their
  `usr/lib/<arch>` whose name is missing under the system's `/usr/lib/<arch>`). Download them with a trixie container:
  `apt-get install --download-only labwc:<arch> cage:<arch> qt6-wayland:<arch> wlrctl:<arch>` (multiarch, `dpkg --add-architecture`).
- `code/`: a copy of `/usr/share/unitotem-manager` with the changed files of `manager/plugins/unitotem_viewer/` on top.
- `labwc/rc.xml`: written by the viewer itself (see `wayland.py`), any valid file to start with.
- optional `WLEXTRA="VAR=1 ..."` (env of the viewer, e.g. `WAYLAND_DEBUG=1`, `UNITOTEM_CEF_SWITCHES=name=value`) and `LABWC_ARGS=-d`.

Other files: `gbm-noscanout.c` (LD_PRELOAD proof of the V3D finding, see CLAUDE.md), the benchmarks are in `tools/gfx-bench/`.
