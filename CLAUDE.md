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
Reboot, power-off and screen on/off timers with cron-style schedules, run by UniTotem itself (`timers.json`, schedules
evaluated with croniter): no dependency on a cron daemon or on the system crontab.

### Updates
The system is updated by replacing the whole root image, never by upgrading packages: apt is not
available at runtime (dpkg stays). The device downloads a signed release image from the project's
repository (GitHub Releases) into the inactive slot, boots it on trial and confirms it only once the
manager answers `GET /health`; otherwise it rolls back to the previous slot. User data (`DATA`:
`/etc/unitotem`, certificates, uploads) is never part of the image and survives updates.

The device side is `/usr/sbin/unitotem-update --json` (subcommands `status`, `check`, `fetch`, `apply`,
`confirm`, `rollback`, `cancel`; progress as one JSON line in `/run/unitotem-update.progress`),
owned by the unitotem-system project (contract in its `AB-DESIGN.md`). The manager never downloads or
writes images itself: it starts the commands, polls them and shows the result in the web UI.
The Updates page shows the installed version, whether the image supports A/B, the result of a check
and, during a fetch, its progress, and offers check/apply/confirm/rollback. A periodic task checks
daily in the background and shows a hint in the navbar. Because data migrations must stay readable by
the previous version (rollback), shared configuration is kept backward compatible by one version.

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


# Packages and plugins

UniTotem comes in roles, and the code is split along them (nothing is shared between packages, no directory is written
by two of them):

- **`unitotem-manager`** (the core, `manager/` minus `plugins/`): scheduler, uploads, users, remote control of other nodes,
  backup, web UI. Installed alone it is a server that orchestrates other nodes and touches nothing of its own machine:
  no updates, no network/audio/display management, no terminal, no power buttons, no timers.
- **`unitotem-node`** (`manager/plugins/unitotem_node`): everything that manages the machine - network, audio, displays and
  screen power, power, timers, updates, terminal, first-boot hotspot. Needs the core.
- **`unitotem-viewer`** (`manager/plugins/unitotem_viewer`): the local Qt+CEF windows. Needs the core and the node.
- **`unitotem`**: metapackage, a totem is all three. `python3-cefpython3` and `plymouth-theme-unitotem` are their own packages.

A plugin is a Python package in `manager/plugins/` exposing `PLUGIN` (`utils/plugins.py`: API classes, routers, backup parts,
start-up/first-boot/shutdown hooks, `run_gui`). The core loads whatever is there, logs and skips a plugin that fails to load,
and **never imports a plugin directly** (only through the hooks). A plugin may import the core. The web UI hides what the
server does not offer: `canCall()` is false for a function missing from `/api/schema`, and `window.__INITIAL_STATE__.plugins`
lists the loaded plugins (the terminal is a websocket route, not an API function). New code that touches the machine
(subprocess, DBus, /sys, systemd) goes in the node plugin; new core code must run on a plain Debian server.

Backup, restore and reset work on *parts* (`utils/backup.py`, `BackupPart`): the core has playlists, users, pairing
(keys and certificates) and uploads; a plugin adds its own. `GET /backup/parts` tells the UI what to offer.

