You are working on **UniTotem Manager** a kiosk program for linux, from now on called simply unitotem.

Unitotem is made of three key components working together:
- scheduler: the key part of the program, handles assets scheduling from one or more playlists
- management ui+backend: a user friendly and simple but powerful web ui that controls all the settings through a 
websocket-based API
- viewer: a chromium based fullscreen frameless window with a custom page that can handle several file formats

## Scheduler
The core of unitotem is the scheduler, it allows both local files and content urls.
Local files are uploaded through the web ui and are referenced through a special prefix `file:` resembling an url scheme,
it only references files directly in the uploads folder, does not allow subfolders and most importantly upper folders.

There may be multiple instances of the scheduler, exactly one for each playlist created, each scheduler broadcasts
when to change asset to the corresponding local windows and to all connected clients, a single scheduler might 
control multiple windows both local and remote.

Each instance of the scheduler must be able to set a default asset duration that applies only assets withouh an intrinsic
duration (yes to web pages and images, no to videos and audios).

Each instance of the scheduler must be able to select the output audio device and volume based on a per-window policy, if a 
scheduler control multiple windows both locally and remotely each window must be able to send its audio to the specified 
device, a 'default' selector must be included to let the system decide.
Volume control (included mute) must be provided per-asset (only for assets supporting it), this is independent from
system level volume control.

Each asset supports the following additional properties:
- `fit`: how the asset fills the viewer area — contain (default), cover, fill (CSS object-fit semantics)
- `bg_color`: background color shown behind the asset (e.g. for images with transparency or letterboxed video)
- `ena_date` / `dis_date`: optional datetime to schedule automatic enable and disable of the asset

The uploads manager provides a download button for individual uploaded files.

## Management
Each device running unitotem (also called `node`) provides a web page via https (http port 80 is open and redirects to
port 443). The web page is written in vue an uses websockets to connect to the backend bidirectionally.

In the top bar of the page the Unitotem logo followed by the hostname of the device, on the right theme switcher and
reboot/shutdown/logout controls.
A dropdown menu with optional badge should contain system notifications.

### Login
When connecting to the device running unitotem, a login page asks for username and password and allows saving login
for a week. The login page shows device hostname, ip and OS version to immediately check if we are connected to the right
device.

### Scheduler
Root page of the program. Made of two main parts: the playlists manager and the uploads manager.
The playlists manager has a selector to choose the playlist to edit and a button to add a new playlist.
Contains a table that shows info about the assets and provides access to quick controls, assset edit and supports drag
and drop reordering.
The uploads manager shows uploaded files with their names, sizes and optionally duration, provides checkboxes for multiple
selections and controls to delete, download or add the file(s) to the playlist.

### Viewers
A dedicated page (route `/viewers`) that shows all connected viewer windows grouped by host and allows assigning a
playlist to each window. This is how the output of a scheduler instance is routed to specific displays.

### Volume control
Global device volume must be controlled too, previously we discussed asset and window volumes, now we are talking about
the audio device itself, this control requires a different ui from the scheduler volumes and must allow selecting default
output device.

### Remote control
Manages client/server modes. By default, out-of-the-box, it must be in server mode (standalone mode is a subset of server
mode without any client attached, each standalone node can become a server at any time).
In server mode the configuration page must display the list of connected and known clients (known clients are paired
clients not currently connected, maybe because offline) and provide buttons to enable/disable clients temporarily,
to unpair the client and to open its configuration page.

In client mode, an unpaired client broadcasts a special packet that allows servers to discover it, through the web ui
servers must highlight discovered clients and propose adoption, meanwhile both clients and servers must provide a 
manual way of start pairing by requesting the user to enter the ip of the other party and optionally the port.
Adoption must require approval of the other party, except for unpaired clients (similar to ubiquiti device adoption).
Adopting a client with preconfigured playlists requires moving playlists and uploads from client to server during pairing and restoring
the connection between the playlists and the client it came from; unpairing a client requires transfering playlists and uploads
to the soon-to-be standalone node, playlists and uploads previously referenced ONLY from unpaired device must be removed
after transfer, playlists and uploads referenced also by other nodes must remain untouched.

