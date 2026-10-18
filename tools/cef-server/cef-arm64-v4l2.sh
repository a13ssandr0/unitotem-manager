#!/bin/bash
# CEF 147 arm64 (cross, sysroot) WITH V4L2 hardware video decode for the Raspberry Pi (use_v4l2_codec=true), same codecs as before.
# Re-runnable; the previous arm64 distribution (no V4L2) is kept in binary_distrib-arm64-nov4l2/.
set -u
W=${CEF_WORK:-/nas/cef-build/work}
DT=${CEF_DEPOT_TOOLS:-/nas/cef-build/depot_tools}
HASH=d58e84d17dd3f646c906ac633156cd0ec46638e9
BRANCH=7727
export PATH=$DT:$PATH
export DEPOT_TOOLS_UPDATE=0
export GN_DEFINES="use_sysroot=true symbol_level=1 is_official_build=true proprietary_codecs=true ffmpeg_branding=Chrome use_v4l2_codec=true use_vaapi=false"
cd $W || exit 1
log() { echo "$(date +%F\ %T) $*" | tee -a logs/stages.log; }
D=chromium/src/cef/binary_distrib
if [ ! -d binary_distrib-arm64-nov4l2 ]; then
  mkdir binary_distrib-arm64-nov4l2
  mv $D/*linuxarm64* binary_distrib-arm64-nov4l2/
fi
rm -f logs/arm64-v4l2.done
log "arm64 v4l2: build"
python3 automate-git.py --download-dir=$W --depot-tools-dir=$DT --no-depot-tools-update --branch=$BRANCH --checkout=$HASH \
  --no-update --arm64-build --no-debug-build --no-distrib-docs --build-target=cefsimple --force-build \
  > logs/arm64-v4l2.log 2>&1 && touch logs/arm64-v4l2.done
[ -f logs/arm64-v4l2.done ] || { log "arm64 v4l2 FAILED (tree kept)"; exit 1; }
log "arm64 v4l2 done"
