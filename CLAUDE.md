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


# Where the details are

CLAUDE.md is kept short on purpose. The rest is in files that are read when the area is touched (search them, do not skim them blindly):

- `docs/design-notes.md`: how each feature is built and decided: on-screen keyboard, touch panels (mapping, screensaver, refresh, zoom, guided setup),
  window layout and panes, accounts/first access/VNC/suspend, remote control (pairing, transfer, windows, file cache), notifications.
- `docs/gotchas.md`: every trap, measurement and user decision found so far, in the order found (CEF/codecs/GPU, Wayland/labwc, rotation, touch,
  screens/DDC, keyboard layouts, updates, lockdown, language, PDF...). `grep -n -i <word> docs/gotchas.md` before working on something.
- `TODO` (read in full at the start of a session) and `docs/TODO-archive.md` (what was done), `VM-TESTING.md` (the test VM), `docs/cef-wayland/`, `docs/layout-and-ticker.md`,
  `docs/rotation-benchmark.md`, `docs/cef-lean-build.md`.

# Rules

Everything above describes *what* UniTotem must do. This section describes *how* to work on it.
These rules are binding.

## Language
All code, comments, identifiers, log messages, commit messages and repository documentation are
written in **English**, without exception. This holds even when the conversation itself is in
another language.

## Progress tracking
Project status and planning live in the `TODO` file at the repository root, not only in commit
messages. It is a SHORT list, read in full at the start of every session (not skimmed: a part
that is skipped is a thing that gets forgotten). It is structured as:

- `=== CURRENT WORK ===` — what is being worked on *right now*, with enough detail to resume
  without re-deriving the context (what was verified, what is still open, which files are involved).
- `=== OPEN ===` — everything that is still undone, grouped by who has to move it: to build (mine),
  waiting for the user, waiting for unitotem-system or the next deb set, needs hardware I do not
  have; plus a line saying what was closed so nobody asks again.
- `=== BACKLOG ===` — the long-standing wish list. Entries already done are kept, commented out
  with `<!-- -->`; never delete them, they are the project's history.

The running log of what was done, measured and found lives in `docs/TODO-archive.md` (newest
first, never deleted, entries there may be superseded by later ones). When something is finished
move its description there; when something new is found open, add it to `OPEN`.

`TODO` is updated *during* the work, not only at the end: when a task is picked up, when something
is verified, and when the session stops.

## Gotchas and new rules
Every gotcha discovered while working (a non-obvious environment behaviour, a dead end that must not be retried, a trap that cost real
debugging time) is appended to `docs/gotchas.md` as soon as it is found; a commit message is not enough, nobody reads those at the start
of a session. A new rule from the user that must be obeyed in EVERY session is written as a one-liner in "Binding rules" below (with the
detail in `docs/gotchas.md`). Design notes of a feature live in `docs/design-notes.md`.

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

## Binding rules (one line each; the story behind each is in docs/gotchas.md)
- **No command may ever open a graphical password prompt**: every ssh/scp/rsync/sshpass call carries `SSH_ASKPASS_REQUIRE=never` in its own environment, `-o BatchMode=yes`
  for key hosts, `sshpass` + `-o PreferredAuthentications=password -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no` for password hosts, `sudo -n` only; say so in every subagent brief.
- **Never leave anything half done**: done = the user can see and use it on the device they look at; hiding a control because the test board lacks something is half done.
- **Names are data, never code; structure over escaping**: validate with `fullmatch`, argument lists never a shell, `--` before positional values, XML via ElementTree (`utils/labwc`),
  JSON via `json.dumps`, Jinja autoescape on; check every new place where a value reaches a command, file or page.
- **Wording**: the product is finished, so no provisional text ("not yet", "for now", "interim"); comments and strings say what the code does, short, and never name example systems; slot names in capitals; everything English.
- **Cross-compile everything that can be cross-compiled** (QEMU only for what must execute target code, and say why); **parallelise** (all cores); **every CEF build ships H.264/AAC**; never delete the CEF build tree.
- **When in doubt, search the web** (and grep the Chromium tree on the build server); keep going alone until there is a result; one graphics stack (Wayland/labwc) on every board.
- **Ports 80/443 never change**; the manager stays running after a test session; deploy to boards with `tools/ticker/deploy-board.sh` and check `NRestarts` and `ss -ltnp | grep :443` afterwards.
- **The websocket API is id-less**: no correlation ids; every state change answers with the event its page listens to; run `tools/gen-api-types.py` after changing an API function.
- **Talk to systemd through its interfaces** (sd_notify, D-Bus, signals), never `systemctl`/`loginctl` subprocesses; no machine-touching code in the core (it goes in the node plugin).
- **Runtime data never under the code directory; nothing image-owned under `/etc/unitotem`** (state only; image files go under `/usr/share/unitotem`).
- **Never `pkill -f <pattern>`** that appears in the caller's own command line (use a pid or `[p]attern`); never test first-access/reset on a shared board without backing up users.json.
- **Do not touch the first-boot welcome page's look**; terminal/VNC/DevTools are admin only, one-time token, secure channel only.