Client and servers connect through a websocket over https and mutual authentication must be implemented through 
certificates exchanged during pairing phase and surviving through the entire life of the installation. Certificates
must be part of the backup and must prevent the confidentiality and integrity of the websocket "conversation":
intermediate proxies performing ssl strip to spy on the traffic from the server or trying to display unwanted assets on 
the client will provide a different certificate, connections with unkwnown certificates must be immediately rejected.

### Security
Must provide user and capabilities management. Each user can be assigned one or more of the following capabilities:
- scheduler (control asset scheduling (including per-asset volume) and file upload)
- audio (allows changing screen volume and audio device volume) — referred to as "volume" in earlier specs
- power allows rebooting/powering off the node
- admin (implies all of the above and allows changing every other settings)

There must be at least one admin account in the system and no "suicidal" actions should be allowed:
- no user can delete itself
- no admin can remove their own admin role (unconditionally, even if other admins exist)

Each user must be able to change its password.

### Network
Network management must be allowed through a page that replicates KDE network manager gui and controls
NetworkManager via DBus ensuring netplan is used as backend (renderer: NetworkManager in netplan config).
Hostname change is performed via the `org.freedesktop.hostname1` (systemd-hostnamed) DBus service, which also
keeps `/etc/hostname` in sync; `/etc/hosts` (the `127.0.1.1` entry) must be updated accordingly.

### Display
Must control orientation and flip of connected displays, an overview of the current monitor layout (position and rotation)
must be provided at the top of the view, each screen will have its independent viewer window so for each
window there must be controls to change content orientation (like screen rotation but without affecting it) and to change
content flip (ex: for backwards projection).

### Timers
Based on user crontab allows setting reboot/poweroff timers.

### Updates
A simple frontend for apt-get update and apt-get dist-upgrade -y.
A periodic task should run in background every day to check for updates and display an hint in the navbar in the webui.
The webui must provide a xterm.js console to show update/upgrade logs. Logs of the last run must be cached for the entire
lifecycle of the program, its ok to discard them as soon as another update/upgrade task is run or if the program exits

### Backup/Restore
Must provide and easy way to backup, restore and reset unitotem configuration.
Each one of the controls must allow selection of the parts to backup/restore/reset.
Certificates for client/server authentication must be saved/restored if that part of the configuration is selected to
be backed-up/restored.

### Info
Displays system metrics collected from many sensors:
- System uptime
- CPU usage
- RAM usage
- Disk partitions and usage
- Temperature sensors (if available)
- Fans (if available)
- Batteries (if available)

This data must be broadcast every 3 second to every logged-in user.


## Viewer
Exactly one per-screen, the program must detect at all times the number of screens connected and add/remove windows
accordingly, if a new screen is connected it must be uniquely identified, registered in the system and a window
must be associated to it but no playlist should be connected to that window automatically.
If a screen is removed the corresponding window must be closed, the playlist outputting to that window must be unaware of 
the change.

The program must keep running normally with zero screens connected, both at startup and
after the last connected screen is disconnected at runtime — the management UI, scheduler
and API must remain fully reachable and functional regardless of screen count.

Screen/window state changes must reach the web UI purely through events, with no polling
anywhere along the chain: the update must be pushed the moment the change is detected, using
the same message the web UI already uses at initialization, so an already-open page reacts
without needing to re-request anything.


# Running
Running the program must allow the following modes:
- standalone
- headless (for servers running on independent machines)

At startup program must check for configuration files presence and for internet connection:
if no configuration file is present this is the first run after installation/reset, shows welcome screen,
if internet is also not available starts a hotspot and displays SSID and password on the welcome screen together with a
QR code, if possible configuration page should be advertised as captive portal


# Rules

Everything above describes *what* UniTotem must do. This section describes *how* to work on it.
These rules are binding.

## Language
All code, comments, identifiers, log messages, commit messages and repository documentation are
written in **English**, without exception. This holds even when the conversation itself is in
another language.

## Progress tracking
Project status and planning live in the `TODO` file at the repository root, not only in commit
messages. It is structured as:

- `=== CURRENT WORK ===` — what is being worked on *right now*. Always kept at the top so the next
  session immediately sees where the previous one stopped, with enough detail to resume without
  re-deriving the context (what was verified, what is still open, which files are involved).
