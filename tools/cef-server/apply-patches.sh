#!/bin/bash
# Apply UniTotem's numbered Chromium patches (patches/0*.patch) inside chromium/src, idempotently.
#
#   ./apply-patches.sh [CHROMIUM_SRC]      # default: $CEF_WORK/chromium/src, CEF_WORK=/nas/cef-build/work
#   DRY_RUN=1 ./apply-patches.sh ...       # only check, change nothing
#
# For each patch: already applied (reverse applies cleanly) -> skipped; applies cleanly (git apply --check) -> applied;
# anything else -> stop with an error before touching more files. experimental-*.patch are NEVER applied.
# Run it after automate-git.py has applied CEF's own patches (our patches touch files CEF does not patch).
#
# patches/cef/ is a `git format-patch` series against the CEF repository (chromium/src/cef, commit d58e84d17) with the Wayland
# work (CefWindow::SetFullscreenOnDisplay, CefDisplay::GetLabel, ...; see docs/cef-wayland/). The patches are applied (not
# committed) in chromium/src/cef with the same rules. After applying them run `python3 tools/version_manager.py -u --fast-check`
# in chromium/src/cef to regenerate the C API wrappers, then rebuild.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-${CEF_WORK:-/nas/cef-build/work}/chromium/src}"
[ -d "$SRC/.git" ] || { echo "not a Chromium checkout: $SRC" >&2; exit 2; }
cd "$SRC"
shopt -s nullglob
patches=("$HERE"/patches/0*.patch)
[ ${#patches[@]} -gt 0 ] || { echo "no numbered patches in $HERE/patches" >&2; exit 2; }
for p in "${patches[@]}"; do
    name="$(basename "$p")"
    if git apply --check --reverse "$p" 2>/dev/null; then
        echo "already applied: $name"
    elif git apply --check "$p" 2>/dev/null; then
        if [ -n "${DRY_RUN:-}" ]; then echo "would apply:     $name"; else git apply "$p"; echo "applied:         $name"; fi
    else
        echo "DOES NOT APPLY (neither applied nor applicable): $name" >&2
        git apply --check "$p" >&2 || true
        exit 1
    fi
done

# CEF series: it is one unit (later patches touch lines of earlier ones), so it is checked as a whole: applied -> skipped, applicable
# -> applied (git apply works on the working tree; the commit messages in the files are ignored).
CEF="$SRC/cef"
cef_patches=("$HERE"/patches/cef/0*.patch)
if [ ${#cef_patches[@]} -gt 0 ] && [ -d "$CEF/.git" ]; then
    cd "$CEF"
    series="$(mktemp)"; trap 'rm -f "$series"' EXIT
    cat "${cef_patches[@]}" > "$series"
    if git apply --check --reverse "$series" 2>/dev/null; then
        echo "already applied: patches/cef/ (${#cef_patches[@]} patches)"
    elif git apply --check "$series" 2>/dev/null; then
        if [ -n "${DRY_RUN:-}" ]; then echo "would apply:     patches/cef/ (${#cef_patches[@]} patches)"; else git apply "$series"; echo "applied:         patches/cef/ (${#cef_patches[@]} patches)"; fi
    else
        echo "DOES NOT APPLY (neither applied nor applicable): patches/cef/" >&2
        git apply --check "$series" >&2 || true
        exit 1
    fi
fi
