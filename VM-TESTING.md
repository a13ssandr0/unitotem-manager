# Testing UniTotem Manager in a VM

The manager cannot be meaningfully tested on a development machine: it drives a
Qt6+CEF viewer on a bare X11 session with no window manager, controls
NetworkManager and systemd-hostnamed over DBus, and runs on a read-only kiosk
image. All of that only exists on the real target, so testing happens in a QEMU
VM booted from the same kiosk image that ships to devices.

This document is the whole procedure. Follow it instead of rediscovering it;
when a step turns out to be wrong, fix it here.

Related: `unitotem-system/debian-kiosk.md` documents the kiosk image itself.

---

## 0. What the setup looks like

| | |
|---|---|
| VM name | `unitotem-test` (libvirt, `qemu:///system`) |
| IP | **192.168.122.50**, fixed |
| Web UI | **https://192.168.122.50/** — real default ports, no forwarding |
| SSH | `root` / `unitotem` (key-based after bootstrap) |
| Login | `admin` / `admin` (created on first run) |
| Disk images | `/var/tmp/unitotem-vm/` |
| Keys, XML, logs | `unitotem-system/work/vm-test/` (gitignored) |

The VM gets a **real IP on libvirt's `default` NAT bridge**, not port forwarding.
That matters: the manager must keep its own default ports (80 redirecting to 443),
and remapping them on the host would mean testing a configuration nobody ships,
while also breaking any link shared with someone else.

Helper scripts live in `tools/`:

| | |
|---|---|
| `tools/vm-ssh [cmd]` | run a command in the VM (or open a shell) |
| `tools/vm-deploy` | rsync the working copy into the VM and restart the manager |
| `tools/ws-probe.py` | log in and watch/send WebSocket API frames |
| `tools/vm-env.sh` | shared settings, sourced by the above |

---

## 1. Prerequisites (host, once)

```bash
sudo usermod -aG kvm,libvirt "$USER"     # then log out and back in
sudo apt install qemu-system-x86 libvirt-daemon-system ovmf imagemagick
```

`virsh -c qemu:///system list --all` must work without sudo before continuing.

---

## 2. Create the VM

The base image is whatever `unitotem-system` last built for amd64:

```bash
SYS=~/Sviluppo/unitotem-system
IMG=$(ls -t $SYS/deploy/amd64/kiosk-*-amd64.img.xz | head -1)

mkdir -p /var/tmp/unitotem-vm && cd /var/tmp/unitotem-vm
xz -dc -T0 "$IMG" > kiosk-amd64.img
qemu-img create -f qcow2 -b kiosk-amd64.img -F raw overlay.qcow2 16G
cp /usr/share/OVMF/OVMF_VARS_4M.fd OVMF_VARS.fd
```

Everything runs against `overlay.qcow2`, so the decompressed base image is never
modified and a broken VM is thrown away with `rm overlay.qcow2` + the
`qemu-img create` line above.

> **Images must live outside your home directory.** `qemu:///system` runs QEMU as
> `libvirt-qemu`, which cannot traverse `/home/<user>` (mode 0750), and the VM
> fails to start with a permissions error that does not mention the home directory
> at all. `/var/tmp` is world-traversable and on the big filesystem.

Define the domain and pin its address (the XML is versioned with this repo):

```bash
virsh -c qemu:///system define tools/unitotem-test.xml
virsh -c qemu:///system net-update default add ip-dhcp-host \
  "<host mac='52:54:00:11:70:01' name='unitotem-test' ip='192.168.122.50'/>" \
  --live --config
virsh -c qemu:///system start unitotem-test
```

The static address is a DHCP reservation on libvirt's own dnsmasq, keyed on the
MAC in the domain XML — nothing has to be configured inside the guest.

The domain declares `<video model='virtio' heads='2'/>` (i.e.
`virtio-vga,max_outputs=2`), which is what makes the multi-monitor test in §6
possible. It costs nothing when unused: the second connector starts disconnected.

### First login and SSH key

```bash
cd $SYS/work/vm-test
SSH_ASKPASS_REQUIRE=never sshpass -p unitotem ssh-copy-id -i vm_test_key.pub \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=known_hosts root@192.168.122.50
tools/vm-ssh hostname          # must print 'unitotem'
```

> **ssh must never be allowed to open a password window.** On a KDE desktop a
> missing key, an unknown host key or a `sshpass` invocation makes ssh spawn
> `ksshaskpass`, which steals focus and blocks any automation until somebody
> clicks it. `tools/vm-env.sh` exports `SSH_ASKPASS_REQUIRE=never` and passes
> `-o BatchMode=yes` for exactly this reason; use the helpers rather than bare
> `ssh`, and if you must call ssh directly, set that variable yourself.

