# Monitor control via CEC, DDC/CI and X blanking — execution plan

Self-contained, written to be picked up cold in a later session. **Nothing here is
implemented yet.** This covers the `TODO` backlog entry "Add display power control"
(CEC and DDC/CI only; GPIO and Serial stay in the backlog).

---

## 0. State and constraints

**Start condition:** step 1 touches `manager/webview/controller.py` and must wait until
that directory is free. Steps 2-7 do not touch `manager/webview/` at all.

**External prerequisite — DONE.** The kiosk image has been migrated from Debian 12
bookworm to Debian 13 trixie. `ddcutil 2.2.0` and a newer `v4l-utils` now come from the
archive, so the **DDC/CI** mechanism is unblocked and no backport is needed.

Two consequences to check rather than assume when implementing: the test VMs may still
be running the **old bookworm guest**, in which case they no longer represent the product
and must be rebuilt from the new image before any result counts; and trixie changes the
system Python, which `debian/rules` uses to derive `CEF_PYTAG`, so the cefpython wheel
must match the new interpreter.

The version table is kept because it is why the migration was chosen, and because the
runtime version gate still matters for any node not yet on trixie:

| Suite | ddcutil |
|---|---|
| bullseye | 0.9.9-2 |
| bookworm | **1.4.1-1** — too old; no 2.x in bookworm or bookworm-backports |
| **trixie** (stable) | **2.2.0-2** — what we need, from the archive |
| trixie-backports | 2.2.7-1~bpo13+1 |
| sid | 2.2.7-1 |

Verified on packages.debian.org. bookworm-backports has no ddcutil at all, which is why
the source-backport option was dropped in favour of the image migration.

**What can land before trixie:**

| Step | Depends on trixie? |
|---|---|
| 1 — re-key assignments on `screen_name` | No. Pure Python in `controller.py`. |
| 2 — timers (bug fixes, action registry, `screenctl.py`) | No. `python-crontab` only. |
| 3 — `manager/tests/` + pytest | No. Fixtures are static text. |
| 4 — hardware layer | Partly. CEC and X paths are independent and testable. The DDC code can be written and unit-tested but cannot be exercised on-image until trixie. |
| 5 — API `Settings/Display/Screens/*` | No. DDC simply reports unavailable. |
| 6 — Web UI | No. Renders whatever the probe reports. |
| 7 — packaging + docs | Partly. `Recommends:` and the `i2c-dev` autoload are independent; the `debian-kiosk.md` section coordinates with the migration. |

