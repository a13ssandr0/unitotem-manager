# CEF on Wayland: Views windows, per-output fullscreen

Status 2026-10-21. Branch `viewer-wayland`. Code: CEF patch series `tools/cef-server/patches/cef/` (against CEF
`d58e84d17`, Chromium 147.0.7727.118), cefpython branch `wayland-views` of `a13ssandr0/cefpython`, tests `tools/cef-wayland-tests/`.

## Why

The kiosk runs labwc (Wayland only). cefpython opened browsers with `WindowInfo()`, which CEF implements natively for X11 only
(`CefBrowserPlatformDelegateNativeLinux::CreateHostWindow` is `#if BUILDFLAG(SUPPORTS_OZONE_X11)`). On the Wayland Ozone backend the
result is a Chrome-style window (a real Chromium `Browser`) with an empty `app_id` and title that `CloseBrowser()` never destroys
(measured: `wlrctl toplevel list` shows `:` before and after `CloseBrowser(True)`, `OnBeforeClose` never fires, the toplevel only goes
at `Shutdown()`). The viewer therefore opened windows through DevTools (`Target.createTarget newWindow`) and placed them on their
output with a labwc key binding pressed through a uinput keyboard.

CEF's supported way to get a frameless, UI-less, closable top-level window that works on every Ozone platform is the Views framework
(`CefWindow::CreateTopLevelWindow`, `CefBrowserView::CreateBrowserView`). cefpython had no bindings for it.

## What was built

1. **cefpython Views bindings** (`src/views.pyx`, `src/display.pyx`, `src/client_handler/views_delegate.*`, `src/extern/cef/cef_views.pxd`):
   `cef.Window`, `cef.Panel`, `cef.View`, `cef.BrowserView`, `cef.Display`, Python window/browser-view delegates (object with optional
   methods named like CEF's: `OnWindowCreated`, `OnWindowDestroyed`, `CanClose`, `IsFrameless`, `GetInitialBounds`,
   `GetInitialShowState`, `GetLinuxWindowProperties` -> `wayland_app_id`, ...). Documented in `vendor/cefpython/api/{Window,BrowserView,View,
   WindowDelegate,Display}.md`.