---

## 3. One-time bootstrap of the new stack

The kiosk image installs `unitotem-manager` from the published APT repository,
which is still the **old 2.3.0 Electron-era package**: no venv worth using, an
entry point at `/usr/bin/unitotem-manager`, and haproxy fronting ports 80/443.
The current stack has to be put in place once.

Nothing below writes to `/usr` except §3.2, which is unavoidable (shared libraries
have to be where the dynamic linker looks). Everything else goes to `/var`, which
is an overlay on the DATA partition — writable and persistent — so ordinary code
deploys never need to remount anything.

### 3.1 Make room on DATA

DATA ships at 1 GB, which does not fit the venv, let alone uploaded videos. The
disk has spare space because the overlay was created larger than the image:

```bash
tools/vm-ssh 'echo ", +" | sfdisk -N 4 --no-reread --force /dev/vda; partx -u /dev/vda; resize2fs /dev/vda4'
```

`sfdisk` complains that re-reading the partition table failed (the disk is in
use) — that is expected; `partx -u` is what actually updates the kernel, and
`resize2fs` grows the mounted filesystem online.

### 3.2 Install the runtime dependencies the image lacks

```bash
tools/vm-ssh 'mount -o remount,rw / && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    libxcb-cursor0 libxcb-keysyms1 libxcb-icccm4 libxcb-shape0 \
    libdbus-1-dev libglib2.0-dev rsync && \
  sync && mount -o remount,ro /'
```

Do **not** use `kiosk-update enter` / `exit` for this: `exit` reboots the VM and
regenerates initramfs, neither of which a plain package install needs. A manual
remount pair is faster and leaves the root read-only again immediately.

> **Missing Qt xcb libraries produce a completely misleading error.** With any of
> them absent, Qt aborts the whole process (SIGABRT, uncatchable) printing
> *"From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed"* — it names
> `libxcb-cursor0` even when that one is installed and the actually-missing
> library is `libxcb-icccm4` or `libxcb-shape0`. Never chase the message; ask the
> linker instead:
> ```bash
> tools/vm-ssh 'ldd /var/unitotem-venv/lib/python3.11/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"'
> ```

`rsync` is installed here too. It is not in the kiosk image, and `tools/vm-deploy`
needs it on both ends.

### 3.3 Put the venv in place

The venv holds PySide6 and the locally built amd64 `cefpython3` wheel, so it is
taken from a built package rather than assembled by hand. Any recent
`unitotem-manager_*_amd64.deb` will do — it is only used for its venv:

```bash
DEB=~/Sviluppo/Python/unitotem-manager_2.3.0_amd64.deb
dpkg-deb -x "$DEB" /var/tmp/unitotem-vm/deb-extract

. tools/vm-env.sh
rsync -a --delete --rsync-path="$VM_RSYNC_PATH" -e "ssh $VM_SSH_OPTS" \
  /var/tmp/unitotem-vm/deb-extract/var/unitotem-venv/ \
  root@192.168.122.50:/var/unitotem-venv/
```

Rebuild the `.deb` only when `manager/requirements.txt` or the CEF wheel changes
(see §8) — never as part of a normal code change.

### 3.4 Point systemd at the deployed working copy

```bash
sed -e 's|^WorkingDirectory=.*|WorkingDirectory=/var/unitotem-manager|' \
    -e 's|^ExecStart=.*|ExecStart=/var/unitotem-venv/bin/python3 /var/unitotem-manager/init.py|' \
    debian/unitotem-manager.service > /tmp/unitotem-manager.service

tools/vm-ssh 'mkdir -p /var/unitotem-manager'
. tools/vm-env.sh
rsync -a --rsync-path="$VM_RSYNC_PATH" -e "ssh $VM_SSH_OPTS" \
  /tmp/unitotem-manager.service root@192.168.122.50:/etc/systemd/system/
rsync -a --rsync-path="$VM_RSYNC_PATH" -e "ssh $VM_SSH_OPTS" \
  debian/unitotem-manager.sudoers root@192.168.122.50:/etc/sudoers.d/unitotem-manager

tools/vm-ssh 'chown root:root /etc/sudoers.d/unitotem-manager && chmod 440 $_
              systemctl disable --now haproxy
              systemctl daemon-reload'
```

The unit in `/etc/systemd/system` takes precedence over the old package's copy in
`/lib/systemd/system`, so the old one can simply be left alone.

