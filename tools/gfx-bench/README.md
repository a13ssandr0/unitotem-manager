# Graphics benchmarks and Wayland experiments

Scripts used on 2026-10-19/20 to measure the viewer's graphics path on a Raspberry Pi 4 (V3D) and on an Intel Alder Lake
laptop ("venus", Iris Xe). They run ON the target and talk to CEF over DevTools (`127.0.0.1:9223`), so they need the manager's
python (`websockets`). Nothing here is installed by a package; copy to `/tmp/vp` (probes) and `/tmp/wlt` (Wayland harness).

| script | what it measures |
| --- | --- |
| `raf.py x w h ['a|b']` | frame rate of a canvas 2D / WebGL 1 / WebGL 2 page that redraws the WHOLE canvas. Counts only frames really drawn and says `NO CONTEXT` when `getContext` returns null. **A separate `requestAnimationFrame` counter lies** (it kept saying 60 while WebGL was dead). |
| `ctxtest.py` | which `getContext()` calls succeed with the current switches |
| `glstress.py secs counts` | WebGL Aquarium with growing fish counts, native canvas size, fps + CPU + GPU/CPU counters (`hw.py`) |
| `glbench.html/.py` | WebGL 2 micro benchmarks (fragment shader, overdraw, instancing, texture sampling, MSAA) without the vsync cap |
| `vdec.py video x w h secs` | video decoder name, fps, dropped frames, tree CPU, GPU counters |
| `trace.py page secs` | Chrome trace summary (where a frame's time goes: swap, draw, vsync...) |
| `hw.py` | i915 throttle flags, GPU frequency, package power/temperature, CPU throttle counters |
| `wlcef.py` | standalone CEF (no Qt, no X11 parent) for Wayland/X11 comparisons: `WLCEF_EXTRA="k=v,k2=v2"` adds switches |
| `wl-run.sh` | starts `cage`, `labwc` or `weston` (`COMP=`) plus `wlcef.py` on the console, `stop` restores X and the manager |
| `native-wl.sh`, `client.sh` | native Wayland clients (`weston-simple-egl`) as a baseline of what the board can scan out |
| `labwc-rc.xml` | labwc rule that makes every window full screen and undecorated (without it the window is ~900x900) |

The compositors are not in the image: they were extracted from Debian trixie `.deb` files into `/tmp` with
`apt-get -o Dir::State=... -o Dir::State::status=<empty file> install --download-only cage labwc weston` and `dpkg-deb -x`
(only the packages missing from the device), so nothing is written to the root filesystem. `cage` looks for
`/usr/bin/Xwayland`: point `WLR_XWAYLAND` at the extracted binary.

Results and what they mean are in `CLAUDE.md` ("Graphics stack measurements, 2026-10-20") and in `TODO`.
