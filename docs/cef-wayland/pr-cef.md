# Draft upstream pull request: chromiumembedded/cef

Not opened. Series: `tools/cef-server/patches/cef/0001..0006` (`git am`). Checked: applies with plain `git am` on a fresh clone of master
(7f8a5ec28, Chromium 156.0.8078.0) and on `d58e84d17` (147.0.7727.118); `tools/fix_style.py` reports "ok" for every touched file.
CEF's rules (docs/contributing_with_git.md) ask for an issue number in the commit descriptions: open the issue first (text below), then add
"Fixes #N" to the commits. No CLA is mentioned in the repository docs.

**Not tested on Windows or macOS.** No Windows SDK/MSVC or Apple SDK is available to the author (`gn gen` with `target_os="win"` fails with
`KeyError: 'WINDOWSSDKDIR'`, with `target_os="mac"` in `mac_sdk.gni`; the official Windows cross-build needs a toolchain package made on a
Windows machine). Linux only: x86_64 and aarch64 builds, ceftests Views suites on x86_64 under labwc. As a partial check the touched
translation units (`display_impl.cc`, `window_impl.cc`, `window_view.cc`, `view_util_aura.cc`) were compiled syntax-only with Chromium's
clang both with and without `USE_OZONE`; without it `BUILDFLAG(IS_OZONE)` is 0, which is what Windows and macOS see (`tools/cef-server/check-nonozone.py`).
It does not replace a Windows or macOS build. aarch64 was built and the wheel hashes checked; the arm64 ceftests were not run yet.

## Title

views: Wayland support for CefWindow (frameless windows, fullscreen on a chosen display, display label, display observer)

## Issue text

CEF Views windows are the supported way to run CEF on Ozone/Wayland (#2804). A kiosk-style application (frameless, fullscreen, one window per
output, closed for real) needs: (a) frameless windows without server-side decorations (#4273), (b) choosing the display of a fullscreen
CefWindow, (c) finding out which compositor output a CefDisplay is, (d) a correct CefWindow::GetDisplay(), (e) a notification when a display
appears, instead of polling.

## Commits

1. **Frameless windows and X11 assumptions.** `CefWindowView::CreateWidget` sets `remove_standard_frame` only on Windows; on Wayland the
   compositor then draws a title bar (xdg-decoration `set_mode(2)`, as in #4273/#4274). Set it for Wayland. The X11-only handling in
   `CreateWidget`/`WindowClosing` is skipped on Wayland. `IsRunningOnWayland()` moves to `view_util` (requested in the #4274 review).
   Overlaps PR #4274 (open); if it merges first this commit shrinks.
2. **`CefWindow::SetFullscreenOnDisplay(int64_t display_id)`** (experimental). `views::Widget::SetFullscreen(bool, int64_t)` takes a target
   display; Wayland turns it into `xdg_toplevel.set_fullscreen(wl_output)`. Works before `Show()`. For a window that is already fullscreen it
   leaves fullscreen first on Ozone, because labwc ignores the output of a fullscreen request for a toplevel that is already fullscreen.
   X11 does not support a target display (`DCHECK_EQ(target_display_id, kInvalidDisplayId)` in `X11Window::SetFullscreen`): there it
   behaves like `SetFullscreen(true)`. Windows and macOS pass the id to their widgets (not tested).
3. **`CefDisplay::GetLabel()`** (experimental): `display::Display::label()`; on Wayland the `wl_output` description, the only place an
   application can learn the output name (wlroots compositors end it with `(DP-1)`).
4. **`CefWindow::GetDisplay()` on Wayland** used the window bounds, which a Wayland client cannot know (they start at 0,0): a window on the
   second output was reported on the first. Use `display::Screen::GetDisplayNearestWindow()` there.
5. ceftests for 2 and 3.
6. **`CefDisplayObserver` + `CefDisplay::AddObserver/RemoveObserver`** (experimental; independent of 1-4, can be taken or left separately).
   `OnDisplayAdded/Removed/MetricsChanged`, a wrapper of `display::DisplayObserver` (`ScopedOptionalDisplayObserver`, so a late removal after
   shutdown is harmless). Needed because Chromium lists a new output about 220 ms after it was switched on; measured with
   `wlr-randr --output DP-2 --off/--on` on labwc, 6 runs: removed 184-219 ms, added 211-228 ms after the command, `FindByOutputName`
   finds the display inside the callback every time, the window opened on the new output 7-8 ms later.

All new API is `added=experimental` (`CEF_EXPERIMENTAL`); say if `CEF_NEXT` is preferred.

## Tested

labwc 0.8.3 on an x86_64 PC (Intel) with two outputs and on a Raspberry Pi 4 (aarch64, two forced connectors, no monitor), pure Wayland,
CEF built from source (official build, proprietary codecs), driven through cefpython bindings: one frameless window per output goes
fullscreen on its output (`WAYLAND_DEBUG`: `set_fullscreen(wl_output#32)` / `#34`, `configure` with each output's size), `GetDisplay()` is right,
output off/on gives a new display id and the window returns with `SetFullscreenOnDisplay`, `Close()` destroys the toplevel and `OnBeforeClose`
fires. 18 checks pass on both boards, 19 on x86_64 with the observer. ceftests (x86_64, `--ozone-platform=wayland`): `Views*` 105 tests
pass, including `WindowFullscreenOnDisplay` (`WAYLAND_DEBUG` shows `set_fullscreen(wl_output)` then `unset_fullscreen`) and
`DisplayObserverAddRemove`. The test of the observer only checks registration: display changes cannot be provoked from ceftests.

Not tested: Windows, macOS, X11 for the new methods (the X11 path only calls `SetFullscreen(true)`), a real monitor on the Pi (aarch64
results are from forced connectors), the arm64 ceftests, observer behaviour on compositors other than labwc.

Related: #2804, #4273, #4274.
