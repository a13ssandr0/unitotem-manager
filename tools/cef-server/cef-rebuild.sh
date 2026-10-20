#!/bin/bash
# Rebuild BOTH distributions (x64 native, arm64 cross with V4L2) after a change to the patched tree (tools/cef-server/patches),
# and make the distributions the wheels are built from. Incremental: minutes when only a few files changed. Re-runnable.
#   sudo systemd-run --unit=cef-rebuild --working-directory=/nas/cef-build/work /nas/cef-build/work/cef-rebuild.sh
set -u
W=${CEF_WORK:-/nas/cef-build/work}
DT=${CEF_DEPOT_TOOLS:-/nas/cef-build/depot_tools}
HASH=d58e84d17dd3f646c906ac633156cd0ec46638e9
BRANCH=7727
export PATH=$DT:$PATH
export DEPOT_TOOLS_UPDATE=0
cd $W || exit 1
log() { echo "$(date +%F\ %T) $*" | tee -a logs/stages.log; }
COMMON="use_sysroot=true symbol_level=1 is_official_build=true proprietary_codecs=true ffmpeg_branding=Chrome"
log "rebuild x64"
GN_DEFINES="$COMMON" python3 automate-git.py --download-dir=$W --depot-tools-dir=$DT --no-depot-tools-update --branch=$BRANCH --checkout=$HASH \
  --no-update --x64-build --no-debug-build --no-distrib-docs --build-target=cefsimple --force-build > logs/x64-rebuild.log 2>&1 \
  && log "rebuild x64 done" || { log "rebuild x64 FAILED"; exit 1; }
log "rebuild arm64"
GN_DEFINES="$COMMON use_v4l2_codec=true use_vaapi=false" python3 automate-git.py --download-dir=$W --depot-tools-dir=$DT --no-depot-tools-update \
  --branch=$BRANCH --checkout=$HASH --no-update --arm64-build --no-debug-build --no-distrib-docs --build-target=cefsimple --force-build \
  > logs/arm64-rebuild.log 2>&1 && log "rebuild arm64 done" || { log "rebuild arm64 FAILED"; exit 1; }