> **`rsync -a` preserves the source's numeric owner.** Files it drops into
> `/etc/sudoers.d/` therefore arrive owned by uid 1000, and `sudo` silently
> ignores — in fact refuses to parse — any file there not owned by root. Always
> `chown root:root` after rsyncing into `/etc`.

haproxy has to go: it holds 80/443 and the manager binds them itself.

---

## 4. The normal loop: deploy and test

```bash
tools/vm-deploy                 # rsync + restart, ~1s
tools/vm-deploy --no-restart    # rsync only
```

This is the whole iteration. **Do not build a Debian package to test a code
change** — that is a multi-minute detour through a Docker container and only
belongs in §8, when the packaging itself is what is being tested.

> **After `npm run build`, `manager/static/manifest.json` must be deployed with
> `manager/static/assets/`.** `manager/templates/base.html` resolves the hashed
> bundle names through the manifest at render time, so a stale manifest makes the
> server keep serving the previous JS with no error anywhere — the fix simply
> appears not to work. `tools/vm-deploy` syncs the whole of `manager/static/` and
> refuses to run if the manifest is missing.

### Logs

```bash
tools/vm-ssh 'journalctl -u unitotem-manager -n 50 --no-pager'
```

> **journald throws away most of the manager's output.** The image sets
> `MaxLevelStore=warning`, so INFO/DEBUG/SUCCESS — nearly all of loguru's output —
> never reaches the journal, and `journalctl -u unitotem-manager` can read
> `-- No entries --` while the service is logging busily. To see everything,
> including a traceback behind a startup failure, stop the unit and run it by hand:
> ```bash
> tools/vm-ssh 'systemctl stop unitotem-manager
>   cd /var/unitotem-manager && DISPLAY=:0 XAUTHORITY=/run/unitotem-x11.auth \
>   timeout 40 /var/unitotem-venv/bin/python3 init.py > /var/tmp/manager.log 2>&1
>   tail -40 /var/tmp/manager.log'
> ```

### Seeing the screen

```bash
virsh -c qemu:///system screenshot unitotem-test /var/tmp/unitotem-vm/shot.ppm
magick /var/tmp/unitotem-vm/shot.ppm /var/tmp/unitotem-vm/shot.png
```

This dumps the actual framebuffer, so it works at every stage — Plymouth, X, the
viewer — and needs no VNC client. To catch something transient (a boot splash, a
flash on window creation) start a capture loop **before** triggering the event;
`virsh screenshot` paces itself at roughly 3 frames per second, which is enough
for anything lasting about a second but will miss a single-frame artefact.

The serial console is wired to `/var/tmp/unitotem-vm/serial.log` and holds
everything from the kernel and systemd — it is the only diagnostic available when
the VM does not get far enough to run sshd.

### The API, without a browser

```bash
.venv/bin/python tools/ws-probe.py --send Viewers/list --count 1
.venv/bin/python tools/ws-probe.py --watch Viewers/list --timeout 30
```

The second form is how the push tests are verified: leave it watching, cause a
change, and see whether the backend pushed a frame on its own.

---

## 5. Leave it running

When a test session ends, **leave the VM and the manager running** on their
default ports, so the web UI stays available to whoever wants to try it. Shut
anything down only when explicitly asked:

```bash
virsh -c qemu:///system shutdown unitotem-test    # only when asked to
```

---

## 6. Multi-monitor hot-plug

The domain already exposes a second output (`Virtual-2`), disconnected at boot.
The way to connect it is the DRM connector's debugfs `force` file — a generic
DRM-core feature, not virtio-specific, and the real source of truth:

```bash
tools/vm-ssh 'export DISPLAY=:0 XAUTHORITY=/run/unitotem-x11.auth
  # plug: fires a real DRM/udev hot-plug event
  echo on > /sys/kernel/debug/dri/0/Virtual-2/force
  # wait for X to re-probe, THEN assign a mode
  for i in $(seq 1 20); do xrandr --query | sed -n "/^Virtual-2/,\$p" | grep -q 3840x2160 && break; sleep 1; done
  xrandr --output Virtual-2 --mode 3840x2160 --right-of Virtual-1'

tools/vm-ssh 'export DISPLAY=:0 XAUTHORITY=/run/unitotem-x11.auth
  # unplug: the CRTC has to be torn down too, exactly like real hardware -
  # a presence signal alone does not stop the video output
  xrandr --output Virtual-2 --off
  echo off > /sys/kernel/debug/dri/0/Virtual-2/force'
```

