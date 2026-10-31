# What can be left out of the CEF build

Question (2026-10-31): the viewer shows no browser interface, so can the build drop the Chrome interface and the other features nobody
uses (password manager, autofill, translation, printing...), to save space? Everything below was measured on the build server
(`/nas/cef-build/work/chromium/src`, CEF 147 / Chromium 147.0.7727.118, the tree of `out/Release_GN_x64`); the commands are in
`tools/cef-server/` and at the end of this note.

## 1. The windows are already Alloy style; what remains is the Chrome bootstrap

`BrowserView.GetRuntimeStyle()` of a viewer window answers 2 = `RUNTIME_STYLE_ALLOY` (checked on a board; cefpython's delegates default to it).
That does not make the browser process an "Alloy" one: since CEF dropped the Alloy bootstrap, every CEF process runs Chrome's own main
delegate. `cef/BUILD.gn` builds `//chrome/app/chrome_main_delegate.cc` into libcef and depends on `//chrome:dependencies`; Alloy style is a
thin layer on top (`libcef/browser/alloy` holds only the browser host and platform delegate). So the profile has `Preferences`, a password
manager service, autofill, safe browsing and a component updater although no window of ours draws them. The upstream statement is the
same: "the Alloy bootstrap has been removed", Chrome style is the default and `--use-alloy-style` selects the style, not another build
(https://github.com/chromiumembedded/cef/issues/3685).

## 2. GN arguments: what `gn gen` accepts

`tools/cef-server/probe-gn-args.sh` generates the build with one argument set at a time into a scratch directory (only ninja files, nothing
is compiled) and counts the steps ninja plans for `libcef.so` (base: 58 284).

Refused, with the reason (Chromium and CEF assert them, or other targets use them unconditionally):

| argument | refused because |
| --- | --- |
| `safe_browsing_mode=0` | unresolved dependencies |
| `enable_extensions=false` | `chrome/test/BUILD.gn` uses an identifier it defines |
| `enable_supervised_users=false` | `assert(enable_supervised_users)` in `chrome/test` |
| `enable_screen_ai_service=false` | `assert(enable_screen_ai_service)` in `chrome/browser/screen_ai` |
| `enable_speech_service=false` | `assert(enable_speech_service)` in `chrome/services/speech` |
| `enable_on_device_translation=false` | `assert(enable_on_device_translation)` |
| `enable_printing=false`, `enable_pdf=false` | `assert(enable_print_preview)` in `cef/BUILD.gn` |
| `enable_plugins=false` | the PDF viewer needs the plugin infrastructure |
| `enable_widevine=false` | `assert(enable_widevine)` in `cef/BUILD.gn` (required argument) |

Accepted (steps removed from 58 284): `use_gtk=false` 170, `enable_media_remoting*`/`enable_remoting` 154, `enable_vr` 110,
`enable_webui_certificate_viewer`/`tab_strip` 40, `enable_compose` 32, `enable_service_discovery`+`enable_mdns` 15, `use_cups` 11,
`enable_captive_portal_detection` 7, `enable_paint_preview` 5; Wayland only (`ozone_auto_platforms=false ozone_platform="wayland"
ozone_platform_x11=false ozone_platform_headless=false`) 328. All together with `use_bluez=false use_kerberos=false`: **57 588 steps
(-696, 1.2 %)**; adding `use_dawn=false` (-16 more) removes WebGPU and was left out. `optimize_for_size=true` is accepted and changes the step count
by nothing (it changes flags, not what is compiled; it would cost speed in Blink, not wanted on a Pi playing video).

## 3. Where the bytes of libcef.so are

`libcef.so` x64, stripped: 252 MiB (aarch64 wheel: 239 MiB); `.text` 202 MB, `.rodata` 21.5 MB, `.data.rel.ro` 11.7 MB.
Defined symbols attributed to the leading namespace (`llvm-nm -S -C`, `nm-areas.py` below), 250 MB in all:

| area | MB | share |
| --- | ---: | ---: |
| no namespace (C: skia, ffmpeg, vpx, aom, dav1d, sqlite, zstd...) | 51 | 20 % |
| blink | 39.5 | 15.8 % |
| v8 | 19.6 | 7.9 % |
| std (libc++ templates) | 10.3 | 4.1 % |
| content | 9.0 | 3.6 % |
| base | 8.9 | 3.6 % |
| mojo | 5.5 | 2.2 % |
| extensions | 5.3 | 2.1 % |
| webrtc | 4.0 | 1.6 % |
| net | 4.0 | 1.6 % |
| autofill | 3.0 | 1.2 % |
| media | 2.5 | 1.0 % |
| ui + views | 2.1 + 1.6 | 1.5 % |
| web_app, safe_browsing, optimization_guide, glic, password_manager, payments, sync, policy, enterprise | about 8 together | 3 % |

The interface of the Chrome browser is not where the weight is: `chrome::` is not even in the first 45 namespaces, `views` is 1.6 MB, the
password manager 0.6 MB, autofill 3 MB. Everything that could be called "browser features nobody here uses" (extensions, autofill, web apps,
safe browsing, optimization guide, glic, passwords, payments, sync, policy, enterprise) adds up to about 25 MB (10 %), and none of it has
a build switch that `gn` accepts. The C code adds up too: the video decoders/encoders in it (av1, vpx, dav1d, aom, ffmpeg) are about 3.5 MB together,
and they are what plays the assets.

By object size, `chrome/browser/ui` is 684 MB of the 6.9 GB of objects (bitcode, so only a proportion) and `chrome/` 1.5 GB: cutting it means
cutting `//chrome:dependencies` out of libcef, i.e. a patch series on Chromium's build graph that has to be redone at every Chromium update,
for at most the 25 MB above.

## 4. What the package carries besides libcef.so (board, 334 MB installed)

| file | MB | decision |
| --- | ---: | --- |
| `locales/*.pak` (220 files) | 49 | **done**: only `en-US.pak` stays (`debian/rules`); no translated string is ever displayed |
| `libvk_swiftshader.so` + `vk_swiftshader_icd.json` | 16.8 | the software fallback of the GPU process on a machine without a working GPU (the test VM): keep, unless the user decides otherwise |
| `resources.pak` | 12 | needed (it holds the DevTools front end that the web UI opens) |
| `icudtl.dat` | 10.6 | needed |
| `chrome_100_percent.pak`, `chrome_200_percent.pak` | 1.9 | images of the Chrome interface; not removed (Chromium loads them at start, untested) |

`libcef.so` `NEEDED` libraries: libX11, libxcb, libXcomposite, libXdamage, libXext, libXfixes, libXrandr, libcups, libatk(-bridge), libatspi,
libcairo, libpango, libasound... A Wayland-only, GTK-free, CUPS-free build would no longer need the X11 ones, libcups, cairo and pango, but the
kiosk image carries them anyway for other packages (Mesa's GLX, Qt, GStreamer, ghostscript, ddcutil), so the image does not get smaller by it;
only the package's own `Depends` get shorter.

## 5. Conclusion

* The viewer already uses the Alloy style; there is no Alloy-only build to choose any more.
* The one saving that costs nothing and is in the package: the 220 locale files (49 MB), done.
* Build arguments can remove about 1.2 % of the build and a few packages from `Depends`; the real effect on `libcef.so` is measured by the build
  of section 6.
* Dropping the Chrome interface as such is not an argument: it is a fork of Chromium's dependency graph, for at most ~25 MB of 252.

## 6. Measured build (`out/Release_Lean_x64`)

`tools/cef-server/cef-lean-x64.sh` builds x64 `libcef` with the accepted set above (its own out directory; the release trees are not touched).
Result: see the end of this file once the build has finished.

## Tools

* `tools/cef-server/probe-gn-args.sh <results> < sets` : one `gn gen` and a step count per set (`name|arg arg ...` per line); it needs
  `chrome_pgo_phase=0` because the PGO profile is owned by another user on the server.
* `tools/cef-server/nm-areas.py`: `python3 nm-areas.py <llvm-nm> libcef.so` prints the table of section 3 (leading namespace of every defined symbol, summed).
* `tools/cef-server/cef-lean-x64.sh`: the measured build.
* The deletion of old trees on the server was done by the user's permission for everything under `/nas` that this project created
  (`out/Dbg_arm64`, 20 GB, rebuildable with `cef-arm64-dbg.sh`; `/nas/cef-master-check`, 38 GB, the check against CEF master of
  `docs/cef-wayland/pr-cef.md`, rebuildable with its `sync.sh`); `/nas/build` belongs to the unitotem-system project and was not touched.
