#!/bin/bash
# libcef.so x64 with the lean GN set in its OWN out dir (out/Release_Lean_x64); the release trees are not touched.
# Measures how much a Wayland-only, GTK/CUPS/Bluetooth/Kerberos-free build really saves (docs/cef-lean-build.md).
set -u
export PATH=/nas/cef-build/depot_tools:$PATH DEPOT_TOOLS_UPDATE=0
cd /nas/cef-build/work/chromium/src || exit 1
OUT=out/Release_Lean_x64
mkdir -p $OUT
cp out/Release_GN_x64/args.gn $OUT/args.gn
printf '\n' >> $OUT/args.gn         # the copied file has no final newline
cat >> $OUT/args.gn <<'ARGS'
enable_compose=false
enable_paint_preview=false
enable_vr=false
enable_service_discovery=false
enable_mdns=false
enable_media_remoting=false
enable_media_remoting_rpc=false
enable_remoting=false
enable_captive_portal_detection=false
enable_webui_certificate_viewer=false
enable_webui_tab_strip=false
use_cups=false
use_gtk=false
use_bluez=false
use_kerberos=false
ozone_auto_platforms=false
ozone_platform="wayland"
ozone_platform_wayland=true
ozone_platform_x11=false
ozone_platform_headless=false
ARGS
echo "$(date +%T) gn gen"; gn gen $OUT || exit 1
echo "$(date +%T) build libcef"; autoninja -C $OUT libcef && echo "$(date +%T) BUILD OK"