Watch the effect with `tools/ws-probe.py --watch Viewers/list` running in another
shell: a frame must arrive within about a second of each change, with the new
screen's geometry matching the mode just assigned, and `window_id` values that
increment and are never reused.

> **After `force on`, X needs a moment to re-probe before the mode list exists.**
> Assigning a mode immediately fails with `xrandr: cannot find mode ...`, and
> because the output then never becomes active, no event reaches the manager and
> it looks like the manager missed the hot-plug. Poll `xrandr --query` until the
> mode appears, as above. `/sys/class/drm/card0-Virtual-2/status` is not a usable
> signal either: it is the raw EDID probe and keeps saying `disconnected` even
> after a successful force. Trust `xrandr --query`.

> **SPICE cannot do this — do not try again.** Flipping a connector's state
> through SPICE requires `spice-vdagent`'s "monitors config" negotiation, and
> `spice-vdagentd` refuses to run without a valid logind session, which this
> image (X started by `start-xorg.service`, no display manager) does not have.
> Creating a synthetic logind session did not satisfy it either. Also dead:
> `xrandr --auto` (no EDID), RandR 1.5 `--setmonitor` (Qt filters strictly on an
> output's `Connected` bit), the `-display dbus` backend, and QMP (no command
> exists for virtio-gpu output state).

`Virtual-1` can be forced off the same way, which is how the zero-screen
behaviour is exercised.

---

## 7. The viewer checklist

What to re-run after touching `manager/webview/` or anything that feeds it:

1. **It starts at all.** Service active, CEF subprocesses alive
   (`pgrep -c -f cefpython3/subprocess`), boot screen on the framebuffer.
2. **Ports.** `https://192.168.122.50/` answers and `http://` redirects to it.
3. **Hot-plug / hot-unplug** as in §6, verified through pushed `Viewers/list`
   frames rather than by re-querying.
4. **Zero screens.** Force every connector off: the manager must stay up and the
   web UI must stay fully usable.
5. **No white flash** when a window is created — capture during a restart (§4).
6. **Shutdown.** `systemctl stop unitotem-manager` must return in seconds, not
   hang until systemd's timeout kills it.
7. **Playback**, once assets are scheduled: image, video and web page; `fit`
   contain/cover/fill; `bg_color`; per-asset volume.

## 8. Rebuilding the Debian package (rare)

Only when `manager/requirements.txt` or the CEF wheel changes, or when the
packaging itself is being tested. It must happen in a `debian:bookworm`
container — the VM is Python 3.11, while the dev host is far newer, and a venv
built against the wrong interpreter will not even start on the target. The image
`bookworm-build:latest` already exists on the dev host; `vendor/cefpython-wheels/`
holds the locally built amd64 wheel, since the fork's CI only publishes arm64.

---

## 9. Known limits of this VM

- The kiosk image's root filesystem is read-only by design. `apt-get dist-upgrade`
  therefore cannot work on it at all (real devices update over OTA), so the
  Updates page can only be exercised meaningfully on a headless/server install.
### GPU acceleration

The guest currently runs on `llvmpipe` (`glxinfo -B` reports `Accelerated: no`,
and the kernel logs `[drm] features: -virgl`), so CEF rasterises in software.
That is a real difference from a device in the field and worth removing, but it
is blocked on the host, not on the domain XML.

What is needed, and what is already done:

- `<acceleration accel3d='yes'/>` on the video model, which turns the device
  into `virtio-vga-gl`, plus a `<graphics type='egl-headless'>` device to give
  virgl the OpenGL context that plain VNC does not have. Both are written up in
  `tools/unitotem-test.xml` and were verified to be stored correctly by libvirt.
- `libvirt-qemu` must be able to open the host's render node:
  `sudo usermod -aG render libvirt-qemu`. Done.

What still blocks it: QEMU refuses to start with `egl: render node init failed`.
`/dev/dri/renderD128` on this host is an NVIDIA card on the proprietary driver,
and QEMU's egl-headless needs working EGL+GBM on it. `libnvidia-egl-gbm` and
`15_nvidia_gbm.json` are both installed, so the remaining suspect is
`nvidia_drm.modeset`, which could not be read (the parameter is root-only) and
which requires a kernel parameter plus a host reboot to change.

Until that is settled the domain keeps `accel3d` off deliberately: enabling it
without the host-side prerequisite does not degrade to software, it makes the
domain fail to start altogether.

Note also `virsh undefine --nvram` deletes the domain's `OVMF_VARS.fd`, so an
undefine/define cycle loses the UEFI variables; recreate it from
`/usr/share/OVMF/OVMF_VARS_4M.fd` before starting, or use `--keep-nvram`.