The service is `Type=notify`: `utils/ready.py` sends READY=1 when the first frame of the viewer is on screen (an early
Qt splash, before CEF starts, so the system's boot splash can end), or when the web server answers if there are no local
windows. `TimeoutStartSec=300`.
On a slow board the manager needs many seconds just to import its dependencies, so the very first frame comes from a
separate tiny process: `unitotem-splash.service` (shipped by unitotem-viewer, started in parallel with the compositor: it warms up, then waits for the Wayland socket) runs
`plugins/unitotem_viewer/bootsplash.py`, a plain script that imports only QtGui (NOT QtWidgets, nor the manager), Pillow and sdnotify (measured on a Pi 4: the window
is up 1.2 s after it starts). It must stay free of imports from the rest of UniTotem, sends its own READY=1 and is stopped by the viewer (a D-Bus `StopUnit` call to systemd) once
the viewer's own Qt splash (splash.py), which looks the same, is showing.


## On-screen keyboard (touch panels)
Per window (`Viewers/set_keyboard`, kept per screen in viewer_settings.json, off by default): enabled, the position of the
button that opens it, a layout. It opens by itself when a text field gets the focus in ANY frame of the page and closes when
the focus leaves (unless the visitor opened it with the button). Pieces: `plugins/unitotem_viewer/cdp.py` (the viewer's
DevTools connection: injects a focus watcher into every frame, cross-origin ones included, which are separate targets,
and delivers text with `Input.insertText` and keys with `Input.dispatchKeyEvent`), `webui/src/keyboard.js` (simple-keyboard
UI drawn inside the page's rotated container, loaded by `boot-screen.html` on demand), and the window id in the page address
(`boot-screen.html?window=N`), which is how a DevTools target is tied to a window. The keyboard can be moved and resized
but is clamped inside the screen and to a minimum size after every gesture, every size change and every rotation.

## Touch panels
Everything per window and per screen, off by default, kept in viewer_settings.json (core) or touch_map.json (node):
- **Touch-to-screen mapping** (node plugin, `system/touch.py` + `touch_service.py`, Display page): touch devices are tied to an
  output by a stable identity (`name|vendor:product|serial`, never the `eventN` number): the touch screens come from `/proc/bus/input/devices` (DIRECT property),
  the mapping is the node's fragment of the compositor configuration (`<touch deviceName mapToOutput>`, labwc handles position and rotation by itself and
  matches by device NAME: two identical panels share one mapping), re-read on SIGHUP; the list is kept up to date by inotify on `/dev/input`, no polling.
- **Screensaver** (`Viewers/set_screensaver`): a window can have a home page shown indefinitely; after an idle timeout the
  playlist plays as an overlay, the first touch dismisses it and is consumed (a shield keeps compat mouse events from
  clicking through). Activity inside the cross-origin home page is reported by a script injected into every frame
  (cdp.py), the idle timer is one asyncio timer (idle.py). No overlay when there is nothing to play.
- **Refresh** (`Viewers/set_refresh`): for windows without the screensaver, reload the page after an idle time and/or at
  cron-style times (croniter, minute-boundary task); the iframe src is set again.
- **Zoom** (per asset, web pages): `zoom` 0.25-5 changes the page's layout viewport (iframe sized 100/zoom % and scaled),
  and `allow_pinch_zoom` (default off) decides whether the visitor can pinch (touch-action on the iframe).
- **On-screen keyboard**: see above.
`Viewers/*` permissions (decided with the user, one by one): the scheduler capability covers what a window shows (`list`,
`assign_playlist`, `unassign_playlist`, screensaver, refresh, per-window audio output); admin only the configuration of the panel
and the layout (`keyboard`/`set_keyboard`, `add_window`, `remove_window`). The web UI hides the keyboard row from non-admins.

## First access, VNC, suspend
- **Accounts = operating-system accounts (user decision, 2026-10-17)**: the web login is PAM (`utils/osusers.py`, python-pam, service
  `unitotem-manager` = `/etc/pam.d/unitotem-manager`, `login` as a stand-in when missing), and only root and members of the group
  `unitotem` get in (`pwd`/`grp`, checked at login and on every request, so removing the account/group ends the session). Creating a user in
  the UI runs `useradd` (no home, nologin shell, GECOS `UniTotem user`) + `chpasswd` (secret on stdin); deleting deletes only accounts
  UniTotem made, otherwise just leaves the group. users.json holds ONLY the capabilities (no password any more). root is admin.
  The backup of the users part carries each account's `/etc/shadow` hash (`chpasswd --encrypted` on restore); an archive from before has no
  recoverable password and is skipped. Wrong password costs ~2.4 s on the VM / ~3.3 s on a Pi 4 (pam_faildelay), a right one 0.12 s / 0.3 s.
- **Accounts and OTA (decided with the user, 2026-10-17)**: `passwd/shadow/group/gshadow` are in the `/etc` overlay and an OTA gives the new slot the
  image's own files, so without work root's password reverts to the image default and the web accounts vanish. Decision: the OTA engine
  (unitotem-system, `migrate-etc-overlay`) MERGES identity files (image base + root's hash + local accounts uid >= 1000 + their memberships),
  validated, atomic, falling back to the plain image files on ANY failure, never touching the old slot (spec sent to that session). Rejected:
  UniTotem-only users (Home Assistant style: the GUI password and the SSH/terminal one would differ and the second never changes), whole-file
  whitelisting (freezes the image's system accounts), systemd userdb (tested: record with a hash is not resolvable by passwd, PAM auth failed),
  libnss-extrausers (works with PAM but needs the package, own writer, root stays in the overlay). Never end up locked out of a device.
- **A session ends only by an event, never by a re-check**: the websocket is authenticated once (`/ws`, `/terminal`, `/vnc`) and kept for good; the
  SERVER closes it (`utils/sessions.py`, close 1008 `Not Authenticated`) when the password of the account changes, the account is deleted or
  the users are restored/reset, and the page just reloads on that close (-> login). Cookies carry a session id (`sid`) and issue time (`at`);
  `UserData.invalid_before/keep_sid` revoke the older ones (`SessionLoginManager`). Changing one's OWN password keeps the session it was
  changed from (`keep_sid`) and ends all the others; an administrator changing someone else's ends all of that user's. No periodic PAM check.
- **First access (no claim code, no default password)**: with no administrator account that can log in (`user_manager.has_admin()`, root does
  not count) every page goes to `/setup` (name + password, policy of `utils/passwords.py`, creates the system account), a 404 afterwards;
  two simultaneous setups have one winner (asyncio lock). The totem shows the WELCOME screen (gradient, address, hotspot QR) meanwhile,
  `idle_asset()` is `first_boot` until an admin exists and `WebviewManager.refresh_idle()` switches the windows when setup completes. There
  is NO admin/admin. The claim code and `--create-admin` were removed on purpose: "too protective".
- **VNC on demand** (node plugin `vnc.py`, `system/vnc.py`, VncWindow.vue): `wayvnc` (`--unix-socket`, `--disable-input` for view-only, `--output NAME`: it captures ONE output, with an explicit
  `--config` so it never looks for a home directory) on a UNIX socket only (no TCP port), view-only enforced by the server, control an explicit per-session switch, started when the window opens and stopped on
  close/drop/shutdown (no idle timeout by default, `UNITOTEM_VNC_IDLE_TIMEOUT` switches one on), admin only, `/vnc` websocket bridge with Origin check. Debian binary is `/usr/bin/wayvnc`.
- **Suspend with scheduled wake** (node plugin `system/{wake,rtc,suspend}.py`, timer action `sus`, `Power/suspend`): the alarm is
  written to `/sys/class/rtc/rtcN/wakealarm` (UTC epoch whatever /etc/adjtime says; clear first, read back), then logind
  `Suspend` over D-Bus; refused with a reason when there is no usable RTC / wakeup / CanSuspend; resume is the logind
  `PrepareForSleep(False)` signal. `MinuteClock` in timer_actions.py is the clock-jump policy of the timers loop.

## Remote control (server/client)
Implemented in `utils/remote/` (identity, tls, link, service, discovery, pairing, transfer, bundle, viewers), `api/remote.py`,
`routers/pairing.py`, the Remote page. Every node has a self-signed ECDSA identity (backed up, restore checks key/cert match);
pairing PINS the peer's certificate on both sides. The link is a websocket over TLS 1.3 with mutual authentication on a SEPARATE
port (`cmdargs.port_link`, 4443), served by the `websockets` library because hypercorn does not expose the peer certificate;
`/remote` on 443 is loopback only. An unpaired client announces itself over mDNS (zeroconf), servers list it and adopt it after
the user compares the certificate fingerprint; manual pairing by address works from both sides. Adopting moves the client's
playlists and uploads to the server (streamed, hash-checked, all-or-nothing); unpairing returns what the client showed and
removes from the server only what nothing else uses (`plan_leave`). All Remote functions are admin only.

## Notifications
`utils/notifications.py` is the ONLY way to raise one: `notifications.notify(key, level, title, text, source=, audience=, link=)` and
`resolve(key)` (plugins import the core, never the opposite). Same key and same content does nothing (a dismissed one stays
dismissed), changed content updates it and makes it unread again. Audience = the capability that may see it (`admin` for anything
about the machine or the network). `link` must be a path of the web UI. State is in `/var/lib/unitotem/notifications.json` (not
in the backup, compatible with the previous version). API `Notifications/list|mark_read|dismiss|dismiss_all`, pushed per user
because the list differs per capability; dismiss is global. UI: the bell in the top bar. Producers: suspend refused/skipped,
update available, remote control (pairing requests, client gone, server unreachable), plugin load failure.
**Desktop notifications (org.freedesktop.Notifications)** also feed the bell: the node plugin (`system/desktop_notifications.py`) OWNS the
well-known name on the user (session) bus and on the system bus, each only when the name is free (a desktop with its own daemon keeps it), so
anything that runs `notify-send` is not lost on a kiosk with no daemon. Mapping: summary -> title, body (markup removed) -> text, app name ->
source, urgency critical -> `error` else `info`, audience `admin`; `replaces_id` updates, `CloseNotification` resolves, a dismissal in the web
UI answers the application with `NotificationClosed(reason 2)` (`notifications.listen_closed`), at most 20 per minute per application. The system
bus needs the policy file `debian/org.unitotem.Notifications.conf` (root may own the name, anybody may send). A GUI application only reaches it
if the manager's account has a session bus (`/run/user/0/bus`): the image must provide one (unitotem-system), otherwise only system-bus senders arrive.

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

System- or OS-level changes (kiosk image, systemd units, the compositor session, packaging on the target)
additionally go into `unitotem-system/debian-unitotem.md`, which is the reference document for the
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

- **A `python3 -m venv --copies` venv is NOT self-contained across a Python-version image
  upgrade.** `--copies` only copies the interpreter *binary*; the standard library it loads is
  still resolved against `sys.base_prefix` (`/usr`), i.e. the venv still depends on the *system*
  having that same Python version installed. Deploying a bookworm-built cp311 venv onto a trixie
  guest (system Python 3.13, no `python3.11` package at all) fails at the very first import —
  `Fatal Python error: init_fs_encoding ... ModuleNotFoundError: No module named 'encodings'` —
  which reads like a corrupt venv rather than a version mismatch. The fix is not copying more
  files; it is rebuilding the venv fresh with the target's own `python3 -m venv`, which is exactly
  what `debian/rules` already does when it derives `CEF_PYTAG` from `$(VENV)/bin/python3` — the
  trap is only in reusing an *already-built* venv across a base-image version bump.
- **The trixie kiosk image pins `/var/lib/dpkg` read-only through its own bind mount
  (`var-lib-dpkg.mount`), separate from the root filesystem's own rw/ro state.** `mount -o
  remount,rw /` is not enough to `apt-get install` anything on this image — `/` can already read
  `rw` while dpkg's database still reports `Read-only file system`. Remount that one mount point
  specifically (`mount -o remount,rw /var/lib/dpkg`, install, `mount -o remount,ro /var/lib/dpkg`)
  rather than fighting the root filesystem. This is new in the trixie image; the old bookworm one
  did not separate the two.

- **NO COMMAND MAY EVER OPEN A GRAPHICAL PASSWORD PROMPT (user rule, repeated since the start of the project; it must stop).** Every ssh/scp/rsync/sshpass
  call runs with `SSH_ASKPASS_REQUIRE=never` in its own environment (not "exported earlier"), key hosts also with `-o BatchMode=yes`, password hosts with
  `sshpass` + `-o PreferredAuthentications=password -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no` (a changed host key prompts too), and `sudo`
  only as `sudo -n`. The brief of every subagent that touches a remote host must say so.
- **ssh must never be able to prompt graphically.** On KDE, a missing key or an unknown host key
  makes ssh spawn `ksshaskpass`, which steals focus and blocks automation until a human clicks it.
  Always run with `SSH_ASKPASS_REQUIRE=never` and `-o BatchMode=yes` — `tools/vm-env.sh` does both,
  so prefer `tools/vm-ssh` over calling ssh directly.
- **A missing Qt xcb library reports the wrong library.** Qt aborts with an uncatchable SIGABRT
  saying *"xcb-cursor0 or libxcb-cursor0 is needed"* regardless of which xcb library is actually
  absent. Ask the linker instead: `ldd .../PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"`.
- **`rsync -a` into `/etc/sudoers.d/` produces a file sudo refuses to parse**, because it preserves
  the source's uid. Always `chown root:root` afterwards.
- **A web UI tab left open across a deploy stops navigating (now it reloads itself, see `webui/src/main.js`).** The bundle
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
- **RESOLVED (2026-08-09): the shipped cefpython3 wheel used to have no proprietary codecs;
  rebuilding CEF from source fixes it, and that build is now done and verified.** The cause was
  that the wheel is built on the **Spotify prebuilt CEF binary distribution**
  (`tools/download_cef.py`), which is built with Chromium's default `ffmpeg_branding=Chromium`.
  There is no `libffmpeg.so` in the distribution to swap — ffmpeg is statically linked into
  `libcef.so` — so the flags have to be set at Chromium build time:
  `proprietary_codecs=true ffmpeg_branding=Chrome` (see the source-build recipe in
  `VM-TESTING.md`, sec. 8b). A CEF 147 distribution was built from source with those flags plus
  `is_official_build=true` and PGO (see the next entry for why official matters), and cp311/cp313
  wheels built from it are staged locally at `vendor/cefpython-wheels/` (gitignored — not yet
  published anywhere, so a fresh clone or CI does not get them automatically; `debian/rules`
  picks up whichever wheel matches the target interpreter's tag from that directory if present).
  Verified end to end on real guests, GPU-passthrough and no-GPU alike: `canPlayType` now answers
  `probably` for `avc1` (H.264) and `mp4a.40.2` (AAC), and a real H.264 MP4 plays with zero
  dropped frames (`kVideoDecoderName=FFmpegVideoDecoder`) instead of failing with
  `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`. HEVC, AC3/EAC3 and Theora still answer `no` — expected,
  `ffmpeg_branding=Chrome` does not bring AC3/EAC3/DTS, and HEVC needs hardware decode neither
  test guest has (see the `use_vaapi`/`use_v4l2_codec` entry further down). Until this wheel is
  actually shipped in a built package, any video test asset on an unpatched install still has to
  be WebM/VP9 or AV1.
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
  decode path are `kAcceleratedVideoDecodeLinux` (its name string is **`AcceleratedVideoDecoder`**, on by default ONLY when
  `USE_VAAPI` is compiled in, so OFF on an arm64/V4L2-only build: it must be enabled with `--enable-features=AcceleratedVideoDecoder`,
  measured on a Pi 4: without it no V4L2 code runs at all and `videoDecoding` stays empty), `kAcceleratedVideoDecodeLinuxGL` and
  `kAcceleratedVideoDecodeLinuxZeroCopyGL` (on by default), so `--enable-features=VaapiVideoDecoder` in `unitotem-system`'s
  `chromium-accel` file changes nothing. What actually decides whether the real decode path exists in the binary at
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
  `media/gpu/chromeos/BUILD.gn` asserts only `is_linux || is_chromeos`) AND `use_vaapi=false` (the two are mutually exclusive
  and the `gn gen` of the x64 config run by `gclient_hook.py` fails on `assert(!(use_v4l2_codec && use_vaapi))` otherwise).
  **Measured on a Pi 4 (2026-10-18, CEF 147 built with `use_v4l2_codec=true`)**: with `AcceleratedVideoDecoder` enabled Chromium picks
  `V4L2VideoDecoder` (`kIsPlatformVideoDecoder=true`, `/dev/video10` = bcm2835-codec stateful H.264), then logs `PickDecoderOutputFormat():
  Initializing ImageProcessor` and STALLS: no frame, no error, no fallback (`currentTime` 0, `getVideoPlaybackQuality().total` 0), and
  `SystemInfo.getInfo` `videoDecoding` stays empty. Without the feature the same build plays H.264/AAC in software (720p30 smooth,
  1080p30 ~15% dropped frames, 52% CPU). So never put `AcceleratedVideoDecoder` in the `pi` profile until the stall is understood.
  **Cause found (2026-10-19, debug libcef on the Pi 4, X11 + Mesa V3D)**: `GBM: Failed to create GBM BO, NV12/YV12/P010 ... gbm_device_is_format_supported() = 0`, so
  `supports_nv12_gl_native_pixmap` is false and `GetPreferredRenderableFourccs` returns ONLY AR24; the stateful decoder offers YU12 first, so
  `PickDecoderOutputFormat` needs an ImageProcessor (to AR24): the GL one is only tried when `candidates[0]` is NV12 (it is YU12), LibYUV does not
  take it, the V4L2 one says "None of YUYV YU12 YV12 NV12 NV21 YU12 formats is supported" -> `Unable to find ImageProcessor to convert format`,
  and `V4L2StatefulVideoDecoder::ApplyResolutionChange` ignores the failure -> silent stall. Patch tried: list NV12 first
  (`tools/cef-server/patches/0001-*.patch`, applied in the server tree, arm64 relinked in 7 min) PLUS a `SetFormat` on the CAPTURE queue for the picked fourcc (upstream assumes it is the queue's current one, so with NV12 first the frames stayed I420 and every one was rejected by the GL image processor). Result on the Pi 4: decodes, but slower than software (1080p: ~12 fps, 45% dropped, 110% CPU) and a second video hung: NOT enabled. Why (dcheck build + timers, 2026-10-19): Chromium then uses the LibYUV image processor, i.e. a CPU NV12->AR24 conversion (the GL image processor only does NV12->NV12), and the dmabuf mappings of the decoder output are uncached (60-120 ms per 1080p frame; a cached bounce gets 33-55 ms, `tools/cef-server/patches/experimental-*`, still too slow). Do not retry V4L2 on the Pi 4 without a different output path.
  A release build compiles every `DVLOG` out (that is why the log goes quiet): debug it with a `dcheck_always_on=true` libcef in its own
  out dir (`out/Dbg_arm64` on the build server), not with the release tree.
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
- **`ddcutil` (2.2.0, trixie's packaged version) prints roughly ten lines of connector-resolution
  diagnostics to *stdout*, not stderr, before the actual result, whenever it cannot resolve a DRM
  connector name for the bus** — which it never could on the passthrough NVIDIA driver used in the
  test VM (`(set_connector_for_businfo_using_edid) Failed to find connector name for /dev/i2c-4
  using EDID ...` down through `Display connectors reported by /sys:`, then the real `VCP ...` or
  `Display N` block). Redirecting stderr away does not remove it. Confirmed on real hardware
  (`unitotem-test`'s passed-through GTX 1060 + LG Ultrafine on DP-1): a parser that assumes the
  result is on a fixed line, or that scans stderr for noise, breaks. Scan every line for the
  `VCP `/`Display `/`Invalid display` prefix instead of trusting position — which is what this
  project's parsers already do, verified byte-identical against this real 2.2.0 output as against
  the 2.2.5 fixtures they were first written against.
- **`cec-ctl`'s exit code is reliable on the actual Debian package (`v4l-utils` 1.30.1, trixie) —
  an earlier note here was wrong.** `cec-ctl -d <nonexistent> --playback/--standby` correctly
  returns **1** and prints `Failed to open ... : No such file or directory`; this was tested
  against a differently-built `cec-ctl 1.32.0` (not the Debian package, origin unconfirmed) that
  returned 0 on the same failure, and that behaviour does not reproduce on the shipped tool.
  `--list-devices` legitimately returns 0 with empty output when no adapter exists — that is a
  correct empty result, not a lie. Trust the exit code for open/command failures on the packaged
  tool; treat an empty `--list-devices` as "no adapter found", not as an error.
- **On a GPU whose driver is not i915/amdgpu/nouveau, `/sys/class/drm` can have *no
  per-connector subdirectories at all*.** Confirmed on `unitotem-test` with the passed-through GTX
  1060 under the proprietary NVIDIA driver on trixie: `ls /sys/class/drm/` shows only `card0`,
  `renderD128`, `version` — no `card0-DP-1` or equivalent, so there is no `/sys/class/drm/<conn>/edid`
  to read at all, not merely a missing `ddc` symlink (that was the dev host's, different, milder
  finding). The only route to a monitor's EDID that worked here was `xrandr --props`, which
  exposes a raw `EDID:` hex property per output on a **real Xorg session running directly on
  KMS with no compositor** — confirmed decoding it produced the exact `mfg:model:serial` triple
  (`GSM:LG ULTRAFINE:401NTMX52747`) that `ddcutil detect` printed for the same physical monitor
  on its I2C bus. **Not universal** — the same check against this project's own dev host found
  *zero* `EDID:` properties on three connected outputs, because that host's `:0` is **XWayland**
  (`ps` shows `/usr/bin/Xwayland :0 ...`), whose RandR is an emulation layer over Wayland's own
  output protocol and does not forward EDID at all (the tell: `xrandr --props` reports
  `RANDR Emulation: 1` on every output there, absent from the VM's capture). Irrelevant to the
  kiosk, which never runs Wayland/XWayland, but the code must still treat "no EDID property
  returned" as a legitimate outcome (fall back to `/sys/class/drm/<conn>/edid` if present, else
  report the mechanism unavailable for that output) — not as a bug — since any developer testing
  this against their own desktop will hit exactly that path routinely. Treat `xrandr --props`'s
  EDID property as the primary route for the RandR-name-to-monitor-identity join on the kiosk's
  own X session, with `/sys/class/drm/<conn>/edid` as a fallback for drivers that do populate it.
- **Importing `utils.models.command_line` under pytest crashes the whole run with an
  `INTERNALERROR`, before a single test executes.** `CommandLineArgs` is a `pydantic_settings
  BaseSettings` with `cli_parse_args=True`, so merely *importing* the module parses `sys.argv` -
  and under pytest that argv is pytest's own (test paths, `-v`, ...), which argparse rejects
  outright. Most of `manager/api` and `manager/utils/models` transitively import it (e.g.
  `api/system/cron.py` → `api/ws/wsmanager.py` → `utils/models/remote.py` →
  `utils/models/command_line.py`), so almost any real test hits this. Fixed by sanitising
  `sys.argv = sys.argv[:1]` at the top of `manager/tests/conftest.py`, before any test module's
  own imports run - it has to be conftest.py specifically, since pytest loads it first.
- **Runtime data must never be written under the code directory.** The kiosk image mounts `/usr`
  read-only (no overlay), so `uploads_folder` defaulting to `<code>/uploaded` made the manager
  crash-loop at import time with an `OSError` in `UploadManager.__init__`. Uploads now default to
  `/var/lib/unitotem/uploaded` (`utils/constants.py`); everything else the manager writes lives in
  `/etc/unitotem`, `/etc/ssl`, `/var/cache/unitotem-cef`, all on the DATA overlay.
- **The websocket API is deliberately id-less (rule from the user).** A command is fire-and-forget and every
  change is pushed to all connected clients, including the one that asked, so two people on the same page
  always see the same state, and the server only loops over the connected clients, no per-request ids to
  remember. Do not add request/response correlation ids. The client side is `webui/src/api.js`
  (`api.X.y()` to send, `api.X.y.on(cb)` / `.use()` to receive, cleaned up on unmount);
  the old `window.sendCommand`/`onWSMessage`/`setInitCommands` globals no longer exist. The server describes
  its API at `GET /api/schema` (checked at runtime in the browser console) and `tools/gen-api-types.py`
  regenerates `webui/src/api.d.ts` for editors: run it after adding or changing an API function.
  `tools/ui-smoke.py` drives headless Chrome over every page and compares console errors and visible text
  before/after a front-end change.
- **The web terminal runs `login`, never a shell, and is started without forking the manager.** The manager is root and
  multi-threaded (Qt, CEF): `pty.fork()` in it is unsafe, so `routers/websocket/terminal.py` opens a pty and starts
  `setsid -c /bin/login` with the subprocess machinery. A non-blocking read on the pty master wakes spuriously and
  raises `BlockingIOError`, which is an `OSError`: treating every `OSError` as "the session ended" closed about a third
  of the sessions the moment they opened (EIO is the real end). Needs the admin capability, at most 4 sessions, 15 min idle.
  `tools/ui-terminal.py` tests it end to end in headless Chrome (login, command, refusals).
- **`pkill -f <pattern>` inside an ssh command line kills the command's own shell**, because the pattern is in its
  command line: the rest of the command silently never runs (a `systemctl restart` after it did not happen and the
  manager was left down). Use `pkill -x name`, a pid, or a pattern written like `[i]nit.py`.
- **Importing a name that does not exist, inside a `try/except ImportError`, fails silently.** In
  `utils/system/network/networkmanager.py` the classes were imported as `NetworkManagerConnectionActive` and
  `NetworkManagerIP4Config`, which are not classes in sdbus-networkmanager 2.0 (they are `ActiveConnection` and
  `IPv4Config`): the fallback made them `None` and every device was reported without an active connection and without
  an address, so the network page said "Connected" and "Not connected" on the same row. Check the real names with
  `dir(sdbus_async.networkmanager)` on the target, and when a fallback is taken log it as an error, not nothing.
- **A router included after `routers.scheduler` is unreachable for GET.** The scheduler router ends in a catch-all
  `/{path:path}` that serves the web UI, and Starlette matches routes in order: `GET /backup` was answering the HTML page
  (the download "worked" and saved HTML). Include every router *before* it; plugin routers are included before it too.
- **The `sdbus` wheel from PyPI (the dev `.venv`) cannot open the user bus here (`sd_bus_open_user` -> error 123, ENOMEDIUM, even with
  `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` right): it bundles an old sd-bus; the Debian `python3-sdbus` (system libsystemd) works.** Test anything
  that opens the user bus against the distro package (e.g. on the build server: `apt install python3-sdbus`, `dbus-run-session -- python3 ...`),
  and run async subprocesses (`notify-send`) in such a test: a blocking `subprocess.run` in the same loop starves the D-Bus server under test.
- **Do not touch the first-boot welcome page's look (user, 2026-10-19: "it has always worked well").** High CPU in `cefpython3 --type=gpu-process` on a
  machine with no GPU (`videoDecoding` empty, llvmpipe: venus before the i915 firmware package) is software rendering: the fix is the GPU (firmware, driver,
  profile in `gpu.py`), not changing the page. (A headless SwiftShader measurement showed its `background-position` animation costs ~12 cores of
  repaint against ~0.5 with a cheaper animation; the user chose to leave the page as it is.)
- **Do not parse `ip addr` (or any CLI) for facts psutil or /proc already give.** `utils/system/ip.py` was a copy of a
  Red Hat parser of `ip addr`'s text that also needed `/usr/sbin/ip`, which a minimal Debian does not have: the login page
  answered 500 on a clean install. It is now `psutil` plus the default route from `/proc/net/route`. Prefer a library
  (croniter for schedules, python-xlib for DPMS, sdnotify for READY) to anything hand-written.
- **`dh_python3` renames the `.so` files of a package it is run on** (`libcef.so` becomes `libcef.cpython-313-...so`) and CEF
  loads them by their plain names. It is not run on `python3-cefpython3`; its dependencies are written by hand.
- **`dpkg-shlibdeps` needs the libraries a binary links against installed in the build environment.** CEF's own are in
  `Build-Depends` (`libgtk-3-0t64`, `libnss3`, ...) so `Depends` is computed, not guessed. Debian's `t64` names apply on trixie.
- **Do not strip blank lines from `debian/control`**: they separate the stanzas ("duplicate field Package found").
- **The unit tests need a root-writable `/etc/unitotem`** (the settings objects create their files at import), so
  they run in `debian/tests` (autopkgtest) against the installed packages, not during the build.
- **Talk to systemd through its own interfaces, never by running its commands**: `sd_notify` (sdnotify) for READY, D-Bus
  (python-sdbus: logind, hostnamed, systemd1 `StopUnit`) for everything else, signals (SIGTERM) for stopping a service.
  No `subprocess` call to `systemctl`/`loginctl`/`hostnamectl`.

- **A plugin's routers must be returned by a function, not built at import.** `Plugin.routers` is a callable: a router module
  imports `routers.login`, which needs the API that is being built while the plugins load; importing it from the plugin's
  `__init__` worked or failed depending on which module was imported first (the API types generator hit it, the manager did not).
- **Do not rely on the exports of a Vite entry chunk**: an app build drops them. `keyboard.js` publishes `window.utKeyboard`
  instead, and the page that loads it calls that.
- **Moving and resizing something inside the viewer page cannot use interact.js and the like**: the page's content can be
  rotated and flipped with CSS and those libraries do not follow a transformed parent; `keyboard.js` converts the pointer
  movement itself (`toLayer`).
- **With `--disable-web-security` Chromium wants `--user-data-dir`**, otherwise it logs an error and ignores the switch.
- **A new `git worktree` for an agent starts from the stale `main`; the project lives on `dev`**: `git reset --hard dev` first
  (merge the result back into `dev`, never `main`).
- **The test VM is shared when several agents work: `tools/vm-deploy` rsyncs with `--delete`**, silently removing another
  agent's code (it shows as the misleading websocket error "Invalid command", which `web_ui.py` gives for any KeyError).
  Run deploy, test and restore under `flock /tmp/unitotem-vm.lock`, and never `pkill` by name on the VM: kill by PID.
- **`Input.synthesizePinchGesture` bypasses `touch-action`** and gives a false "pinch works"; test pinch with
  `Input.dispatchTouchEvent`. `visualViewport.scale` of the viewer page survives `Page.reload` (reset it with
  `Emulation.setPageScaleFactor`).
- **Setting an iframe's `src` to its current URL reloads it**, cross-origin included. A DevTools binding is a window
  global: page functions must not reuse its name (`utSaver`, `utKey`, `utFocus`, `utActivity`).
- **Chromium's `autoplay` restarts playback on `load()`**: pause media after `load()` when the stage is hidden.
- **python-xlib**: `get_extension_major` is on `Display.display`; `Display.screen()` is stale after a RandR change (use
  `root.get_geometry()`); Xlib can have read events during a round trip so `add_reader` will not fire for them (drain the
  queue); `struct input_event` is `qqHHi` on 64-bit (uinput).
- **In `api/viewers.py` the method named `list` shadows the builtin inside the class**: under Python 3.14 `list[str]` in a
  signature there raises (use `typing.List`).
- **`requests.post('/auth/token')` follows the 303**: read the cookie with `allow_redirects=False`. Assets added with
  `Scheduler/add_url_assets` are disabled by default.
- **`logger.warning()` never reaches the journal on the kiosk**: the unit's stderr is stored at priority INFO and journald keeps
  only warnings. A line that must survive needs the sd-daemon `<4>` prefix (only when `JOURNAL_STREAM` is set), as claim.py does.
- **croniter 6.0 is wrong across DST with timezone-aware datetimes**: feed it naive local times and use `.timestamp()`.
- **RTC alarms**: the kernel takes UTC epoch seconds; overwriting a pending alarm gives EIO (clear first); an alarm in the past
  is silently accepted and sets nothing (read back); many RTCs cannot hold an alarm 24 h or more ahead. asyncio sleeps on the
  monotonic clock, which does not run during suspend: a wall-clock loop needs a wake-up on resume.
- **Touch input under a real finger**: a `touchstart` preventDefault also cancels the click, so anything on the on-screen keyboard
  must use pointer events (CDP mouse tests hid this). There is no window manager: a screen mode/rotation change does not resize
  the Qt window or CEF's X child by itself (the viewer follows `QScreen.geometryChanged` now). Test real touch with a uinput
  multitouch device (`struct input_event` is `qqHHi`), the X pointer position is the X-level oracle.
- **Vuetify layout items do not register reliably for an element that comes and goes**: the window taskbar leaves room through
  `--taskbar-height` (padding on the drawer and the page), not `v-footer app`.
- **A `@dataclass` with default `eq=True` is unhashable**: in a set it gave a 500 on a websocket upgrade before accept.
- **Never run first-access/reset tests on the shared VM without backing up users.json**: use a throwaway container.
- **Hypercorn does not expose the TLS peer certificate**: pinning needs a listener that does (`websockets` server, port 4443).
  A certificate cannot be removed from a live `ssl.SSLContext` (unpairing reloads the listener and checks the fingerprint per
  connection). TLS 1.3 reports a refused client certificate to the client as an invalid HTTP response (`InvalidMessage`).
- **Do not pop zeroconf entries by hand** (no new "added" event fires on re-announce): filter in the view. mDNS and reaching the
  libvirt VM from a container need `docker --network host`.
- **A regex ending in `$` accepts a trailing newline**: use `fullmatch` for anything that crosses a trust boundary.
- **Uploading a file with an unknown mime type returns 500** (`Asset.media_type` is None): known, unrelated to remote control.
- **Chromium stops painting a window that another window covers, so a splash over a CEF window starves it**: the page never draws
  (not even requestAnimationFrame) and the splash never goes. The viewer disables occlusion handling
  (`disable-backgrounding-occluded-windows`, `disable-renderer-backgrounding`, `disable-features=...NativeWindowOcclusion`), keeps
  the Qt splash on top of each window and closes it when that window's page reports a drawn frame (`utFrame` binding, two animation
  frames); `SPLASH_MAX_MS` is only the safety net.
- **The boot picture is ONE picture in three places** (Plymouth theme, `bootsplash.py`, the viewer page): `bootsplash.py` draws the theme's own
  PNGs (`plymouth/unitotem/{wordmark,spinner}-<w>.png`, copied by debian/rules into the viewer's `static/bootsplash`) with the theme's
  geometry and 1 turn/s; change the artwork in `plymouth/generate-assets.py` only. The theme shows no status line.
- **A white frame before the page (measured on a Pi and on the VM with a 48x48 corner sampler of the root window, ~900k samples/45 s)** had
  two causes: a window is mapped above the splash and unpainted until the event loop runs (the browser takes ~150 ms to create inside
  `show()`: the splash is raised and flushed from `WebviewWindow.on_mapped`, before that), and the splash left when the page had
  *drawn* a frame, which software compositing puts on the window a moment later (`PRESENT_MS`). Pinning black in CEF/Qt was not enough.
- **The Qt splash (`splash.py`) draws the same picture too**: it calls `bootsplash.artwork()` (theme PNGs, still ring), so Plymouth -> bootsplash -> Qt splash -> page show one identical picture; it used to draw thin text and a small arc (one visible frame on a Pi).
- **`bootsplash.py` starts in parallel with X**: it imports and decodes every PNG first (`warm_up`), then waits for the socket and
  connects with a short retry; no `After=start-xorg.service`.
- **Every CEF build ships the proprietary codecs (H.264/AAC), always** (user rule): never build or package a wheel without
  `proprietary_codecs=true ffmpeg_branding=Chrome`; `debian/rules` takes only wheels from `vendor/cefpython-wheels` and fails when there is none,
  it must never fall back to the stock wheel. CEF is built locally (no CI); NEVER delete the build tree (sources, objects, ninja state) on your own, success or failure:
  it makes every rebuild incremental (hours saved) and is kept until the user says it can go. The `build/` dir of the submodule is root-owned when Docker made it:
  delete it with a throwaway container mounting that one path, not sudo. Wheels are not in git (`vendor/cefpython-wheels`, ignored).
- **The build server's scripts are in `tools/cef-server/` (README.md, `sync.sh` pushes/pulls them): edit them there, not on the server.**
- **Builds run on the build server (`alessandro@supercompiler`, a VM with sudo, 128 cores/256 GB, `/nas` for data; was root@ in an LXC before)**: CEF from
  source is in `/nas/cef-build` (`build-all.sh`, re-runnable, as a systemd unit so a lost connection does not stop it; units do NOT survive a reboot),
  the wheels come from `tools/build-cef-wheels.sh start|status|fetch` (`tools/cef-wheels-inner.sh` inside a trixie container: x86_64 native,
  aarch64 CROSS with gcc-aarch64-linux-gnu + multiarch, Python 3.10-3.14, version `147.0+codecs`). `python3-cefpython3` for arm64 is cross built too:
  `TARGET_ARCH=arm64 tools/build-debs.sh cefpython` (no qemu). The checkout's `vendor/cefpython/cefpython3/` holds stale artifacts of an old local build
  (libcef.so, cefpython_py313.so): never copy that directory into a build, only `__init__.py` (the first wheels had the wrong extension module because of it:
  check every wheel with the ELF machine of libcef.so/subprocess/cefpython_pyXY.so). siso was OOM-killed on the 31 GB PC: that is why it moved.
- **Cross-compile everything that can be cross-compiled** (user rule, 2026-10-17): before ANY compilation (C/C++, Python extensions, wheels,
  packages, image parts) first ask whether it can be cross-built from the x86 host (cross gcc/clang, sysroot, multiarch, `dpkg-cross`,
  `--host-arch`), and write down the answer. Choosing QEMU/binfmt emulation is almost certainly the wrong choice; use it only for
  what must execute target code (debootstrap second stage, postinst scripts, tests) and say why.
- **Parallelise everything** (user rule): the host has many cores (20) that are not fast, so use all of them (`ninja -j`, `make -jN`,
  `cmake --parallel`, `xargs -P`, independent builds/wheels side by side) - watch RAM (31 GB) for link steps and Docker CPU limits.
- **Codecs are essential on a kiosk: every build ships H.264/AAC** (`proprietary_codecs=true ffmpeg_branding=Chrome`), no exception, no
  "temporary" stock wheel.
- **Raspberry Pi graphics = ANGLE on Mesa GLES** (`pi` profile in `utils/system/gpu.py`, measured on a Pi 4): without `use-gl=angle use-angle=gles`
  Chromium's GPU process dies at start ("GLES3 is unsupported and ES version fallback is disabled") and everything is software
  (7 fps vs 44 fps). The viewer logs the real `glImplementationParts` at the first frame and warns (journal-visible) when a profile that
  expects GPU compositing does not get it (`EXPECTS_COMPOSITING`, `Cdp.gpu_info`).
- **A silent `str.replace` that finds nothing is a bug you will chase for an hour**: after a merge, code you patch by string may
  have moved (the DevTools binding dispatch did). Assert the pattern is there.
- **Password rules are those of plain `passwd`/`useradd` (user decision, 2026-10-19: the 10-character policy was "too strict")**: always only
  non-empty, no control characters, <= 128 (`passwords.validate_basic`), usernames as Debian's `NAME_REGEX` (lowercase). The stricter policy (10+ chars,
  not the username, not a common word) is opt-in (`security.json`, `Security/set_password_policy`, switch on the Users page) and applies on top. Terminal account stays root (user decision).
- **`WS.disconnect` needs the user**, or `active_users` keeps dead sockets. `/remote/*` refuses loopback senders: test a pairing
  request from another host. A hidden `v-badge` stays in the DOM with its text and a `v-menu`'s content stays after the first
  opening: test the computed display / `aria-expanded`. The scratchpad directory is shared between agents: prefix your files.
- **Working rules from the user (2026-10-20)**: (1) when in doubt, SEARCH THE WEB, always (and the Chromium tree on the build server: `grep` its
  `content/public/common/content_switches.cc` instead of guessing a flag); (2) keep going alone until there is a result; (3) prefer ONE graphics stack
  (X11, or Wayland + a compositor) identical on every board to keep the number of cases low, and split into two stacks only where one board loses
  performance; (4) when the best result is reached and confirmed, build the images for both systems (the OS image is the unitotem-system session's job: send it
  the spec, never touch its A/B state or `config.txt` without telling it).
- **Graphics stack measurements, 2026-10-20 (venus = Intel Alder Lake-P Iris Xe, Mesa 25.0, kernel 6.12; Pi 4 = V3D 4.2, Mesa 26.2). Tools in `tools/gfx-bench/`.**
  - **Tearing on X11 is real and a compositor fixes it, where one can run.** Our kiosk X11 has no compositor and no window manager: a windowed CEF
    surface is presented by a vsynced COPY, not a flip, and tears. On venus `picom` (glx backend, `vsync = true`, no shadows/fading) removed it with no
    measurable cost (4K60 H.264 scaled to 1080p: 61.3 fps, 0 dropped, 82% of a core). On the Pi 4 picom does NOT work: its GL backends need GLSL 3.30 and the
    V3D exposes OpenGL 3.1 / GLSL 1.40 (`GLX_BAD_FB_CONFIG`, `GLSL 3.30 is not supported`), and `--backend=xrender` is worse than nothing (WebGL 33 -> 15 fps,
    video 33% dropped, +50% CPU). picom needs `libev4t64 libxcb-composite0 libxcb-damage0 libconfig11` on top of the image's libraries.
  - **Wayland (cage/labwc, CEF with `--ozone-platform=wayland`) on venus is spectacular**: same 4K60 video at 40% of a core (X11 + picom: 82%), 9 W package
    (22 W), GPU 752 MHz (1371), no tearing (user checked), 60 fps WebGL. `cage` 0.2.0 + `labwc` 0.8.3 + `weston` 14 from trixie run as root with
    `LIBSEAT_BACKEND=builtin` on a VT (`openvt -c 1 -f -s`), X and the manager stopped; cage wants `/usr/bin/Xwayland` (set `WLR_XWAYLAND`). The viewer
    CANNOT run there as it is: it embeds CEF as an X11 child of a Qt window (`SetAsChild(winId)`). A Wayland port means CEF top-level windows (no Qt parent),
    one per output placed by the compositor (labwc window rules / `MoveToOutput`), and the Qt-only parts (splash, screen detection, rotation) redone.
  - **On the Pi 4 Wayland is NOT better for full-screen redraws**: cage, labwc and weston all cap anything that updates the whole 1080p surface at ~30-37 fps (X11:
    60 with MSAA off), only `desynchronized:true` canvases reach 55-60. A native EGL client (`weston-simple-egl -f` under labwc) does 60.4 fps, so the
    board and the compositors can scan out 1080p60: the limit is Chromium's Wayland present path on the V3D (trace: swap is fast, every other BeginFrame has no
    draw, the swap ack comes too late). Flags that did NOT change it: zero-copy, native GMB, overlay delegation, disable-gpu-rasterization, ANGLE gl,
    `WLR_DRM_NO_MODIFIERS`/`NO_ATOMIC`, a different compositor, DRM syncobj off. ANGLE on Vulkan (`v3dv`) breaks every WebGL context. Not investigated further.
  - **Pi 4 WebGL: MSAA is the cost on X11.** A default WebGL context (`antialias:true`, 4x MSAA) that redraws the full canvas runs at 33 fps at ~1080p; with
    `antialias:false` 60. `--webgl-antialiasing-mode=none` (read from the command line by `webgraphicscontext3d_provider_impl.cc`) gives 60 to every page,
    now in the `pi` profile (`utils/system/gpu.py`, verified in the real viewer). **`webgl-antialiasing-mode=implicit` makes every `getContext()` return null**
    (it looked like 60 fps because the rAF counter kept running). `--disable-gl-multisampling` no longer exists. The WebGL Aquarium (30000-fish demo) is GPU-bound
    on the V3D and does not change (28 fps at 1 fish, 12 at 5000, 1080p). Where the time goes on X11: `egl::Surface::swap` blocks 10 ms on average (27 max)
    in the GPU process while draw/composite cost ~2 ms.
  - **Measurement traps (each cost an hour)**: a stand-alone `requestAnimationFrame` counter keeps ticking when the WebGL context is null: count draws and
    check the context; a CDP `/json/new` window is NOT the screen size (X11 popup: 1919x992; labwc without a rule: ~913x931; fullscreen via
    `Browser.setWindowBounds` clamps) so print `innerWidth x innerHeight` and fix the size (labwc rule in `tools/gfx-bench/labwc-rc.xml`); a stray video tab keeps
    decoding in the background and pollutes the next measurement; `pkill -f "wlcef.py"` inside an ssh command line kills its own shell (use a script file).
    Never measure while the system session builds on the Pi (its `CPUWeight=1` on the services makes every number wrong).
  - **Pi 4 and 4K**: X11 offers 3840x2160 and 4096x2160 only at 30 Hz; 4Kp60 needs `hdmi_enable_4kp60=1` in `[pi4]` of `config.txt` (HDMI0 only, more power/heat). The
    image does not set it (nobody decided to); `config.txt` is the system session's.
  - **venus**: VA-API works with the 25.2.3 iHD driver and the firmware already installed (H.264 4K60 and HEVC 720p, `VaapiVideoDecoder`); i915 reaches 1450 MHz
    and 90 C with the 30000-fish Aquarium at 4K and never raised a GPU throttle flag (`throttle_reason_*`); a second 4K monitor on HDMI-2 was seen as connected
    but NOT enabled by anything (the manager adds the window once `xrandr --output HDMI-2 --auto` turns it on).
- **THE KIOSK RUNS WAYLAND (labwc) AND NOTHING ELSE since 2026-10-20 (user decision "via X11, andiamo tutto su Wayland"). Everything X11 in this file is history.**
  Pieces: `unitotem-compositor.service` (shipped by unitotem-viewer, enabled by the image: labwc as root on VT1 with libseat's builtin backend, `RuntimeDirectory=unitotem`,
  the compositor's private `XDG_RUNTIME_DIR=/run/unitotem/xdg` (the manager gets `WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0`, an absolute path, and root's `XDG_RUNTIME_DIR=/run/user/0`), `labwc --merge-config` with `XDG_CONFIG_DIRS=/etc/unitotem/xdg:/run/unitotem/labwc/node:/run/unitotem/labwc/viewer`; the base config is
  `debian/labwc-rc.xml`); `utils/labwc.py` (one configuration FRAGMENT per writer, `reload()` = SIGHUP: the viewer writes the window placement, the node the touch mapping);
  the viewer (Qt wayland only for QScreens and splash; browser windows made by DevTools `Target.createTarget`, see below); the node (`system/screens/wayland.py`: `wlr-randr --json`
  for outputs/power/rotation names, `wlopm` for blanking, EDID from `/sys/class/drm/card*-<output>/edid` for the identity; `touch.py`; `vnc.py` with wayvnc); `bootsplash.py` in QtGui.
  Output names are the DRM connector's (`HDMI-A-1`, not RandR's `HDMI-1`): saved assignments/settings follow through `utils/outputs.find_renamed`. The screen mechanism formerly called `x` is
  `output` (old files are read through a validator). The web UI lost the "click a monitor to move the window onto it" (`set_bounds`): the compositor owns window geometry.
  Needs in the image: `labwc`, `qt6-wayland`, `wlr-randr`, `wlopm`, `wayvnc` (all Depends/Recommends of the debs) and the patched CEF/cefpython wheels. No Xorg, no xauth, no python-xlib.
  Dev tooling: `tools/wayland/session.sh` (runs the real compositor unit and the manager from a copy of the code on a board, see `tools/wayland/README.md`).
  Measured with the real viewer under labwc, 1080p: Pi 4 canvas2d and WebGL2 **60.2 fps** (X11 was 60 with MSAA off but tearing), venus 60/60, no tearing.
  - **The Pi's "Wayland caps at ~30 fps" was Chromium's buffers, not the compositors.** Chromium's `BufferQueue` asks for scanout buffers, Mesa's
    `gbm_bo_create_with_modifiers()` then allocates them **LINEAR** (`create_with_modifiers2 flags=0x1`; the V3D can only scan out linear), and labwc/cage/weston present such a
    surface two vblanks after every commit (`presented` seq jumps by 2, 31 flips/s, V3D only ~15% busy). A native EGL client gets UIF-tiled buffers (modifier `0x0700000000000006`)
    and does 60. Fix: no SCANOUT flag. Proven with `tools/wayland/gbm-noscanout.c` (LD_PRELOAD) and shipped as CEF patch `tools/cef-server/patches/0002-gbm-no-scanout-buffers-env.patch`
    (a 4-minute arm64 relink; reads `UNITOTEM_NO_SCANOUT_BUFFERS`, which the viewer sets on the `pi` profile under Wayland). Diagnosis tools: `WAYLAND_DEBUG=1` on the viewer
    (`zwp_linux_buffer_params_v1.add` modifier, `wp_presentation_feedback.presented` sequence numbers), `/sys/kernel/tracing/events/drm/drm_vblank_event_delivered`, `drm-engine-*` in
    `/proc/<pid>/fdinfo`, Chromium trace categories `wayland,viz` (`tools/gfx-bench/wltrace.py`). Flags that changed nothing: `disable-gpu-vsync`, `in-process-gpu`;
    `disable-frame-rate-limit` only makes rAF lie (400 "fps").
  - **Windows: CEF's own windowing (Views), through the `cefpython` branch `wayland-views` (docs/cef-wayland/, api/Window.md of the fork).** A Wayland client cannot choose its output, and the windows
    `cefpython.CreateBrowserSync` makes on Wayland (Chrome-style, parentless `WindowInfo`) have empty `app_id`/title, are never destroyed by `CloseBrowser` (a zombie toplevel that labwc moves onto
    the remaining output after an unplug) and unknown to `Browser.getWindowForTarget`. The viewer (`window.py`) therefore opens `cef.BrowserView` + `cef.Window.CreateTopLevelWindow(delegate)`:
    frameless, `app_id` `org.unitotem.viewer`, and `SetFullscreenOnDisplay(display id)` = `xdg_toplevel.set_fullscreen(output)`, so the COMPOSITOR is told nothing (no key binding, no uinput, no
    wlrctl). `cef.Display.FindByOutputName` matches the `(DP-1)` that wlroots compositors append to the `wl_output` description; the id changes at every plug and **CEF learns about a new output
    a moment after Qt does**: a window made right away went fullscreen on the wrong output (measured: 640x480 DP-1 plugged back, the window landed on DP-2), so `show()` waits (up to 6 s) until
    the browser knows the output. Needs the CEF patches `tools/cef-server/patches/cef/0001..0005` (`SetFullscreenOnDisplay`, `GetLabel`, frameless windows on Wayland): without them `cef.Window` lacks them.
    Verified on venus with two outputs: a window per output with the right size, `wlr-randr --output DP-1 --off/--on` closes (windows 2 -> 1) and re-opens (-> 2, 640x480 on DP-1), 60.2 fps canvas/WebGL2.
  - **The last output of a Raspberry Pi cannot be disabled by `wlr-randr --off`** (it stays enabled): `wayland.set_power` blanks it with `wlopm` instead. Qt wayland does emit `screenRemoved` / `screenAdded` when an output
    is disabled / enabled (checked with `tools`-style script), no `udevadm trigger` or debugfs `force` is needed to test hot-plug on a running compositor.
  - **Test traps**: labwc `-d` logs `[map] identifier=, title=` per window; a rule or keybinding written with the wrong quoting makes labwc silently load the DEFAULT config (check
    "error parsing config file"); Chromium rewrites its `/proc/<pid>/environ` (it looks empty), check the environment with a shim that prints `getenv`; `umount` of a bind-mounted
    libcef says "busy" while the viewer runs (stop it first); `raf.py -1` runs in the viewer's own window (it stays on the test page).
- **Audio never worked on the image until 2026-10-20 (found testing a Pi 4, see WAYLAND-IMAGE.md "Audio").** Two causes: Debian's PipeWire units have `ConditionUser=!root` (never started for root: needs an empty
  `ConditionUser=` drop-in in `/etc/systemd/user/*.d/` for pipewire/pipewire-pulse sockets+services and wireplumber), and the manager's unit had no `XDG_RUNTIME_DIR` (libpulse, in the browser and in pulsectl, looks for `$XDG_RUNTIME_DIR/pulse/native`;
  `10-wayland.conf` sets `/run/user/0`). A recording of a sink MONITOR (`pw-record --target <sink> -P stream.capture.sink=true`) does not show the sink's volume or mute (taken before them): do not use it to test volume. `pw-record` needs SIGINT to finish its file and a shell leaves SIGINT ignored for
  background jobs: reset the signal (python `signal.signal(SIGINT, SIG_DFL)` then exec). Chromium's audio process links libpulse AND libasound; with no pulse server found it produced no sound at all (no ALSA stream either), it did not fall back.

