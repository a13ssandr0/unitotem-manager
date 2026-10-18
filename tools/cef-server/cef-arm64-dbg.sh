#!/bin/bash
# libcef.so arm64 (cross) with DCHECK/DVLOG enabled, in its OWN out dir (out/Dbg_arm64): only to see why the V4L2 decoder
# pipeline stalls on the Pi 4. The release tree (out/Release_GN_arm64) is not touched.
set -u
W=${CEF_WORK:-/nas/cef-build/work}
export PATH=${CEF_DEPOT_TOOLS:-/nas/cef-build/depot_tools}:$PATH DEPOT_TOOLS_UPDATE=0
cd $W/chromium/src || exit 1
mkdir -p out/Dbg_arm64
cp out/Release_GN_arm64/args.gn out/Dbg_arm64/args.gn
grep -q dcheck_always_on out/Dbg_arm64/args.gn || printf '\ndcheck_always_on=true\n' >> out/Dbg_arm64/args.gn
echo "$(date +%T) gn gen"; gn gen out/Dbg_arm64 || exit 1
echo "$(date +%T) build libcef"; autoninja -C out/Dbg_arm64 libcef && echo "$(date +%T) BUILD OK"
