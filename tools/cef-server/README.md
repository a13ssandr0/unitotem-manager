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