- **CEF Views on Wayland (2026-10-21, design note `docs/cef-wayland/design.md`, series `tools/cef-server/patches/cef/`, bindings in vendor/cefpython branch `wayland-views`).** A Views window (`cef.Window` +
  `cef.BrowserView`) is the way to get a frameless, closable, per-output window on Wayland; `WindowInfo()` ends up as a zombie Chrome-style toplevel. Traps found: (1) **CToCpp wrappers**: the CEF
  library hands out a NEW wrapper object every time (`window.AsPanel()`, a delegate argument): never keep a raw pointer from a temporary `CefRefPtr` (use-after-free, "kWrapperType == wrapperStruct->type_"
  FATAL) and never use the address as identity (`CefView::IsSame`). (2) The DevTools HTTP server is served by the message loop: a script that polls `127.0.0.1:<port>/json` from the thread that
  pumps `MessageLoopWork` deadlocks (poll from a thread). (3) Display ids on Wayland are the `wl_output` registry names and change on every unplug/plug; the output NAME is only in the label
  (`CefDisplay::GetLabel`, `... (DP-1)`); a client does not know where its toplevel is (bounds start at 0,0), `CefWindow::GetDisplay` needed the entered-output patch. (4) labwc ignores the output of
  `xdg_toplevel.set_fullscreen` for a toplevel that is already fullscreen: leave fullscreen first (done inside `SetFullscreenOnDisplay`). (5) X11 does not support a target display (DCHECK).
  (6) A forced debugfs connector (`echo on > .../force`) is undone with `echo off > .../force` AND `echo detect > /sys/class/drm/cardN-X/status` (`unspecified` is rejected). (7) Test scripts
  must not be called `bisect.py`/`random.py`: Python's stdlib imports them. (8) `clang-format` of the CEF tree: `buildtools/linux64-format` + `depot_tools` in PATH, needed by `make_distrib.py`.
