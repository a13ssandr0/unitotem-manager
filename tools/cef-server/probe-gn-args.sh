#!/bin/bash
# Dry-run probe of GN argument sets for libcef.so on the build server: for each set, gn gen into ONE scratch directory
# (out/Probe_base: only ninja files, ~2 GB, nothing is compiled), report whether gn accepts it (CEF asserts some arguments) and
# how many build steps ninja plans for libcef.so. Usage: probe-gn-args.sh <results-file> < sets   (one set per line: name|arg arg ...)
set -u
export PATH=${CEF_DEPOT_TOOLS:?set CEF_DEPOT_TOOLS=<depot_tools dir on the server>}:$PATH DEPOT_TOOLS_UPDATE=0
cd "${CEF_WORK:?set CEF_WORK=<CEF work dir on the server>}/chromium/src" || exit 1
OUT=out/Probe_base
BASE=$(grep -v -E 'chrome_pgo_phase|target_cpu' /tmp/args.base.gn)
RESULTS=${1:?results file}
: > "$RESULTS"
while IFS='|' read -r name args; do
  [ -z "$name" ] && continue
  { printf '%s\n' "$BASE"; echo 'chrome_pgo_phase=0'; for a in $args; do echo "$a"; done; } > $OUT/args.gn
  if ! gn gen $OUT > /tmp/probe-gn.log 2>&1; then
    echo "$name | GN REFUSES | $(grep -m1 -E '^(ERROR|Assertion failed)' /tmp/probe-gn.log | cut -c1-150) $(grep -m2 -E 'assert|Dependency|depends' /tmp/probe-gn.log | tr '\n' ' ' | cut -c1-200)" >> "$RESULTS"
    continue
  fi
  ninja -C $OUT build.ninja > /dev/null 2>&1
  steps=$(ninja -C $OUT -n libcef.so 2>/dev/null | tail -1 | sed -E 's/^\[[0-9]+\/([0-9]+)\].*/\1/')
  echo "$name | ok | steps=$steps" >> "$RESULTS"
done
echo DONE >> "$RESULTS"
