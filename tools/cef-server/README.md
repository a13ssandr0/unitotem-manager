# CEF build scripts (build server)

Scripts that build CEF 147 (Chromium branch 7727, commit `d58e84d17dd3f646c906ac633156cd0ec46638e9`) from source with the
proprietary codecs, on the build server `alessandro@supercompiler` (128 cores / 256 GB, sudo NOPASSWD). They used to live only
there; this directory is the source of truth. `./sync.sh push` copies them to `/nas/cef-build/work/`, `./sync.sh pull` brings
the server's version back. The wheels themselves are built afterwards by `tools/build-cef-wheels.sh`.

Layout on the server: `/nas/cef-build/work/` (checkout: `chromium/`, `cef/`, logs in `logs/`, `automate-git.py` from upstream CEF,
not copied here), `/nas/cef-build/depot_tools`. Override with `CEF_WORK` / `CEF_DEPOT_TOOLS`. The tree is NEVER deleted without the
user's say-so: it is what makes rebuilds incremental.

## Scripts

| script | what |
|---|---|
| `build-all.sh` | x64 native (download + build), then arm64 cross (sysroot). Finished stages are skipped (`logs/x64.done`, `logs/arm64.done`). |
| `cef-arm64-v4l2.sh` | arm64 rebuild with V4L2 hardware decode for the Pi. Moves the previous distribution to `binary_distrib-arm64-nov4l2/` once. |
| `cef-arm64-dbg.sh` | `libcef.so` arm64 with `dcheck_always_on=true` in its own `out/Dbg_arm64` (release tree untouched), to debug the V4L2 stall. |
| `sync.sh` | rsync of the scripts to/from the server. |

## GN_DEFINES

Common: `use_sysroot=true symbol_level=1 is_official_build=true proprietary_codecs=true ffmpeg_branding=Chrome`

- `proprietary_codecs=true ffmpeg_branding=Chrome`: mandatory, every build ships H.264/AAC (ffmpeg is linked into `libcef.so`, no swap possible).
- `is_official_build=true`: never false; a non-official build segfaults in Skia (`malloc_usable_size`, see CLAUDE.md).
  Needs PGO profiles (`--with-pgo-profiles`, `checkout_pgo_profiles`).
- `use_sysroot=true`: Debian sysroot, needed for the arm64 cross build (`install-sysroot.py --arch=arm64`).
- arm64 with V4L2: add `use_v4l2_codec=true use_vaapi=false`. They are mutually exclusive (`gn gen` asserts), and on arm64 `use_vaapi`
  defaults to false while `use_v4l2_codec` defaults to false everywhere, so without it no hardware decode client is compiled in.
- x64: the common set only (VA-API is the default there).
- Debug-ish build (`Dbg_arm64`): the release `args.gn` plus `dcheck_always_on=true`; a release build compiles every `DVLOG` out.

## Running

Host tools needed: `pkg-config gperf bison flex` (plus python3, git). As root, git needs `git config --global --add safe.directory '*'`.

Start as a systemd unit so a lost ssh connection does not stop the build (units do NOT survive a reboot, re-run the script, it is re-runnable):

    sudo systemd-run --unit=cef-build --working-directory=/nas/cef-build/work /nas/cef-build/work/build-all.sh
    sudo systemctl status cef-build        # or: tail -f /nas/cef-build/work/logs/stages.log

Markers and logs in `work/logs/`: `stages.log` (stage timeline), `x64.log`, `arm64.log`, `arm64-v4l2.log`, `arm64-sysroot.log`,
`arm64-dbg.log`; `x64.done`, `arm64.done`, `arm64-v4l2.done` mark finished stages (delete one marker to redo that stage; the V4L2 script
removes its own). Distributions land in `chromium/src/cef/binary_distrib/`.

## Timings

x64 full build 3 h 48 on the old box; arm64 cross about 35 min incremental (V4L2 rebuild), `Dbg_arm64` about 1 h 27.

## Patches (`patches/`)