- `=== NEXT ===` — the planned next steps.
- `=== BACKLOG ===` — the long-standing wish list. Entries already done are kept, commented out
  with `<!-- -->`; never delete them, they are the project's history.

`TODO` is updated *during* the work, not only at the end: when a task is picked up, when something
is verified, and when the session stops.

## Gotchas and new rules
Every gotcha discovered while working (a non-obvious environment behaviour, a dead end that must
not be retried, a trap that cost real debugging time) and every new rule given by the user is
written into this file, in this section, as soon as it is found. A commit message is not enough:
commit messages are not read at the start of the next session, this file is.

System- or OS-level changes (kiosk image, systemd units, X11 session, packaging on the target)
additionally go into `unitotem-system/debian-kiosk.md`, which is the reference document for the
kiosk image.

## Running the project
- **Never change the default ports.** The manager listens on 80 (redirect) and 443. Do not move it
  to 8080/8443 "just for a test" — the user connects to the same instance and a moved port silently
  breaks their session. In the test VM the remapping is done by QEMU's port forwarding on the host
  side, which leaves the guest-side ports untouched.
- **Leave it running.** After a test session the manager stays up and reachable, so the user can
  try it out. Shut it down only when explicitly asked to.

## Committing
Commit and push after every completed piece of work — no need to be asked. Keep commits coherent
(one concern each) and their messages in English.

## Testing
Testing happens in the QEMU test VM, which runs the real kiosk image. **The full procedure is in
`VM-TESTING.md`** — create the VM, boot it, deploy, test multi-monitor, and every known trap. Read
it before doing anything with the VM instead of rediscovering it. The essentials:

- **Deploy with `rsync`, not with a Debian package.** Building a `.deb` is a multi-minute detour
  through a Docker container; the normal iteration is rsync the code into the VM and restart the
  service. Build a package only when the packaging itself is what is being tested.
- **Keep the number of steps minimal** for every test, unless the user explicitly asks for the long
  path. Prefer the shortest route that still exercises the real thing.
- **SSH into the VM as `root`, password `unitotem`** (a key is set up to avoid repeated password
  prompts). The VM's serial console is also wired to a file and is the way to debug anything that
  happens before or below the point where SSH works.
- **Multi-monitor hot-plug** is done with `-device virtio-vga,max_outputs=2` plus forcing the DRM
  connector from debugfs (`echo on > /sys/kernel/debug/dri/0/Virtual-2/force`). **SPICE does not
  work for this and must not be retried** — `spice-vdagentd` refuses to run without a logind
  session, which this kiosk's X11-without-display-manager design does not have. See `VM-TESTING.md`
  for the full list of dead ends.

## Gotchas

Each of these cost real debugging time at least once. `VM-TESTING.md` has the fuller version of
the VM-specific ones.

- **ssh must never be able to prompt graphically.** On KDE, a missing key or an unknown host key
  makes ssh spawn `ksshaskpass`, which steals focus and blocks automation until a human clicks it.
  Always run with `SSH_ASKPASS_REQUIRE=never` and `-o BatchMode=yes` — `tools/vm-env.sh` does both,
  so prefer `tools/vm-ssh` over calling ssh directly.
- **A missing Qt xcb library reports the wrong library.** Qt aborts with an uncatchable SIGABRT
  saying *"xcb-cursor0 or libxcb-cursor0 is needed"* regardless of which xcb library is actually
  absent. Ask the linker instead: `ldd .../PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"`.
- **`rsync -a` into `/etc/sudoers.d/` produces a file sudo refuses to parse**, because it preserves
  the source's uid. Always `chown root:root` afterwards.
- **A web UI tab left open across a deploy stops navigating, and it is not a bug.** The bundle
  names are content-hashed and `tools/vm-deploy` rsyncs with `--delete`, so the old chunks are
  gone; a tab still running the previous `main` chunk asks for a lazy-loaded page that no longer
  exists and the click appears to do nothing, with no visible error. Reload the page (Ctrl+Shift+R)
  before concluding anything is broken. A quick way to check the real state without a browser is
  to drive CEF's own DevTools endpoint on the VM (`http://127.0.0.1:9223/json/new?<url>`, then
  `Runtime.evaluate` over the target's WebSocket) — that is how this was diagnosed.
