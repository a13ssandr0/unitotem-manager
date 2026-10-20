# The kiosk image on Wayland: what unitotem-system has to change

Decision of 2026-10-20: the kiosk runs **labwc (Wayland)** and no X server at all. The program side is done (branch `viewer-wayland`, see CLAUDE.md
"THE KIOSK RUNS WAYLAND"). This is the list for the image (`~/Sviluppo/unitotem-system`), which that project owns. Measured on a Pi 4 and on venus
(Intel Iris Xe): 60 fps canvas2d and WebGL at 1080p on both, no tearing, a window per output, outputs unplugged and plugged at run time, touch mapping, VNC,
screen power. Everything below was run on real boards with the transient unit of `tools/wayland/session.sh`, not yet from an image.

## Remove
- `start-xorg.service` (and its enablement, the `getty@tty1` mask stays: labwc takes VT1), `unitotem-xbackground.service` + `/usr/sbin/unitotem-xbackground`,
  `unitotem-manager-x.conf` (the `After=start-xorg` drop-in), `10-unitotem-noblank.conf` and `/etc/X11/xorg.conf.d`, `xinit`, `xserver-xorg*`, `x11-xserver-utils`
  (xset/xsetroot/xrandr) and the checks that keep them alive after the compiler removal, `xauth`, `/run/unitotem-x11.auth` everywhere.
- `unitotem-maintenance`: `UNITOTEM_UNITS="unitotem-manager.service start-xorg.service"` becomes `unitotem-manager.service unitotem-compositor.service`
  (compositor `Restart=always`, stop it with systemctl, never pkill).
- `verify-image.sh`: the X checks (lines about start-xorg, xset, noblank, 10-x11.conf, xbackground) become the checks below.

## Add
- Packages: `labwc wlrctl qt6-wayland wlr-randr wlopm wayvnc` (the debs of unitotem-viewer/unitotem-node now Depend on / Recommend them; `wayvnc` pulls ffmpeg libraries, the
  biggest cost), plus `xwayland` (labwc starts it lazily and logs an error without it; no X client is run). Mesa is already there. Remove `python3-xlib`.
- `unitotem-compositor.service` comes with the unitotem-viewer deb (disabled): **enable it** (`WantedBy=multi-user.target`). It does what start-xorg did: Plymouth
  `deactivate` before, plymouthd killed once the socket exists, VT1, `Restart=always`. `unitotem-splash.service` (viewer deb) waits for the same socket.
- `/etc/unitotem/xdg/labwc/rc.xml` comes with the viewer deb (base configuration). The manager unit needs the drop-in `debian/10-wayland.conf` (unitotem-node deb:
  `WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0` absolute, and `XDG_RUNTIME_DIR=/run/user/0`, see "Audio" below); replace `20-unitotem-x.conf` by one that orders the manager after the compositor and waits
  (up to 60 s) for `/run/unitotem/xdg/wayland-0` instead of `/tmp/.X11-unix/X0`.
- `/dev/uinput`: the viewer presses a key binding through a virtual keyboard to put each window on its output: `uinput` in `/etc/modules-load.d/` (it exists on the Pi and
  venus images today). Root runs everything, no group needed.
- `LIBSEAT_BACKEND=builtin` is set by the unit; there is no seatd/logind session. The compositor needs `/dev/dri/card*` and the input devices as root (it is).
- No cursor theme is needed; if a mouse is plugged in the pointer shows (labwc has no kiosk "hide cursor" without a touch-only config): decide if it matters.
- Pi: `dtoverlay=vc4-kms-v3d` is already there. 4Kp60 still needs `hdmi_enable_4kp60=1` in `[pi4]` (nobody decided; not Wayland related).

## The browser: new wheels
CEF patch `tools/cef-server/patches/0002-gbm-no-scanout-buffers-env.patch` (Chromium allocates LINEAR scanout buffers on the Pi's V3D and every frame is presented two vblanks late:
30 fps instead of 60). The viewer sets `UNITOTEM_NO_SCANOUT_BUFFERS` itself on the `pi` profile. It needs the rebuilt arm64 (and x64, same source) cefpython wheels:
`tools/cef-server/cef-rebuild.sh` then `tools/build-cef-wheels.sh start|fetch`, then `TARGET_ARCH=... tools/build-debs.sh`. Until then a board gets 30 fps from the stock wheel.

## Check list for verify-image.sh
- `unitotem-compositor.service` enabled, `labwc` and `wlrctl wlr-randr wlopm wayvnc` present, no `Xorg`/`xset`/`xrandr`/`xauth`.
- `/etc/unitotem/xdg/labwc/rc.xml` valid XML (a broken base file makes labwc silently use its defaults: look for "error parsing config file" in the journal).
- manager drop-in waits for `/run/unitotem/xdg/wayland-0`; `WAYLAND_DISPLAY` of the manager and of `unitotem-splash` is `/run/unitotem/xdg/wayland-0` (absolute); the manager has `XDG_RUNTIME_DIR=/run/user/0`.
- `wlr-randr --json` answers (`WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0`), `wlopm` lists the outputs.

## Behaviour that changed for users
Output names are DRM's (`HDMI-A-1`, was RandR's `HDMI-1`): saved playlist assignments, screen settings and timers follow by themselves. The Display page no longer moves a
window onto a clicked monitor (the compositor owns geometry). Two identical touch panels share one mapping (labwc matches by device name). VNC shows one output at a time.

## Audio (found 2026-10-20 on a Pi 4 with the dev5 image; applies to every image, X or Wayland)
PipeWire has never run for root on the image, so the manager's audio controls (pulsectl) and the browser's sound did not work at all:
1. Debian's `pipewire.socket`, `pipewire.service`, `pipewire-pulse.socket`, `pipewire-pulse.service` have `ConditionUser=!root` ("start condition unmet"), and the manager and the
   kiosk run as root. The image must override it for the user manager of root, for those four units and for `wireplumber.service`: `/etc/systemd/user/<unit>.d/10-root.conf`
   with `[Unit]` and an empty `ConditionUser=`. (root already has `user@0.service` and `Linger=yes`, and the units are enabled.)
2. The manager's unit had no `XDG_RUNTIME_DIR`, so neither libpulse in the browser nor in pulsectl looked in `/run/user/0/pulse/native`. The unitotem-node drop-in `10-wayland.conf` now sets
   `XDG_RUNTIME_DIR=/run/user/0`; the manager should also start after `user@0.service`.
Measured with both applied (volatile drop-ins on the Pi): `wpctl` shows the HDMI sink, `pw-play` and the browser (stream "Chromium", WebAudio oscillator) play into it, ALSA `RUNNING` owned by pipewire, pulsectl lists the sink, sets and reads back
the volume and the mute. Without 2 the page runs and no stream ever appears.
