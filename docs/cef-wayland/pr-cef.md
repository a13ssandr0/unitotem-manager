# Draft upstream pull request: chromiumembedded/cef

Not opened. Series: `tools/cef-server/patches/cef/0001..0005` (`git am` against CEF `d58e84d17`, Chromium 147.0.7727.118). They are written
against the 7727 branch; for a PR to `master` they must be rebased (window_view.cc/window_impl.cc may have moved: master
already has a `IsRunningOnWayland()` in window_impl.cc as the 7727 branch has, and PR #4274 touches the same lines of
`CefWindowView::CreateWidget` as patch 1).

## Title

views: Wayland support for CefWindow: frameless windows, fullscreen on a chosen display, display label

## Description

CEF Views windows work on Ozone/Wayland and are the supported way to run CEF there (#2804). Building a kiosk-style application on
it (frameless, fullscreen, one window per output, closed for real) showed four gaps, fixed one per commit:

1. **Frameless windows and X11 assumptions** (same root cause as #4273 / #4274). `CefWindowView::CreateWidget` only sets
   `remove_standard_frame` on Windows, so a frameless window on Wayland gets server-side decorations from the compositor
   (xdg-decoration). Set it on Wayland. The X11 specific window handling in `CreateWidget`/`WindowClosing`
   (`ui::SetUseOSWindowFrame`, `_NET_WM_STATE_MODAL`, transient-for) treated the accelerated widget as an X11 window on every Ozone
   platform; skip it on Wayland. `IsRunningOnWayland()` moves to `view_util` (as requested in #4274 review).
2. **`CefWindow::SetFullscreenOnDisplay(int64_t display_id)`** (experimental API). `views::Widget::SetFullscreen(bool, int64_t)` takes a
   target display and Ozone/Wayland turns it into `xdg_toplevel.set_fullscreen(wl_output)` (Windows and macOS widgets take it too), but
   `CefWindow::SetFullscreen(bool)` cannot supply it. The method also moves a window that is already fullscreen (it leaves fullscreen
   first: labwc ignores the output of a fullscreen request for a toplevel that is already fullscreen). X11 does not support a target
   display (`DCHECK_EQ(target_display_id, kInvalidDisplayId)` in `X11Window::SetFullscreen`, crbug.com/40111909), where it behaves like
   `SetFullscreen(true)`.
3. **`CefDisplay::GetLabel()`** (experimental API): `display::Display::label()`. On Wayland it is the `wl_output` description, the only way
   for an application to learn which compositor output a `CefDisplay` is (wlroots compositors end it with the connector name).
4. **`CefWindow::GetDisplay()` on Wayland** matched the window bounds against the displays, but a Wayland client does not know the
   position of its toplevel (bounds start at 0,0): a window on the second output was reported on the first. Use
   `display::Screen::GetDisplayNearestWindow()` (entered outputs) on Wayland.
5. ceftests: `ViewsWindowTest.WindowFullscreenOnDisplay` and a `GetLabel()` call.

API versioning: marked `CEF_EXPERIMENTAL` (`added=experimental`); say if you prefer `CEF_NEXT`.

## Testing

labwc 0.8.3 (wlroots) on a Raspberry Pi 4 (V3D, aarch64) and an Intel Iris Xe PC (x86_64), pure Wayland, CEF built from source
(official build, `proprietary_codecs`), driven through the cefpython bindings of these APIs (`tools/cef-wayland-tests/outputs.py` of
the UniTotem repository): one frameless window per output goes fullscreen on its output (`WAYLAND_DEBUG`: `set_fullscreen(wl_output#32)`
and `#34`, `configure` with the output sizes), `GetDisplay()` is right, an output unplugged and plugged back (new display id) takes the
window back with `SetFullscreenOnDisplay`, `Close()` destroys the toplevel and `OnBeforeClose` fires. 18/18 checks on both boards. ceftests
builds; the new test was not run (no ceftests setup for a Wayland session here). Not tested: Windows, macOS, X11 (the X11 path only
calls `SetFullscreen(true)`).

Related: #2804, #4273, #4274.