- **A stale `manager/static/manifest.json` silently serves the previous JS bundle.** `base.html`
  resolves hashed bundle names through the manifest at render time, so deploying `assets/` without
  it means the change appears not to work, with no error anywhere.
- **journald on the kiosk image discards INFO and below** (`MaxLevelStore=warning`), so
  `journalctl -u unitotem-manager` can show nothing at all while the service logs busily. Run the
  entry point by hand to see real output.
- **After forcing a DRM connector on, X needs time to re-probe before it has a mode list.**
  Assigning a mode immediately fails with `xrandr: cannot find mode`, the output never activates,
  and it looks like the manager missed the hot-plug event. Poll `xrandr --query` first.
- **CEF paints opaque white before a document loads**, at both application and per-browser level,
  which is a full-screen flash on a kiosk. Both settings are now pinned to `0xFF000000`, alongside
  a black Qt palette on the window and its widget (`manager/webview/window.py`).
- **The manager sitting near a full core on an idle kiosk is software rendering, not a runaway
  loop.** Measured on the test VM: the backend alone (`--no_gui=true`) costs 0% of a core, so all
  of it is the viewer; the CEF pump interval makes no difference (84% at 10ms, 85% at 50ms)
  because CEF does its UI-thread work *inside* `MessageLoopWork` — asking less often just makes
  each call do more; and setting `QT_NO_GLIB` before the process starts rather than at runtime
  changes nothing either. `Settings/Display/getGPUFeatureStats` reports
  `gpu_compositing: disabled_software` and `rasterization: unavailable_off`, i.e. Chromium is
  compositing every pixel on the CPU. Do not go looking for a busy loop; the number is expected
  wherever there is no working GPU, and only means something when compared before and after a
  change on the same machine.
- **The "re-plugging a screen makes the viewer burn a core" story was the wrong model. It is the
  boot screen animating, and hot-plug only had anything to do with it because a new screen gets a
  new window with no playlist assigned, which therefore stays on the logo.** `window_id` increments
  and is never reused, so after a couple of plug cycles no new window matches anything in
  `viewer_assignments.json` and every one of them lands on `boot-screen.html` — which is why the
  first plug of a session could look quiet and later ones did not. Settled by three measurements,
  each on the software-rendered test VM at 5120x2160, whole process tree:
  - **Control, standalone CEF** (raw Xlib window, no Qt, none of the manager's code —
    `/var/tmp/cefctl.py` on the guest): a genuinely static page costs **0.0%**, the shipped
    `boot-screen.html` costs **178.6%**, and the same page with `document.getAnimations()
    .forEach(a => a.pause())` costs **0.5%**. CEF is not the problem; the page is.
  - **In the manager, same window**: 31.9% while showing the logo, 1.4% the moment `show()` puts
    content in the iframe, 32.2% back on the logo again — same screen, same re-plug history.
  - **Which animation**: pausing both took the tree from 207% to 1.6%; the `text-shadow` `glow`
    alone accounted for 203.8% and the ring's `rotation` for 58.7%.
  Fixed in `9977db2` by animating `opacity`/`transform` instead of paint properties and stepping
  the frame rate: 178.6% → 27.5% at 5120x2160 and 38.5% → 6.9% at 1280x800 for the page, 187% →
  31% for a hot-plugged screen in the manager. Then in `486e67f` by not showing that page at all
  on a window with no playlist — it is idle, not booting — which takes an unassigned screen to
  1.2–1.3% at any size. The renderer leak behind "6 renderers for 2 windows" was a separate
  defect, fixed in `4c96a0f`.
- **When a CSS rule appears not to apply, ask `document.getAnimations()` / the computed style
  rather than re-reading the sheet.** `9977db2` added `.halo::before { content: none; }` to stop a
  duplicated element from generating a spinner ring; it has exactly the same specificity as the
  `.loader::before` that creates the ring and, being written earlier, silently lost on source
  order. The duplicate ring sat pixel-exact on top of the real one, so nothing looked wrong — only
  `getAnimations()` returning three entries where two were expected showed it (fixed in `1f3a485`
  with `.loader.halo::before`).