Inventory checked on 2026-10-20 against the server's tree (`/nas/cef-build/work/chromium/src`, Chromium 147.0.7727.118, CEF
`d58e84d17`; `git HEAD` of the Chromium checkout is the pristine base): of the 439 files `git diff` reports as modified, 437 are
touched by CEF's own `cef/patch/patches/*.patch` (applied by `automate-git.py`, not ours). Exactly two are not, and they are
byte-identical to the two numbered patches below: `media/gpu/v4l2/v4l2_stateful_video_decoder.cc` (0001) and
`ui/gfx/linux/gbm_wrapper.cc` (0002). The CEF checkout (`chromium/src/cef`) has no local modifications. Nothing of ours is
uncaptured. All three patch files pass `git apply --check` on the pristine files of 147.0.7727.118 (0001 and 0002 also together,
and 0001 together with the experimental one).

Patches are plain `git diff` output relative to `chromium/src`, applied AFTER `automate-git.py` has applied CEF's patches, with:

    tools/cef-server/apply-patches.sh [/nas/cef-build/work/chromium/src]      # DRY_RUN=1 to only check

It is idempotent (an already applied patch is detected with `git apply --check --reverse` and skipped), checks each patch before
applying it, stops on the first one that fits neither state, and never applies `experimental-*`. The build scripts do not call it:
run it by hand before `cef-rebuild.sh` (or before `build-all.sh` on a fresh checkout). `automate-git.py --force-clean` or
`--force-update` resets the files, so re-run it afterwards.

| patch | file | fixes | runtime switch |
|---|---|---|---|
| `0001-v4l2-stateful-nv12-candidate-first.patch` | `media/gpu/v4l2/v4l2_stateful_video_decoder.cc` | Lists NV12 first among the CAPTURE formats and tells the driver the picked one (`SetFormat`). Without it the V4L2 stateful decoder on a Pi 4 stalls silently (no ImageProcessor for YU12 -> AR24). With it Chromium picks the LibYUV image processor (NV12 -> AR24 on the CPU); the GL image processor only does NV12 -> NV12. **Measured slower than software decode, so the feature is NOT enabled** (`AcceleratedVideoDecoder` stays out of the `pi` profile); the patch is kept as the base for further work. Only matters in a build with `use_v4l2_codec=true`. | `--enable-features=AcceleratedVideoDecoder` (not used) |
| `0002-gbm-no-scanout-buffers-env.patch` | `ui/gfx/linux/gbm_wrapper.cc` | Mesa's `gbm_bo_create_with_modifiers()` makes every buffer scanout capable and on the Pi 4 (V3D) a scanout buffer can only be linear, so the Wayland compositor presents every frame two vblanks late (30 fps). With the variable set, buffers are created through `gbm_bo_create_with_modifiers2()` without the scanout flag (tiled UIF layout, 60 fps). Behaviour is unchanged when the variable is unset. | env `UNITOTEM_NO_SCANOUT_BUFFERS` (any value; read once; the viewer sets it for the `pi` GPU profile, `unitotem_viewer/app.py`) |
| `experimental-libyuv-cached-bounce.patch` | `media/gpu/chromeos/libyuv_image_processor_backend.cc` | NOT shipped, NOT applied by the script. Copies the NV12 frame to cached memory, converts there and copies the ARGB out (the dmabuf mappings are uncached on the Pi 4: reading 2 MB takes 5-14 ms, libyuv straight on them 60-120 ms per 1080p frame; bounce: 33-55 ms). Still ~15 fps at 1080p with 55% dropped frames. | none |

## Rebuilding and making wheels

1. `./sync.sh push` (scripts to the server), then on the server `apply-patches.sh` (the copy in `/nas/cef-build/work`).
2. `sudo systemd-run --unit=cef-rebuild --working-directory=/nas/cef-build/work /nas/cef-build/work/cef-rebuild.sh` rebuilds BOTH
   distributions (x64 native, arm64 cross with V4L2) incrementally and re-creates `chromium/src/cef/binary_distrib/`. Follow it with
   `tail -f /nas/cef-build/work/logs/stages.log`. Never run two builds at once and never delete the tree.
3. `tools/build-cef-wheels.sh start|status|fetch` (from the repository): copies `vendor/cefpython`, builds cp310-cp314 wheels for x86_64
   and aarch64 inside a trixie container from those distributions (`WHEEL_VERSION=147.0+codecs`), and `fetch` rsyncs them to
   `vendor/cefpython-wheels/` (not in git). `python3-cefpython3` for arm64: `TARGET_ARCH=arm64 tools/build-debs.sh cefpython`.
