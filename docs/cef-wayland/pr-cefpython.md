# Draft upstream pull request: cefpython (a13ssandr0/cefpython)

Not opened. Branch `wayland-views` (pushed to a13ssandr0/cefpython), based on `cefpython147-qt` f86bdbf.

## Title

CEF Views bindings (Window, BrowserView, Display), Wayland backend selection, CreateBrowserSync on Wayland

## Description

cefpython could only open browsers as native windows (`WindowInfo`), which CEF implements for X11 only, and forced
`ozone-platform=x11`/`GDK_BACKEND=x11`. On Wayland the result is a Chrome-style window with no `app_id`/title that `CloseBrowser()`
never destroys. This adds the CEF Views framework, which works on every Ozone backend:

* `cef.Window`, `cef.Panel`, `cef.View`, `cef.BrowserView`, `cef.Display`; Python delegates (an object with optional methods named like
  CEF's `CefWindowDelegate`/`CefViewDelegate`/`CefBrowserViewDelegate`: `OnWindowCreated`, `OnWindowDestroyed`, `CanClose`,
  `IsFrameless`, `GetInitialBounds`, `GetInitialShowState`, `GetLinuxWindowProperties` -> `wayland_app_id`, ...). One C++ delegate
  (`client_handler/views_delegate.*`) forwards to Python; the Python object is kept alive by it. Windows are identified with
  `CefView::IsSame` (the library hands out a new wrapper every time).
* `Window.SetFullscreenOnDisplay(display_id)`, `Display.GetLabel()`, `Display.FindByOutputName(name)`: need the CEF patches
  of the UniTotem repository (`tools/cef-server/patches/cef`, draft upstream PR `docs/cef-wayland/pr-cef.md`); the vendored headers
  in `src/include/views` are updated.
* `ozone-platform` is chosen automatically: wayland when `WAYLAND_DISPLAY` is set and `DISPLAY` is not, x11 otherwise (so a desktop
  with XWayland keeps working as before); `GDK_BACKEND=x11` is not forced in a pure Wayland session; the `SetAsChild` warning is accurate.
* `CreateBrowserSync()` with a parentless `WindowInfo` on the Wayland backend opens a Views window and closes it with the browser.
* Docs in `api/` (Window, BrowserView, View, WindowDelegate, Display, ozone-platform), README index, migration guide; a runnable example;
  `unittests/views_test.py`.

## Testing

`unittests/views_test.py` passes on a Wayland compositor (labwc, pure Wayland, Python 3.13, CEF 147 x86_64); the documented example runs
verbatim; two outputs, hot-plug, move, close: `tools/cef-wayland-tests/outputs.py` (UniTotem repository), 18/18 on an x86_64 board and a
Raspberry Pi 4 (aarch64). The unit test also passes on the X11 backend (same board, X session). Not tested: Windows/macOS
(guarded by `IF UNAME_SYSNAME`), Python versions other than 3.13.