- **What is left after that fix really is software compositing, and no CSS can remove it.**
  With no GPU, *every* animated frame re-composites the whole window surface, so the residual cost
  is (animated layers) × (surface area) × (frame rate) and lives in the GPU process'
  `VizCompositorThread`, not in the renderer. Measured floors at 5120x2160: one small
  compositor-animated ring and nothing else still costs 51.9% at 60fps and 18.1% at 20fps. Do not
  go hunting for a repaint loop in that number — cut frames or surface, or get a GPU.
  Earlier measurements with a GTX 1060 passed through (115% at 3840x2160 on nouveau, ~60% on the
  proprietary driver, against 161% at 5120x2160 on llvmpipe) are consistent with this: hardware
  only made the same wasted animation cheaper. **The two together are what actually fix it**:
  re-measured on `f447c9f` with the GPU passed through and the proprietary driver, four
  unplug/re-plug cycles at 3840x2160 cost **6.7–8.2% of a core** with the process count flat at
  8 / 2 renderers — against ~60% for the same test with only the renderer-leak fix, and 161%
  where this started.
- **Measure this kind of thing with `/proc/<pid>/stat` deltas over the whole process tree.**
  `ps pcpu` is a lifetime average and will report a quiet number for a process that started
  burning a core a minute ago. The load also sits in CEF *subprocesses*, not in the Python
  process, so measuring the main pid alone reports ~1% while the tree is at 160%.
- **The shipped cefpython3 wheel has no proprietary codecs, and the only fix is rebuilding CEF
  from source.** `canPlayType` says no to `avc1` (H.264), `mp4a.40.2` (AAC), HEVC, AC3/EAC3 and
  Theora, and an H.264 MP4 fails with `DEMUXER_ERROR_NO_SUPPORTED_STREAMS: FFmpegDemuxer: no
  supported streams`. VP8, VP9, AV1, Opus, Vorbis, MP3, FLAC and WAV all work. The cause is that
  the wheel is built on the **Spotify prebuilt CEF binary distribution** (`tools/download_cef.py`),
  which is built with Chromium's default `ffmpeg_branding=Chromium`. There is no `libffmpeg.so`
  in the distribution to swap — ffmpeg is statically linked into `libcef.so` — so the flags must
  be set at Chromium build time: `proprietary_codecs=true ffmpeg_branding=Chrome`. Until such a
  wheel is in place, any video test asset has to be WebM/VP9 or AV1.
- **Build CEF from source with `is_official_build=true`, never `false`.** A non-official build
  completes and produces a working-looking `libcef.so` that segfaults at runtime inside Skia,
  at `sk_malloc_size` → `malloc_usable_size` (`SIGSEGV SI_KERNEL`, registers full of the `0xcd`
  poison pattern). It kills the manager during font setup (`SkFontMgr_FCI::onMatchFamilyStyle`)
  and a standalone CEF when a window is created (`ContentsContainerOutline::SetClipPath`), so it
  looks like two unrelated bugs. The difference between the two builds is a single GN arg: CEF's
  own `gn_args.py` emits `use_partition_alloc_as_malloc=false` **only** for official builds, so a
  non-official one gets the default `true` and `malloc_usable_size` and the allocator that owns
  the pointer no longer agree. That correlation is established from the two builds' resolved
  args; it was not isolated by flipping that one arg on its own, so treat "build official" as
  the rule rather than "set that arg". Official is also what Spotify's published builds use,
  i.e. the configuration the rest of the stack has been tested against. Official builds need PGO
  profiles: set `'checkout_pgo_profiles': True` in `chromium/.gclient` and run `gclient
  runhooks`, or GN fails with a `.profdata` not found.
- **Chromium will not use VA-API on NVIDIA, by explicit upstream decision.**
  `media/gpu/vaapi/vaapi_wrapper.cc` skips any DRM device whose driver reports `nvidia-drm`
  ("their VA-API drivers do not support Chromium and can sometimes cause crashes",
  crbug.com/1492880), gated on the `VaapiOnNvidiaGPUs` feature which is disabled by default.
  `VaapiIgnoreDriverChecks` does *not* bypass it. Forcing `--enable-features=VaapiOnNvidiaGPUs`
  does get past the skip, and then `nvidia_drv_video.so` fails to initialise inside the GPU
  process anyway (`init CUDA ERROR 'unknown error' (999)`, `CUDA ERROR 'initialization error'`)
  even though `vainfo` outside Chromium lists the full H.264/HEVC/VP9 NVDEC profile set. So on
  NVIDIA there is nothing to enable — do not go looking for a switch that turns it on.