**Keep the runtime version gate regardless.** Parse `ddcutil --version`; if < 2.0, mark
DDC unavailable with the reason surfaced in the UI ("ddcutil 1.4.1 found, 2.0+
required"). On a bookworm node that is correct behaviour; on trixie it never triggers.

**Unverified, flag when implementing:** the fixtures below come from ddcutil **2.2.5**,
trixie ships **2.2.0**. Drift within 2.2.x is unlikely but untested — the parsers must
accept both the `DRM connector:` spelling (`detect --brief`) and `DRM_connector:`
(plain `detect`). Trixie's `v4l-utils` version is unverified. For the migration task,
not for us: `debian/rules` derives `CEF_PYTAG` from the venv's Python, so a trixie
Python bump affects cefpython wheel selection.

---

## 1. Verified research findings

### 1.1 DDC/CI — measured on real hardware

Dev host: X.Org 24.1.10, NVIDIA RTX 2060S (proprietary driver), ddcutil 2.2.5, three
monitors — Philips 246V5 on DVI-D (bus 7), LG Ultrafine on HDMI (bus 8), LG Ultrafine
on DP (bus 9).

| Finding | Evidence |
|---|---|
| **`ddcutil detect`'s verdict is not a capability signal** | The Philips is reported `Invalid display` / `DDC communication failed`, yet answers `0x10`, `0x60`, `0x62`, `0x8D` correctly. Only `0xD6` genuinely fails. The verdict is just a probe of `0xDF`. Capability is **per-VCP-code, not per-display**. |
| **Address by `--bus`, never `-d`** | `ddcutil -d 1 getvcp D6 60 62 8D` → three "Maximum retries exceeded". `ddcutil --bus 8 getvcp --terse D6` → correct, 5/5 runs. `-d` re-runs detection per call. |
| **One code per call** | The batched 4-code call failed; single-code calls succeed. |
| **Timings** | `capabilities` **31.0 s** (exit 1); `detect --brief` **5.6 s**; `getvcp --terse --bus N` **0.40 s** |
| **Unsupported code is distinguishable** | `VCP AA ERR`, exit 1. Supported → exit 0. Bad bus → `Bus /dev/i2c-30 does not exist.`, exit 1. |
| **No JSON mode** | No `--json` in 2.2.5. `libjansson` is linked but used only for ddcutil's internal capabilities cache. Text parsing it is. |
| **Our own capability cache is mandatory** | The kiosk root is `ro` (`kiosk/files/fstab-amd64`: `LABEL=ROOT / ext4 defaults,noatime,ro`) and the unit sets no `HOME`, so ddcutil's `~/.cache/ddcutil` cannot be relied on — the 31 s cold cost may be paid every time. |

**DDC power-on caveat:** `0xD6 <- 04/05` reliably powers a monitor down, but many
monitors stop clocking DDC once in DPM off, so `0xD6 <- 01` never arrives. Untested (it
would have powered off the user's monitor). Treat DDC power-**on** as best-effort and
always pair it with another mechanism.

**Use the CLI, not a Python binding.** `libddcutil` bindings are unpackaged, the ABI
moved between 1.4 and 2.x, and a hung I2C transaction inside the manager process is far
worse than a subprocess we can time out and kill.

### 1.2 CEC — verified against `cec-ctl` 1.32; no CEC hardware available

- **`cec-ctl` from `v4l-utils`**, driving the kernel CEC API (`/dev/cec*`). Not
  `libcec`/`cec-client`: that targets Pulse-Eight USB adapters, which the kernel already
  exposes via `pulse8-cec.ko` (confirmed present in the module tree alongside
  `rainshadow-cec`, `extron-da-hd-4k-plus-cec`, `seco-cec`, `cros-ec-cec`, `cec-gpio`
  and the `cec` core).
- Options confirmed in `cec-ctl --help-all`: `--standby` (0x36), `--image-view-on`
  (0x04), `--text-view-on` (0x0D), `--active-source phys-addr=` (0x82),
  `--give-device-power-status` (0x8F), `--report-power-status` (0x90; states
  on/standby/to-on/to-standby), `--user-control-pressed
  ui-cmd=volume-up|volume-down|mute` (0x44 with 0x41/0x42/0x43),
  `--user-control-released` (0x45), `--give-audio-status` (0x71),
  `--set-audio-volume-level` (0x73), `-A/--list-devices`, `-d/--device`, `-D/--driver`,
  `-p/--phys-addr`, `-e/--phys-addr-from-edid <path>`, `--playback`, `--timeout <ms>`.
- **CEC cannot select an arbitrary input.** Only "switch to *me*" (Active Source 0x82 /
  Set Stream Path 0x86). "Switch to HDMI 3" is DDC/CI-only. Do not fake this in the UI.
- **CEC volume is relative** (repeated pressed/released). Absolute (0x73) is CEC 2.0 and
  rarely implemented. Mute is a toggle.
- **`cec-ctl` lies about its exit code.** `cec-ctl -d /dev/cec0 --playback` printed
  `Failed to open /dev/cec0: No such file or directory` and returned **0**.
  `--list-devices` also returns 0 with empty output when no adapter exists. Parse
  stdout/stderr; never trust the exit code.
- **Which GPUs have CEC:** Raspberry Pi `vc4_hdmi` (the arm64 kiosk already force-loads
  `vc4`/`v3d` via `/etc/modules-load.d/rpi-gpu.conf`, `kiosk/stage2-configure.sh:97`),
  some SoCs, USB adapters, DisplayPort-to-HDMI via `drm_dp_cec`. Desktop Intel/AMD/NVIDIA
  generally do **not**. The dev host has no `/dev/cec*` — no CEC path was tested end to
  end.

### 1.3 Mapping a RandR output name to an I2C bus / CEC device

EDID-first, even though ddcutil >= 2.0 guarantees the connector line, because both fast
paths are demonstrably unreliable:

```
RandR name (QScreen.name())
  -> normalised DRM connector       fallback only; names differ per driver
  -> /sys/class/drm/<conn>/edid     always readable
  -> mfg:model:serial               e.g. GSM:LG ULTRAFINE:406NTJJ6D028
  -> ddcutil bus                    matched against `detect` output
```

- `/sys/class/drm/<conn>/ddc` symlink: **absent on NVIDIA** (verified). Exists on
  i915/amdgpu/nouveau. Fast path only.
- Name mismatch is real: RandR `HDMI-1` vs DRM `card0-HDMI-A-1` on i915; RandR
  `HDMI-A-1` vs DRM `card1-HDMI-A-1` on the dev host. The normaliser handles
  `HDMI-A-n <-> HDMI-n`, `DP-n`, `DVI-D-n`, `eDP-n`, `Virtual-n`, stripping `cardN-`.
- **CEC device mapping:** enumerate `/dev/cec*`, read kernel connector info
  (`CEC_ADAP_G_CONNECTOR_INFO`, surfaced in `cec-ctl`'s driver-info block) for the DRM
  card and connector id; fall back to `cec-ctl -d N --phys-addr-from-edid
  /sys/class/drm/<conn>/edid`. **Unverified — no CEC hardware.** Must tolerate the ioctl
  being absent.
- **Zero displays** is a supported, possibly permanent state. Gated on
  `WebviewApp.display_available()` (`manager/webview/app.py:74-87`). With no screens:
  `outputs: []`, no probing, no subprocess. DDC/I2C does not need X, so a headless node
  with a monitor on I2C is still controllable; the X mechanism reports unavailable.

---

## 2. Capability model

One record per output, probed and cached, never assumed.

| Operation | DDC/CI (>= 2.0) | CEC | X |
|---|---|---|---|
| Power off | `0xD6` <- `04`/`05`, **only values the monitor advertises** | `--standby` (0x36) | `xrandr --output X --off` |
| Power on | `0xD6` <- `01` (often ignored once off) | `--image-view-on` (0x04) + `--active-source` (0x82) | `xrandr --output X --auto` |
| Power state | `getvcp 0xD6` | `--give-device-power-status` (0x8F) -> 0x90 | `xrandr --query`, `/sys/class/drm/<conn>/dpms` |
| Input source | `0x60`, values from `capabilities` | **not supported** | — |
| Volume | `0x62`, absolute 0..max | relative `ui-cmd=volume-up/down` | — |
| Mute | `0x8D` <- `01` mute / `02` unmute | `ui-cmd=mute` (0x43), toggle | — |

`auto` resolution order: **CEC -> DDC -> X** for *on* (only CEC reliably wakes a TV);
**DDC -> CEC -> X** for *off*.

**Probing:** `capabilities` (31 s) runs once per output in a background task at startup
and on hot-plug, cached in memory and persisted, refreshed only on explicit request.
Per-code `getvcp --terse` fallback (0.4 s each) when `capabilities` fails — which it did
on the dev host.

---

## 3. X blanking decision

- **Primary, per output: `xrandr --output <name> --off` / `--auto`.** Works with no DDC
  and no CEC. Only acceptable **after step 1** removes the assignment churn.
- **Secondary, documented: DPMS.** Idempotent triple before every force: `xset +dpms` ->
  `xset dpms 0 0 0` -> `xset dpms force off|on`. Zero timeouts restore `DPMSForceLevel`
  while leaving no automatic blanking timer, preserving exactly what `xinitrc`'s
  `xset -dpms` protects against. **`xinitrc` is not edited**, so deployed images gain
  this with no rebuild; re-applying it every time survives an X restart the manager never
  saw.
- **Why DPMS is secondary, established not assumed:** it is per **X screen**, not per
  output — no per-output DPMS property exists on any of the dev host's three outputs
  (`xrandr --props` checked). All-or-nothing on multi-output. Stays available for
  single-output nodes.
- Both run via `asyncio.to_thread` with the **inherited** environment, so the unit's
  `DISPLAY=:0` / `XAUTHORITY=/run/unitotem-x11.auth` reach the child.
- **Untested:** the dev host's X reports `Server does not have the DPMS Extension`.
  Needs the VM.

---

## 4. Step 1 (prerequisite) — re-key assignments on the RandR output name

**Problem.** `manager/webview/controller.py:117` — `self._assignments: dict[tuple[str,
int], str]`, keyed `(instance_id, window_id)`. `window_id` comes from `_next_id` in
`app.py` and is never reused, so an output that goes off and returns gets a new id and
the assignment is orphaned. That contradicts the spec's *"the playlist outputting to
that window must be unaware of the change"* — and makes `xrandr --off` unusable.

**Fix.** Key on `(instance_id, screen_name)`. `screen_name` is already in the
`WebviewInfo` payload (`app.py:481`: `window_screen = {wid: screen.name() for screen,
wid in self._screen_windows.items()}`) and is the RandR output name — stable across an
off/on cycle.

**Deliberately minimal.** Playlist loops keep addressing windows by `window_id`
(`PlaylistLoop.assign(instance_id, window_id)`, `send_webview_command(...,
window_id=...)`). Only the *assignment map* is re-keyed; `screen_name -> current
window_id` resolution happens in `_restore_assignments()` (`controller.py:161-191`),
which already runs on every info update and is already documented as idempotent — the
right hook, and its existing docstring already describes the behaviour we now want to
actually hold.

**Touch points, all in `manager/webview/controller.py`:** `_assignments` type (117),
`remove_playlist_loop` (146), `_restore_assignments` (161-191), `unregister_webview`
(207), `_save_assignments` (233), `load_assignments` (256-283),
`assign`/`unassign`/`get_assignment` (287-310), `get_webviews` (358),
`serialize_assignments` (376). The WS contract and `viewers.vue` are unchanged —
`api/viewers.py` and `api/display.py` keep taking `window_id` and translate at the edge.

**Migration of `/etc/unitotem/viewer_assignments.json`** — three formats, reusing the
existing `_legacy_assignments` machinery (121, 175-182):

1. `dict` (pre-per-window) — already handled, keep.
2. `list` of `{webview_id, window_id, playlist_id}` (current) — **new legacy case.** At
   load time no viewer is connected, so `window_id -> screen_name` is unknowable. Park
   it, resolve on the first `_restore_assignments()` where the reported windows carry
   both ids, then rewrite the file in the new format.
3. `list` of `{webview_id, screen_name, playlist_id}` (new) — load directly.

Best-effort by nature, the same contract the existing `dict` migration already has.
Records whose `screen_name` is `None` (possible — `window_screen.get(wid)` can return
`None`) must be dropped with a warning, never keyed on `None`.

**Acceptance test, before any hardware work:** in the VM, assign a playlist to a screen,
`xrandr --output X --off`, then `--auto`; the playlist must resume on the same output
with no manual reassignment, and the JSON must have been rewritten in the new format.

---

## 5. Files and API

### Hardware layer — `manager/utils/system/screens/` (a package, like `network/`)

`identity.py` (EDID parse, RandR/DRM normaliser, the join) · `ddc.py` (version gate, the
three parsers, `--bus`/`--terse`/`--noverify`) · `cec.py` (parses stdout, **never** the
exit code) · `xorg.py` · `__init__.py` (façade + mechanism resolution). Module-level
functions, house style (`audio.py`, `dbus_system.py`). **Stdlib and `subprocess` only —
no Qt, no PySide6, no cefpython.**

### Persistence — `manager/utils/models/screen_control.py`

`/etc/unitotem/screen_control.json`. Pydantic `BaseModel(validate_assignment=True)`,
`load()`/`save()`, atomic `tmp.replace(path)` — cloned from
`manager/utils/models/viewer.py`. Path declared in
`manager/utils/models/command_line.py`. Holds per-output preferred mechanism, the
mandatory cached capability probe, and the timer parameter table. Added to the
backup/restore part list.

### API — nested, giving `Settings/Display/Screens/*`

Confirmed against `__treegen` (`manager/api/ws/endpoints.py:60-66`): it walks
`dir(cls)`, and for a callable that `isclass(...)` recurses with the already-joined
prefix. At that point `prefix` is `Settings/Display`, so a nested `class
Screens(WSAPIBase)` yields `Settings/Display/Screens/<method>`.

`api/display.py` would roughly triple in size, so split it without changing the path:
define the class in a new `manager/api/display_screens.py` and bind it in `display.py`:

```python
from api.display_screens import Screens          # no PySide6/cefpython in this chain

class Display(WSAPIBase):
    Screens = Screens
```

`dir()` still finds it, `isclass` still holds, the path is unchanged. The constraint at
`api/display.py:11-14` (must stay importable without PySide6/cefpython) is preserved.

| Target | Payload | Permission |
|---|---|---|
| `Settings/Display/Screens/getScreens` | -> `{outputs: [...]}` | `power` **or** `audio` |
| `.../probe` | `{name?}` -> broadcasts `getScreens` | admin |
| `.../setPower` | `{name, state: on\|off, mechanism: auto\|ddc\|cec\|x}` | `power` |
| `.../getPower` | `{name}` | `power` |
| `.../setInput` | `{name, source: int}` | admin |
| `.../setVolume` | `{name, volume: int}` | `audio` |
| `.../setMute` | `{name, mute: bool}` | `audio` |
| `.../setPreferredMechanism` | `{name, mechanism}` | admin |

**Permission rationale:** `power` for monitor power (the same class of act as
`Power/poweroff`); `audio` for volume and mute (the spec assigns "screen volume" to
`audio`); input source and mechanism configuration stay admin (no decorator implies
admin). Verified that stacking two `@UserPerms.requires.*` decorators **ORs** them —
`RequiresMeta.set_perm` does `func.perms.add(...)`
(`manager/utils/models/user.py:32-43`) — so `power`-or-`audio` is expressible.

**Staying off the loop:** every entry point is `async def`, wrapping `asyncio.to_thread`
around `subprocess.run(timeout=...)` (10 s getvcp/setvcp, 60 s capabilities, 15 s
cec-ctl). **One `asyncio.Lock` per I2C bus and per CEC device** — concurrent
transactions on one bus is exactly how "Maximum retries exceeded" was reproduced. The
31 s probe is a background task that broadcasts `getScreens` on completion. A non-local
`viewer_id` returns an explicit "not supported yet": a remote viewer is a client node
running its own manager, so the correct route is the Remote channel, not
`send_webview_command`, and a 31 s ddcutil call on the Qt main thread would be a bug.

---

## 6. Tests — `manager/tests/`, pytest (new infrastructure)

```
manager/tests/
  conftest.py
  fixtures/
    ddcutil_detect_brief.txt
    ddcutil_capabilities_lg.txt
    ddcutil_getvcp_terse.txt
    edid_lg_hdmi.bin
  test_ddc_parsers.py
  test_identity.py
  test_mechanism_resolution.py
  test_cron_actions.py
manager/requirements-dev.txt      # pytest - dev only, never shipped
pyproject.toml                    # [tool.pytest.ini_options]
```

- **Not shipped, no packaging change needed.** `debian/rules:48-50` copies only
  `manager/{api,routers,templates,utils,webview,resources,static}` plus `init.py`,
  `main.py`, `requirements.txt`, so `manager/tests/` and `requirements-dev.txt` are
  excluded automatically and pytest never enters the shipped venv.
- **`pythonpath = ["manager"]` is required** — the codebase uses absolute imports rooted
  at `manager/` (`from api.ws.endpoints import ...`). There is no existing
  `pyproject.toml`, `setup.py` or `pytest.ini` at the repo root; this is a new file.
- **Pure logic only.** Nothing that imports PySide6 or shells out.

### 6.1 Fixtures — verbatim captures (these exist nowhere else)

`ddcutil_detect_brief.txt` — `ddcutil detect --brief`, ddcutil 2.2.5:

```
Invalid display
   I2C bus:          /dev/i2c-7
   DRM connector:    card1-DVI-D-1
   drm_connector_id: 0
   Monitor:          PHL:PHL 246V5:AU01626003817

Display 1
   I2C bus:          /dev/i2c-8
   DRM connector:    card1-HDMI-A-1
   drm_connector_id: 0
   Monitor:          GSM:LG ULTRAFINE:406NTJJ6D028

Display 2
   I2C bus:          /dev/i2c-9
   DRM connector:    card1-DP-1
   drm_connector_id: 0
   Monitor:          GSM:LG ULTRAFINE:401NTMX52747
```

Plain `ddcutil detect` spells the same field `DRM_connector:` and adds an
`EDID synopsis:` block with `Mfg id:` / `Model:` / `Serial number:`. The parser must
accept both spellings.

`ddcutil_getvcp_terse.txt` — one `ddcutil --bus N getvcp --terse <code>` result per
line, with the bus and expected meaning:

```
VCP 62 C 30 100              bus 8  continuous: cur 30, max 100
VCP 62 C 81 100              bus 7  continuous: cur 81, max 100
VCP 62 C 100 100             bus 9  continuous: cur 100, max 100
VCP 10 C 19 100              bus 7  continuous: cur 19, max 100
VCP 60 SNC x00               bus 8  simple non-continuous
VCP 60 SNC x11               bus 7  simple non-continuous: HDMI-1
VCP D6 SNC x01               bus 8  power mode: on           (5/5 runs identical)
VCP D6 SNC x01               bus 9  power mode: on
VCP 8D CNC x00 x02 x00 x02   bus 7  complex: max 0x0002, cur 0x0002 (unmute)
VCP 0B CNC x5d xdc x00 x00   bus 8  complex: max 0x5DDC, cur 0x0000
VCP AA ERR                   bus 8  unsupported feature, exit 1
```

Grammar: `C` -> `VCP <cc> C <cur> <max>`; `SNC` -> `VCP <cc> SNC x<nn>`; `CNC` ->
`VCP <cc> CNC <max-hi> <max-lo> <cur-hi> <cur-lo>` (field order pinned by the `0x0B`
row, where the two pairs differ). Also capture:
`DDC communication failed for monitor on bus /dev/i2c-7` (bus 7, code `D6`) and
`Bus /dev/i2c-30 does not exist.` (exit 1).

`ddcutil_capabilities_lg.txt` — excerpt of `ddcutil -d 1 capabilities` (LG Ultrafine,
MCCS 2.1), the codes that matter:

```
VCP Features:
   Feature: 60 (Input Source)
      Values:
         11: HDMI-1
         12: HDMI-2
         0f: DisplayPort-1
         00: Unrecognized value
   Feature: D6 (Power mode)
      Values:
         01: DPM: On,  DPMS: Off
         04: DPM: Off, DPMS: Off
   Feature: 62 (Audio speaker volume)
   Feature: 8D (Audio Mute)
```

`edid_lg_hdmi.bin` — first 32 bytes of `/sys/class/drm/card1-HDMI-A-1/edid`, which must
parse to `GSM:LG ULTRAFINE:406NTJJ6D028`:

```
00000000: 00ff ffff ffff ff00 1e6d c15b c44f 0300
00000010: 0622 0103 803c 2278 ea40 b5ae 5142 ad26
```

Capture the full 128/256-byte EDID at implementation time; the serial lives in a
descriptor block beyond byte 32.

### 6.2 Coverage

1. **`detect --brief` parser** — buses 7/8/9, the three DRM connectors, the three
   `mfg:model:serial` triples, and that **`Invalid display` is parsed as a display with
   `valid=False`, not skipped** (the bug that would otherwise re-hide the Philips). Both
   `DRM connector:` and `DRM_connector:` spellings.
2. **`getvcp --terse` parser** — every row above; `VCP AA ERR` -> unsupported; the
   `0x0B` row pinning `CNC` field order; `DDC communication failed...` and
   `Bus ... does not exist.` -> typed errors.
3. **`capabilities` parser** — `0x60` -> `{11: HDMI-1, 12: HDMI-2, 0f: DisplayPort-1}`;
   `0xD6` -> `{01, 04}` **only**, asserting we never offer the MCCS `02`/`03` that this
   monitor did not advertise; `0x62`/`0x8D` present with empty value maps.
4. **RandR/DRM normaliser** — `HDMI-1 <-> card0-HDMI-A-1`,
   `HDMI-A-1 <-> card1-HDMI-A-1`, `DP-1`, `DVI-D-1`, `eDP-1`, `Virtual-2`; a genuine
   mismatch returns *no* match rather than a wrong one.
5. **EDID -> `mfg:model:serial`** — the real LG bytes -> `GSM:LG
   ULTRAFINE:406NTJJ6D028`, matching what ddcutil prints; truncated/garbage EDID ->
   `None`.
6. **Mechanism resolution** — `auto` picks CEC->DDC->X for on, DDC->CEC->X for off; an
   X-only output resolves to X for both; nothing available raises a typed "no mechanism"
   error; ddcutil `1.4.1` marks DDC unavailable with a reason string.
7. **Cron action encode/decode round-trip** — every registry action survives `new()` ->
   `serialize()`; **`serialize()` returns `'*'` and `'*/15'` unchanged instead of
   raising** (the regression test for §7 bug 1); a crontab line whose command is not in
   the registry is reported read-only, never rewritten; a screen-timer uuid resolves
   against the JSON table, and a missing entry degrades cleanly.

---

## 7. Timers

**Two confirmed bugs, both fixed here.**

1. **`UniCron.serialize()` raises on `*`.** `manager/utils/system/crontab.py:39-49` does
   `int(str(job.minute))` and friends. Reproduced against the real installed
   `python-crontab`: for `0 22 * * * /usr/sbin/poweroff`, `int(str(job.dom))` raises
   `ValueError: invalid literal for int() with base 10: '*'`. This is worse than
   "wildcards break" — the *first realistic timer anyone creates* breaks `getJobs` for
   the entire list, and "every day at 22:00" is precisely what this feature needs. Fix:
   return the raw slice strings; `*/15` then survives too. The UI already renders `'*'`.
2. **`deleteItem()` never calls the API.** `webui/src/pages/settings/timers.vue:212-215`
   only splices the local array, so deletions come back on refresh. Fix: send
   `Settings/Cron/deleteJob` with `{uuid}`. Also remove the fake seeded row (173-183)
   and wire the unused `setJobEnabled` backend method to a per-row `v-switch`.

**Named actions, uuid indirection.** Replace the hardcoded
`'/usr/sbin/' + ('poweroff' if cmd == 'pwr' else 'reboot')` (`crontab.py:13`, duplicated
at `manager/api/system/cron.py:36-39`) with one `ACTIONS` registry. Screen actions need
parameters, and cron runs each line through `/bin/sh`, so the crontab line carries
**only the job's own hex uuid**:

```
0 22 * * * /var/unitotem-venv/bin/python3 /usr/share/unitotem-manager/screenctl.py <hex-uuid>  # unitotem:-)<hex-uuid>
```

Output name, mechanism and on/off live in `/etc/unitotem/screen_control.json`, validated
by pydantic (`Literal` for mechanism and state, `[A-Za-z0-9_-]+` for the output name)
**before** being written. No user-controlled byte reaches root's crontab. `serialize()`
joins cron and JSON so the UI round-trips; unknown commands are reported read-only.

`screenctl.py` sits beside `init.py` and is therefore already installed by
`debian/rules` — no packaging change. It **sets `DISPLAY`/`XAUTHORITY` itself**: cron
provides a nearly empty environment, so relying on the unit's `Environment=` would
silently break the X mechanism. It works whether or not the manager is running.

---

## 8. Web UI

`webui/src/pages/settings/display.vue` gains the **per-screen selector it currently
lacks** (it hardcodes `window_id: 0`) plus a "Monitor control" card per output: power
on/off with a chip showing which mechanisms are available; an input `v-select` populated
**from the monitor's own advertised `0x60` values**; volume and mute styled after
`audio.vue`. Unavailable mechanisms render disabled with the reason in a tooltip
("ddcutil 1.4.1 found, 2.0+ required", "no CEC adapter", "no DDC/CI on this output").
Reacts to `Settings/Display/Screens/getScreens` in the existing `onWSMessage` switch.
`timers.vue` gets the three fixes plus screen actions with conditional output and
mechanism selectors.

Deploy reminder: `manager/static/manifest.json` must ship alongside `assets/`, or the
old hashed bundle is served silently.

---

## 9. Packaging and OS-level changes

- `debian/control:12` -> **`Recommends: ddcutil, v4l-utils, i2c-tools`**. Never
  `Depends`, and never versioned: a versioned `Depends: ddcutil (>= 2.0)` would make
  `unitotem-manager` uninstallable on stock bookworm, which is unacceptable for a
  package that must also serve headless nodes. The runtime version gate handles it
  instead.
- Sibling repo `unitotem-system`: `/etc/modules-load.d/unitotem-i2c.conf` containing
  `i2c-dev`, following the `rpi-gpu.conf` pattern at `kiosk/stage2-configure.sh:97`.
  Install `ddcutil` and `v4l-utils` in the image package set — from the trixie archive,
  no backport stage.
- **New section 29 in `unitotem-system/debian-kiosk.md`** (the file currently ends at
  28): the module autoload, the ddcutil >= 2.0 requirement and that trixie supplies it,
  the `xrandr --off` vs DPMS decision, and why `xinitrc` was deliberately left alone. In
  **English**, per `CLAUDE.md`, even though parts of that file are Italian. Coordinate
  with the trixie migration task.

---

## 10. Verification

**Done, read-only, on the dev host** — the three parsers against real 2.2.5 output;
per-code capability asymmetry; `--bus` 5/5 vs `-d` failing; timings 31 s / 5.6 s /
0.40 s; `VCP AA ERR` + exit 1; `cec-ctl` returning 0 while failing to open the device;
no `--json` in 2.2.5; no `ddc` sysfs symlink on NVIDIA; no per-output DPMS property; the
`int(str(job.dom))` crash and the missing `CronTab._cron_re`, both against the installed
library.

**Unit tests, no hardware** — §6.

**VM** — the §4 acceptance test; `--auto` restoring the mode; the zero-screens path;
cron firing `screenctl.py` under cron's minimal environment; graceful degradation with
no I2C and no EDID.

On **virtio-vga** there is no EDID and no I2C, so DDC/CI cannot be exercised at all —
that configuration can only prove that absence is handled. But the test VM can now also
run with a **real GTX 1060 passed through** (`tools/unitotem-test-gpu.xml`, procedure in
`VM-TESTING.md`), and the card drives a **physical 4K monitor on DP-1**. That gives the
guest a real EDID on a real I2C bus, so **the DDC/CI path is testable in the VM after
all** — detection, the EDID join, `getvcp`, and input/volume/mute against actual
hardware. Switching between the two domains is one `undefine --keep-nvram` plus
`define`, so the sensible plan is: functional and degradation work on virtio, DDC/CI
verification on the GPU configuration.

Two constraints that come with the GPU configuration: the proprietary NVIDIA driver
**removes the DRM connector debugfs**, so the `force` hot-plug trick is unavailable
(CRTC off/on is the working proxy), and it **refuses arbitrary modelines** (`BadMatch`).
Neither blocks the §4 acceptance test, which uses `xrandr --output X --off`/`--auto` on
a connected output.

**Real hardware, only the user can do it** — any DDC/CI power, input or volume change on
a real monitor; whether `0xD6 <- 01` wakes it; the entire CEC path (a Raspberry Pi
`vc4` HDMI or a Pulse-Eight USB adapter); DPMS force (the dev host's X reports
`Server does not have the DPMS Extension`). These ship marked unverified.

---

## 11. Sequencing — one commit per step

1. **[blocked: `manager/webview/` must be free]** Re-key assignments on `screen_name`
   and migrate `viewer_assignments.json` (§4).
2. Timers: bug fixes, action registry, `screenctl.py` (§7). Independent of 1 — can go
   first.
3. `manager/tests/` + pytest infrastructure + all coverage (§6).
4. Hardware layer `utils/system/screens/` + persistence model (§5). DDC is not
   exercisable on-image until trixie.
5. API `Settings/Display/Screens/*` (§5).
6. Web UI (§8).
7. Packaging + `unitotem-system` section 29 + doc updates (§9).

**Doc updates in step 7:** in `TODO`, comment out the `Add display power control` entry
down to CEC and DDC/CI only with `<!-- -->` (GPIO and Serial stay live), and add a line
for the `manager/routers/backup.py:123` `CRONTAB.remove_all(comment=CRONTAB._cron_re)`
`AttributeError` — confirmed: `_cron_re` does not exist on `CronTab` in the installed
python-crontab, so factory reset raises. Recorded, not fixed here. The `CLAUDE.md`
gotchas gain: `cec-ctl` exits 0 on failure; `ddcutil detect`'s verdict is not a
capability signal; address ddcutil by `--bus`, one code per call; `capabilities` costs
~30 s cold and ddcutil's own cache is unusable on a read-only root.
