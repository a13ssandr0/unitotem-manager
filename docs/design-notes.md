# Feature design notes

Moved out of CLAUDE.md (2026-11-01) so that file stays short. Everything here is still binding for the area it describes: read the section before changing that area.

## On-screen keyboard (touch panels)
Per window (`Viewers/set_keyboard`, kept per screen in viewer_settings.json, ON by default since 2026-10-26): enabled, the position of the
button that opens it, a layout. It opens by itself when a text field gets the focus in ANY frame of the page and closes when
the focus leaves (unless the visitor opened it with the button). Pieces: `plugins/unitotem_viewer/cdp.py` (the viewer's
DevTools connection: injects a focus watcher into every frame, cross-origin ones included, which are separate targets,
and delivers text with `Input.insertText` and keys with `Input.dispatchKeyEvent`), `webui/src/keyboard.js` (simple-keyboard
UI drawn inside the page's rotated container, loaded by `boot-screen.html` on demand), and the window id in the page address
(`boot-screen.html?window=N`), which is how a DevTools target is tied to a window. The keyboard can be moved and resized, partly off the screen (160 px of its header stay visible, so what it covers can be reached)
and is clamped to a minimum size after every gesture, every size change and every rotation.

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

## Window layout (area, panes)
Design and contract with the RSS ticker: `docs/layout-and-ticker.md`. Per window, kept per screen like the keyboard (`"<viewer id>|<screen name>"`, so it survives an unplug) in
`window_layouts.json` (`utils/models/layout.py`, core; backup part `layouts`; a window with no entry has the default). Configured only from the Viewers page
(`WindowLayout.vue` + `PaneTree.vue`: preview of the window in content space, the area dragged/resized/typed with presets such as "centre third (vertical screen)",
splitters dragged, per-pane playlist, split/close buttons); **admin** for `set_area`/`split_pane`/`close_pane`/`set_split_ratio`, **scheduler** for what a pane shows.
- **`area`** = the content rectangle (percent of the window in CONTENT space, after the window's orientation, clamped never refused, min 5 %) + `margin_color` (a plain CSS colour
  only). It replaces the old xinit trick of shrinking the window to a third of the height on the 55" vertical panel. The on-screen keyboard and the screensaver still cover the
  WHOLE window (the screensaver overlay is `#stage`, which has the margin colour behind `#area`). Audio output stays per window.
- **`tree`** = `Leaf(id)` / `Split(dir h|v, ratio 0.1..0.9, a, b)`, max 8 panes and 4 levels, ids never reused. The first leaf in tree order is the MAIN pane: its playlist is the
  window's assignment in `viewer_assignments.json` (unchanged format, so `assign_playlist` without `pane_id`, old files and old clients keep meaning "the window"); the other
  panes' playlists are `WindowLayout.panes`. Closing the main pane promotes the next one and its playlist becomes the assignment (nothing that stays on screen restarts).
- **Operations are pure functions that return a new layout** (`split_leaf`, `close_leaf`, `set_ratio`, `set_area`, `with_pane_playlist`); `model_copy(update=)` does NOT validate,
  so they all go through `_rebuild`. A split is addressed by a PATH of `a`/`b` from the root (`''` = root) because splits have no id; `is_default` is "area full and tree == Leaf(1)".
- **One place makes the loops match the layout: `WebviewManager._sync_window`** (assign, unassign, split, close, restore after restart/replug/restore-from-backup all end there).
  `PlaylistLoop` targets are `(viewer, window, pane)`; `Show`/`SetAssetAudio` carry `pane`; `SetWindowLayout {window_id, area, tree}` is pushed (with `nocache`) when a window first appears and on
  every change. The viewer says it understands panes with `layout: true` in its `WebviewInfo`; a viewer that does not is driven as one pane and never gets the new messages (an old
  client would show a pane's asset over the whole window).
- **Page (`boot-screen.html`)**: `#stage` (whole window, margin colour) > `#area` (percent rectangle) > `#panes` + `#ticker-host`. One `.pane` per leaf, all FLAT and absolutely
  positioned in percent of `#panes`: never nest them or move an element to another parent, the iframe/video would reload (a surviving pane keeps playing through splits, closes and splitter
  drags: measured). `class Stage` = what `#stage` used to be (containers, zoom, volume, refresh, `current`); the old globals (`show`, `setAssetAudio`, `refreshPage`, `syncMedia`,
  `showSaver`, `current`) still work and take an optional `pane`. The first pane is in the static HTML (the first frame must not wait for a script) and the boot logo is
  sized with container units (`.boot {--boot-em: 12.5cqw}`, an eighth of the PANE's width: identical to Plymouth for a full-window pane). A new pane starts blank and is parked on the idle page
  by the manager. A command for a pane the page does not know yet (commands of a window can overtake its layout) is kept (latest per pane) until the next layout. The page sanitises what it is told
  (areas clamped, a tree that is not valid is ignored). `setTickerPlacement(mode, pos)` sets `#area[data-ticker|data-ticker-pos]` for the ticker package.
- **Gotchas**: `touch-action` for the pinch switch is per pane (and `html` allows it if any pane does); the DevTools focus reports carry a per-frame id and `pagehide` says "no longer focused" (several
  same-origin iframes share one DevTools session, and a closed pane's iframe never sends a focusout); the playlists of panes of OTHER nodes count as "used" when a client is unpaired
  (`bundle.plan_for`), the panes' playlists and the layouts of the leaving client travel with it (`bundle.collect_windows`, see Remote control); `Viewers/layout` is a read AND the broadcast after
  every change, `close_pane` answers `Viewers/list` (the main assignment may have changed); tests that touch `utils.backup`/`remote.bundle` need `/var/lib/unitotem` (use a conftest-free
  plugin that skips the mkdir, as `tools/gen-api-types.py` does, on a dev host).
- **Turned and mirrored windows (the 55" vertical panel)**: the area, the panes, the screensaver overlay and the keyboard all live in CONTENT space inside `body[r]`/`main[f]`, so a window
  turned by the content orientation (`Settings/Display/set_orientation`, 1/3 quarter turns) or by the compositor (`wlr-randr --output X --transform 90`: the window becomes 1080x1920,
  orientation stays 0) is laid out the same way: area `top 33.33, height 33.34` is the middle third of the LONG side. Checked on the Pi with DevTools pixels and real
  `Input.dispatchMouseEvent`/`dispatchTouchEvent` at known points: the right pane (or the stage, in the margin) receives each press for 90/270 and for the transform, `#stage` is the whole window
  under the screensaver, the keyboard is rotated with the content. `--transform 90` leaves the 1920x1080 mode alone (safe with the HDMI capture card) and `--transform normal` restores it;
  start a `sleep N; wlr-randr ... --transform normal` safety net on the board before trying it.
  The VIEWER reports `orientation` and `flip` of every window in `WebviewInfo` (and again when they change); the manager keeps them per screen in `viewer_settings.json`
  (`orientations`, `flips`, unknown keys are ignored by older versions) and sends them when a window appears, so a vertical panel stays vertical after a reboot or a replug (before, they were
  lost, and read by list position instead of window id). The editor preview follows them (aspect swapped, mirrored, presets named "portrait screen" or "of the height").
- **`keyboard.js` rescales its panel when the layer changes size** (a turned window swaps width and height): it used to keep the pixels and be clamped, so in a turned window it stayed in the middle.
- **Practical limit of panes with video on a Pi 4** (measured 2026-10-25 on an idle board, 1080p window, software H.264 decode, whole process tree, 40 s): 1 pane 90 % of one core, 2 panes 155 %, **4 panes 263 % and
  30 fps with 0 dropped frames**, 6 panes 370 % and 26 fps, 8 panes 376 % and 20 fps (the CPU is full, frames are late rather than dropped). Pictures and static web pages cost ~1 % per pane (4 web panes 4.7 %, with a
  CSS animation 4.3 %); the animated BOOT logo costs 28 % for one pane and 73 % for four (a pane with no playlist shows the idle page, not the logo, so this only matters for a pane left on `boot`). The editor
  shows a hint above 4 panes; it cannot know which panes play video, so it is not a detector. Do not measure while something else compresses an image: the first runs here were useless for that reason.
- **Tests of the layout**: `tests/test_layout.py` (model, operations, file), `tests/test_layout_controller.py` (per-pane assignments, restart, unplug/plug under a new window id,
  renamed outputs, close/promote, old viewers, backup part, API answers), `tools/ui-panes.py` (the page's logic in a local headless Chrome: zoom/pinch/refresh/screensaver per pane,
  junk layouts). Verified for real on a Pi 4 (1080p, DevTools screenshots): full window, centre third with a margin colour, 2 and 4 panes with different playlists, ratio/split/close while two
  videos play (no `Show`, same element, `currentTime` keeps going), promotion of a main pane, restart of the manager with a saved layout, per-pane volume, screensaver overlay over the whole window,
  backup/reset/restore of the part, the Viewers page driven with real mouse events (14 checks) as admin and as a scheduler-only user.
- **The letterbox colour of an image used to be sampled by scaling it to ONE pixel**: on the Pi 4 that returned white at some point (a 64x36 canvas of the same picture was right), so every
  picture that did not fit its pane got white bars. It is averaged over a 16x9 canvas now (`Stage.applyDominantColour`).
- **Drags in the Vue editor listen on `window`, not on the element that was grabbed**: with a synthetic mouse (DevTools) `setPointerCapture` succeeded (`hasPointerCapture` true) and the events
  still went to the element under the pointer, so nothing moved. `tools`-style UI tests must use `Input.dispatchMouseEvent` with `buttons` set; grabbing the middle of the area hits a gutter.
- **A Pi that is building an image is not a place to measure** (an `xz` at 350% CPU and an rsync in D state made the manager's imports take 5 minutes and the first start time out at 300 s,
  and 1080p VP8 drop half its frames): everything here was checked for behaviour (counts of `Show`, element identity, `currentTime`), not for speed.

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
  UniTotem-only users (a second, separate password store: the GUI password and the SSH/terminal one would differ and the second never changes), whole-file
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
removes from the server only what nothing else uses (`plan_leave`). The windows travel too (2026-11-01, user decision): the manifest has an optional `windows` map (per screen name: audio output, keyboard, screensaver, refresh, language, orientation, flip and the layout with its ticker; `bundle.collect_windows`), stored on arrival under the new viewer id (`apply_windows`: validated one by one, an invalid entry is left out and never fails the transfer, all-or-nothing with `undo`, the panes' playlists remapped to the ids they got on arrival), the sender drops its copy once the other node has everything (`WebviewManager.drop_viewer_settings`) and the windows of the receiving viewer are told everything again (`windows_arrived`). The playlists of the panes (not only the main ones) go with the leaving node (`plan_leave(panes=)`). A node that sends no `windows` (an older release) is read as having none. Unit-tested (`tests/test_remote_windows.py`), NOT run between two real nodes. All Remote functions are admin only.
**The files of a client's windows (2026-11-01, user decision; found by reading the code)**: after adoption the uploads live on the server, yet the windows of a client ask THEIR OWN manager for `https://localhost/uploaded/<name>` (`show_payload`): before this, a paired client could show web pages only, never an uploaded image, video or PDF. Now the server sends each client the files of the playlists assigned to its windows (main and panes) as a `kind: cache` transfer on the same link and with the same staging/hash checks (`utils/remote/filecache.py`): the client says what it has (`cache-have`: name -> SHA-256, on connect and after each transfer), the server sends the difference and a `keep` list (everything wanted: the rest of the client's cache goes), the client keeps them in `/var/lib/unitotem/remote-files` (not in a backup; emptied when the node leaves its server), served by `/uploaded` after its own uploads (`UploadedFiles` in main.py), and the server shows again what each pane was playing once they arrive (`refresh_viewer`). What starts a look: a client's `cache-have`, and `utils/changes.py` (`changed()` is called by every save of a playlist, of the playlists' index, of the assignments and of the layouts; the service debounces it by 1.5 s). The client keeps playing what it has if the server goes away. An older client sends no `cache-have`, so it is never sent anything; an older server never sends a cache transfer. The `/pdf-view` page of a client reads the PDF from `/uploaded` too, so PDFs work there as well. Unit-tested (`tests/test_remote_filecache.py`: a full transfer through two channels); on Pi A only the serving from the cache folder was seen, NOT a push between two real nodes.

## Notifications
`utils/notifications.py` is the ONLY way to raise one: `notifications.notify(key, level, title, text, source=, audience=, link=)` and
`resolve(key)` (plugins import the core, never the opposite). Same key and same content does nothing (a dismissed one stays
dismissed), changed content updates it and makes it unread again. Audience = the capability that may see it (`admin` for anything
about the machine or the network). `link` must be a path of the web UI. State is in `/var/lib/unitotem/notifications.json` (not
in the backup, compatible with the previous version). API `Notifications/list|mark_read|dismiss|dismiss_all`, pushed per user
because the list differs per capability; reading and dismissing are PER USER (`read_by`/`dismissed_by`: what one user dismisses stays for the others, and a notification is hidden for good, and its sender told, once every user who could see it has dismissed it; the legacy `read`/`dismissed` flags mean everybody, so an older release's file still reads). UI: the bell in the top bar. Producers: suspend refused/skipped,
update available, remote control (pairing requests, client gone, server unreachable), plugin load failure.
**Desktop notifications (org.freedesktop.Notifications)** also feed the bell: the node plugin (`system/desktop_notifications.py`) OWNS the
well-known name on the user (session) bus and on the system bus, each only when the name is free (a desktop with its own daemon keeps it), so
anything that runs `notify-send` is not lost on a kiosk with no daemon. Mapping: summary -> title, body (markup removed) -> text, app name ->
source, urgency critical -> `error` else `info`, audience `admin`; `replaces_id` updates, `CloseNotification` resolves, a dismissal in the web
UI answers the application with `NotificationClosed(reason 2)` (`notifications.listen_closed`), at most 20 per minute per application. The system
bus needs the policy file `debian/org.unitotem.Notifications.conf` (root may own the name, anybody may send). A GUI application only reaches it
if the manager's account has a session bus (`/run/user/0/bus`): the image must provide one (unitotem-system), otherwise only system-bus senders arrive.