- **`VaapiVideoDecoder` is not a feature — passing it in `--enable-features=` is a silent no-op.**
  It only appears in Chromium's source as a decoder *name* string
  (`media/base/decoder.cc`), never as a `BASE_FEATURE`. The real gates for the VA-API/V4L2 Linux
  decode path are `kAcceleratedVideoDecodeLinux` (on by default when `USE_VAAPI` is compiled in)
  and `kAcceleratedVideoDecodeLinuxZeroCopyGL` (on by default) — both already enabled without
  asking, so `--enable-features=VaapiVideoDecoder` in `unitotem-system`'s `chromium-accel` file
  changes nothing. What actually decides whether the real decode path exists in the binary at
  all is a **build-time** GN arg, not a runtime switch — see the next entry.
- **On a stock Linux build, whether hardware decode exists in the binary at all is decided by
  `use_vaapi`/`use_v4l2_codec` at GN time, and their defaults differ sharply by architecture.**
  `media/gpu/args.gni`: `use_vaapi` defaults to `is_linux && (x11 || wayland) && (target_cpu ==
  "x86" || target_cpu == "x64")` — **false on arm64, unconditionally, regardless of what hardware
  is present**. `use_v4l2_codec` defaults to `false` on every architecture, including arm64; it
  is not something a Raspberry Pi build gets "for free" for being non-x86, it has to be turned on
  explicitly. `media/mojo/services/BUILD.gn` compiles the real decoder client
  (`gpu_mojo_media_client_linux.cc`) only when `use_linux_video_acceleration = use_vaapi ||
  use_v4l2_codec` is true; otherwise it silently links `gpu_mojo_media_client_stubs.cc`, a no-op.
  So a default arm64 CEF build has **no hardware video decode client compiled in at all** — not
  "decode fails to accelerate", but the code path to try does not exist in the binary, and no
  runtime switch can bring it back. Reaching V4L2 on arm64 requires `use_v4l2_codec=true` in
  `GN_DEFINES` for that build specifically (confirmed buildable outside ChromeOS:
  `media/gpu/chromeos/BUILD.gn` asserts only `is_linux || is_chromeos`), and the result is
  untested — see `manager/utils/system/gpu.py`'s `VERIFIED['v4l2'] = False`.
- **The VA-API decode blocklist (`IsBlockedDriver` in `vaapi_wrapper.cc`) only blocks
  *encoding*, never decoding**, and only for one specific case (AMD Stoney Ridge VBR). There is
  no general non-Intel decode blocklist at that layer — the NVIDIA exclusion (previous entries)
  is its own separate, earlier check in the same file, not part of this one. Do not conflate the
  two or assume AMD/other-vendor decode is blocklisted the way NVIDIA is; it has simply never
  been measured here (no AMD/Intel machine available).
- **`video_decode: enabled` in the GPU feature stats does not mean anything is offloaded.** It
  means "not blocklisted". The honest signal is `SystemInfo.getInfo`'s `videoDecoding` array
  (the "Video Acceleration Information" table in `chrome://gpu`): an empty array means the GPU
  process advertises no accelerated decode profiles at all. Confirm which decoder was actually
  used with the CDP `Media` domain — `kVideoDecoderName` and `kIsPlatformVideoDecoder` — never
  by inferring it from CPU cost.
- **GPU acceleration in the test VM is PCI passthrough, not virgl.** `accel3d`/`egl-headless` is
  a dead end on this host and must not be retried; `tools/unitotem-test-gpu.xml` passes a real
  GTX 1060 instead and the guest then reports `gpu_compositing: enabled` and
  `rasterization: enabled`. Full procedure, including the proprietary-driver install and the
  traps it brings, is in `VM-TESTING.md`, "GPU acceleration".
- **`sudo` strips `DEBIAN_FRONTEND` and closes every file descriptor ≥ 3.** Anything relying on an
  inherited environment variable or on `APT::Status-Fd` therefore silently does nothing when run
  through sudo. The manager runs as root; it does not need sudo in the first place. 