2. **CEF patches** (`tools/cef-server/patches/cef/`, one concern per patch):
   - 0001 frameless windows and X11 assumptions on Ozone/Wayland: `remove_standard_frame` on Wayland (upstream issue
     chromiumembedded/cef#4273, PR #4274 is open for the same thing), and the X11-only window handling in `CefWindowView::CreateWidget`/
     `WindowClosing` (`ui::SetUseOSWindowFrame`, transient-for/modal properties) is skipped on Wayland; `IsRunningOnWayland()` moves to
     `view_util` (what the maintainer asked for in #4274).
   - 0002 `CefWindow::SetFullscreenOnDisplay(int64 display_id)`.
   - 0003 `CefDisplay::GetLabel()`.
   - 0004 `CefWindow::GetDisplay()` on Wayland uses the entered output instead of matching the window bounds.
   - 0005 ceftests.
3. **cefpython**: `ozone-platform` is chosen automatically (wayland when `WAYLAND_DISPLAY` is set and `DISPLAY` is not, X11 otherwise,
   overridable), `GDK_BACKEND=x11` is no longer forced in a pure Wayland session, the `SetAsChild` warning is accurate,
   `CreateBrowserSync()` with a parentless `WindowInfo` on Wayland opens a Views window and closes it with the browser.

## Per-output fullscreen (B): findings

* `views::Widget::SetFullscreen(bool, int64_t target_display_id)` exists; `WaylandToplevelWindow::SetFullscreen` passes
  `GetWaylandOutputForDisplayId(id)` to `xdg_toplevel.set_fullscreen(wl_output)`. Windows and macOS widgets take the id too. **X11
  does not** (`X11Window::SetFullscreen` has `DCHECK_EQ(target_display_id, kInvalidDisplayId)`, crbug.com/40111909), so the CEF API
  falls back to `SetFullscreen(true)` there.
* CEF's `CefWindow::SetFullscreen(bool)` had no way to pass it, and `CefWindowImpl::SetFullscreen` returns early when the window is
  already fullscreen, hence a new method (experimental API, `CEF_EXPERIMENTAL`; `CEF_NEXT` if the maintainers want it stable).
* It works before `Show()`: calling it in `OnWindowCreated` puts `set_fullscreen(wl_output)` on the wire for the very first configure
  (`WAYLAND_DEBUG`: `xdg_toplevel#47.set_fullscreen(wl_output#32)` then `configure(1024, 768, ...)`).
* **labwc ignores the output of `set_fullscreen` for a toplevel that is already fullscreen** (measured: after an unplug/replug the
  request went out and nothing came back). The CEF implementation leaves fullscreen first when the window must move.
* Display ids: Chromium's display id on Wayland is the registry name of the `wl_output` global (49, 50, ...). It **changes when the
  output is unplugged and plugged back** (51, 52, ...), so applications look the display up again by name after a hot-plug.
* The output name (`DP-1`) is not in `display::Display`; Chromium has it (`zxdg_output_v1.name`, `WaylandOutput::Metrics::name`) but only
  the `wl_output` description reaches `Display::label()`. wlroots compositors end the description with `(NAME)` (`"Invalid Vendor Codename
  - RTK FHD demoset-1 (DP-2)"`, `"(null) (null) (HDMI-A-1)"`, and `"... (DP-1 via Unknown)"` for some connectors), so `CefDisplay::GetLabel()`
  + `cef.Display.FindByOutputName(name)` resolve a connector name; other compositors can match on bounds.
* A Wayland client does not know where its toplevel is in the global space: `Window.GetClientAreaBoundsInScreen()` starts at 0,0, and
  `CefWindowView::GetDisplay()` (match by bounds) reported the first output for a window on the second: fixed in 0004.

## D. The Alloy "native" Wayland gap: recommendation

Do **not** implement a Wayland version of the native windowed path (`CefWindowX11` equivalent). The Views framework is the supported
path (maintainer, issue #2804: "the Views framework is required"), it already provides what a Wayland application needs, and upstream
is working on embedding in third party Wayland applications (subsurfaces) in #2804 as a separate feature. Changing
`CloseHostWindow` or `ChromeBrowserHostImpl` so that the Chrome-style window created for a parentless `WindowInfo` is destroyed by
`CloseBrowser()` is not a small change: the window belongs to a real Chromium `Browser`/`BrowserView` whose lifetime Chrome style owns
(`OnBeforeClose` only fires when the whole `BrowserList` is torn down at shutdown). Instead cefpython (not CEF) maps a parentless
`WindowInfo` on Wayland to a Views window whose delegate closes the window when the browser is destroyed: measured
(`tools/cef-wayland-tests/old_windowinfo.py`) `before close: [':']`, `after CloseBrowser: []`, `OnBeforeClose` fired.
Embedding into a foreign window (`SetAsChild(handle)`) stays X11/XWayland only.

## Test evidence

All on the real boards with the real compositor (labwc 0.8.3), pure Wayland (no X server), CEF 147 built from source with the patches.

| test | venus (Intel Iris Xe, x86_64, DP-1 640x480 forced + DP-2 1920x1080) | Pi 4 (V3D, arm64, HDMI-A-1/2 forced) |
|---|---|---|
| `outputs.py --hotplug`: one frameless window per output, fullscreen on its output, sizes, app_id, off/on, move back, close | 18/18 PASS | 18/18 PASS (phantom connectors: no monitor attached) |
| `views_test.py` (cefpython unit test) | OK | not run (Pi reserved by another session) |
| doc example `api/Window.md` verbatim | works | - |
| `old_windowinfo.py`: parentless WindowInfo, CloseBrowser | window closes, `OnBeforeClose` | - |

`WAYLAND_DEBUG=1` on the Pi: two toplevels, `set_fullscreen(wl_output#32)` / `(wl_output#34)`, `configure(1024, 768)` / `(800, 600)`,
`wl_surface.enter` of the right outputs.

### Frame rate, CPU, memory (`tools/cef-wayland-tests/perf.py`, 15-20 s, whole process tree, PSS)

Views window against a Chrome-style window opened through DevTools (the viewer's current workaround), fullscreen, same compositor.

| board | page | mode | fps | CPU (% of a core) | PSS MB |
|---|---|---|---|---|---|
| venus 1920x1080 | WebGL2 | Views | 60.0 | 36.7 | 411 |
| | | DevTools | 60.0 | 36.3 | 421 |
| | canvas 2D | Views | 60.0 | 38.9 | 412 |
| | | DevTools | 60.0 | 39.0 | 433 |
| | idle page | Views | 60.0 | 13.5 | 399 |
| | | DevTools | 60.0 | 14.1 | 417 |
| Pi 4 (HDMI-A-1 forced 1920x1080 for WebGL2, see note) | WebGL2 | Views | 60.0 | 76.0 | 375 |
| | | DevTools | 60.0 | 77.0 | 391 |
| Pi 4 | canvas 2D | Views | 60.0 (1024x768) | 77.7 | 373 |
| | | DevTools | 60.3 (800x600) | 68.0 | 381 |
| Pi 4 | idle | Views | 60.0 | 11.5 | 360 |
| | | DevTools | 60.3 | 11.3 | 378 |

Views windows use 2-5% less memory and the same CPU and frame rate. Note for the Pi rows: there was no monitor, the outputs are
forced connectors (`echo on > /sys/kernel/debug/dri/1/HDMI-A-1/force`); the 1920x1080 mode of the WebGL2 row is a custom mode set with
`wlr-randr --custom-mode`, the canvas/idle rows ran at the sizes in the table (the DevTools window is not the output size, see the
measurement traps in CLAUDE.md), so the Pi CPU numbers of the two modes are only comparable in the WebGL2 and idle rows. The Pi numbers
say the Views path costs nothing extra; they are not a statement about scanout to a real monitor.

## Related upstream work (searched 2026-10-20/21)

* chromiumembedded/cef#2804 "Linux: Add support for embedded Ozone/Wayland windows" (open; embedding via subsurfaces, Views is the supported path).
* chromiumembedded/cef#4273 / PR #4274 "Frameless Views windows request server-side decorations on Wayland" (open): the same `remove_standard_frame`
  change as our 0001.
* chromiumembedded/cef#4061 (old Chrome decoration on Wayland), Collabora "CEF on Wayland upstreamed" (2019: Ozone is usable only in Views mode).
* crbug.com/40111909 (X11 target display), wayland-protocols xdg-shell `xdg_toplevel.set_fullscreen`.
* No upstream issue exists for choosing the display of a fullscreen CefWindow.

## Not verified

* Windows and macOS behaviour of `SetFullscreenOnDisplay` (the Chromium widgets take the id; no such machine here). The patch is guarded
  only for X11.
* ceftests: the new test compiles and links (`autoninja ceftests`); it was not run (the ceftests harness needs its own display setup).
* A real monitor on the Pi (see above); a second real monitor on venus (a forced DP-1 phantom of 640x480 is the second output).
* cefpython on macOS/Windows: the code is platform-guarded (`IF UNAME_SYSNAME`) but only built and run on Linux x86_64/aarch64.
