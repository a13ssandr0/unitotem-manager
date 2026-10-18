#!/bin/bash
# What it does: builds CEF 147 (Chromium branch 7727) from source with proprietary codecs (H.264/AAC): x64 native, then arm64 cross
# (sysroot). Logs and stage markers are in $W/logs/. Re-runnable: finished stages (logs/x64.done, logs/arm64.done) are skipped.
# Runs on the build server; paths can be overridden from the environment (CEF_WORK, CEF_DEPOT_TOOLS). See README.md.
set -u
W=${CEF_WORK:-/nas/cef-build/work}
DT=${CEF_DEPOT_TOOLS:-/nas/cef-build/depot_tools}
HASH=d58e84d17dd3f646c906ac633156cd0ec46638e9
BRANCH=7727
export PATH=$DT:$PATH
export DEPOT_TOOLS_UPDATE=0
export GN_DEFINES="use_sysroot=true symbol_level=1 is_official_build=true proprietary_codecs=true ffmpeg_branding=Chrome"
cd $W || exit 1
log() { echo "$(date +%F\ %T) $*" | tee -a logs/stages.log; }

if [ ! -f logs/x64.done ]; then
  log "x64: download + build"
  python3 automate-git.py --download-dir=$W --depot-tools-dir=$DT --no-depot-tools-update --branch=$BRANCH --checkout=$HASH \
    --no-chromium-history --with-pgo-profiles --x64-build --no-debug-build --no-distrib-docs --build-target=cefsimple --force-build \
    > logs/x64.log 2>&1 && touch logs/x64.done
  [ -f logs/x64.done ] || { log "x64 FAILED (tree kept)"; exit 1; }
  log "x64 done"
fi

if [ ! -f logs/arm64.done ]; then
  log "arm64: sysroot + cross build"
  ( cd chromium/src && python3 build/linux/sysroot_scripts/install-sysroot.py --arch=arm64 ) > logs/arm64-sysroot.log 2>&1 \
    || { log "arm64 sysroot FAILED"; exit 1; }
  python3 automate-git.py --download-dir=$W --depot-tools-dir=$DT --no-depot-tools-update --branch=$BRANCH --checkout=$HASH \
    --no-update --arm64-build --no-debug-build --no-distrib-docs --build-target=cefsimple --force-build \
    > logs/arm64.log 2>&1 && touch logs/arm64.done
  [ -f logs/arm64.done ] || { log "arm64 FAILED (tree kept)"; exit 1; }
  log "arm64 done"
fi
log "ALL CEF BUILDS DONE"
