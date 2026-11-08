#!/bin/sh
# Regenerate the apt repository in docs/ (flat repository served by GitHub Pages).
#
#   docs/update-pkgs.sh          build every package (tools/build-debs.sh: amd64 and arm64 python3-cefpython3),
#                                copy the packages of the current version and the third-party packages to docs/,
#                                write Packages, Packages.gz and Release. Nothing is signed.
#   docs/update-pkgs.sh sign     sign Release (Release.gpg, InRelease). Run by the maintainer after reviewing Release.
#
# Environment:
#   SKIP_BUILD=1                 do not build; use the files already in dist/
#   UNITOTEM_REPO_KEY=<id>       signing key of the repository (default: campoloalex@gmail.com)
#
# The packages expected in docs/ are the binary packages of debian/control (version of debian/changelog) and of
# packaging/*/debian/control (their own version). Architecture "any" means amd64 and arm64; "all" is one file.
# The script stops before touching docs/ when a package is missing from dist/.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCS="$ROOT/docs"
DIST="$ROOT/dist"
KEY="${UNITOTEM_REPO_KEY:-campoloalex@gmail.com}"
MODE="${1:-index}"

case "$MODE" in
    index|sign) ;;
    *) echo "usage: $0 [sign]" >&2; exit 2 ;;
esac

if [ "$MODE" = sign ]; then
    cd "$DOCS"
    if [ ! -f Release ] || [ ! Release -nt Packages ]; then
        echo "Release is missing or older than Packages: run $0 first" >&2
        exit 1
    fi
    gpg --yes --default-key "$KEY" -abs -o Release.gpg Release
    gpg --yes --default-key "$KEY" --clearsign -o InRelease Release
    echo "Signed: Release.gpg and InRelease (key $KEY)"
    exit 0
fi

# Lines "package arch version" from one debian/control file.
binaries() {
    awk -v ver="$2" '
        /^Package:/      { pkg = $2 }
        /^Architecture:/ {
            if ($2 == "any") { print pkg, "amd64", ver; print pkg, "arm64", ver }
            else print pkg, $2, ver
        }' "$1"
}

# File names of every expected package: name_version_arch.deb
expected_files() {
    VERSION="$(dpkg-parsechangelog -l "$ROOT/debian/changelog" -S Version)"
    binaries "$ROOT/debian/control" "$VERSION" | while read -r pkg arch ver; do
        echo "${pkg}_${ver}_${arch}.deb"
    done
    for dir in "$ROOT"/packaging/*/; do
        ver="$(dpkg-parsechangelog -l "$dir/debian/changelog" -S Version)"
        binaries "$dir/debian/control" "$ver" | while read -r pkg arch v; do
            echo "${pkg}_${v}_${arch}.deb"
        done
    done
}

if [ -z "${SKIP_BUILD:-}" ]; then
    cd "$ROOT"
    tools/build-debs.sh all
    TARGET_ARCH=arm64 tools/build-debs.sh cefpython
fi

# Check every file before copying anything. File names contain no glob characters: globbing is off for the word split.
FILES="$(expected_files)"
set -f
missing=""
for f in $FILES; do
    [ -f "$DIST/$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
    set +f
    echo "Missing in $DIST:$missing" >&2
    echo "Build them (tools/build-debs.sh, TARGET_ARCH=arm64 tools/build-debs.sh cefpython) or unset SKIP_BUILD." >&2
    exit 1
fi

for f in $FILES; do
    cp -f "$DIST/$f" "$DOCS/$f"
    echo "Copied $f"
done
set +f

cd "$DOCS"
dpkg-scanpackages --multiversion . > Packages
gzip -k -f Packages
apt-ftparchive -o APT::FTPArchive::Release::Architectures="amd64 arm64 all" release . > Release
echo "Written: Packages, Packages.gz, Release (not signed)"
echo "Review Release, then the maintainer runs: $0 sign"
