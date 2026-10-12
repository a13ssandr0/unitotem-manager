#!/bin/sh
# Build the Debian packages in a clean Debian 13 container, so the result does not depend on what is installed on this
# machine, and check them with lintian. Output goes to dist/.
#
#   tools/build-debs.sh third-party     # python3-fastapi-login, python3-sdbus-networkmanager
#   tools/build-debs.sh main            # unitotem-manager, unitotem-viewer, python3-cefpython3, plymouth-theme-unitotem
#   tools/build-debs.sh all
#
# The container is amd64. arm64 packages are built the same way on an arm64 machine (or under qemu).
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WHAT="${1:-third-party}"
mkdir -p "$REPO/dist"

docker run --rm --platform "${PLATFORM:-linux/amd64}" -v "$REPO:/src:ro" -v "$REPO/dist:/out" debian:trixie sh -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends build-essential devscripts debhelper dh-python dh-sequence-python3 pybuild-plugin-pyproject \
    python3-all nodejs npm python3-pytest python3-pip python3-poetry-core python3-setuptools lintian fakeroot ca-certificates >/dev/null
WHAT='"$WHAT"'

build_third_party() {
    name="$1"; pypi="$2"; version="$3"
    work=/tmp/build/$name; rm -rf "$work"; mkdir -p "$work"; cd "$work"
    pip download -q --no-deps --no-binary :all: --dest . "$pypi==$version" 2>&1 | grep -v WARNING || true
    sdist=$(ls *.tar.gz)
    mkdir "$name-$version"
    tar -xzf "$sdist" -C "$name-$version" --strip-components=1
    # The upstream sdist is the original tarball of the source package
    cp "$sdist" "${name}_${version}.orig.tar.gz"
    cp -r "/src/packaging/$name/debian" "$name-$version/debian"
    (cd "$name-$version" && dpkg-buildpackage -us -uc -b >/dev/null)
    cp ./*.deb /out/
}

build_main() {
    # Own dependencies first, from the packages just built
    apt-get install -y -qq /out/python3-fastapi-login_*.deb /out/python3-sdbus-networkmanager_*.deb >/dev/null
    rm -rf /tmp/build/main; mkdir -p /tmp/build/main
    cp -a /src/. /tmp/build/main/
    cd /tmp/build/main
    rm -rf manager/static webui/node_modules dist
    apt-get build-dep -y -qq . >/dev/null
    dpkg-buildpackage -us -uc -b
    cp ../*.deb /out/
}

case "$WHAT" in
    third-party|all)
        build_third_party python-fastapi-login fastapi-login 1.10.3
        build_third_party python-sdbus-networkmanager sdbus-networkmanager 2.0.0
        ;;
esac
case "$WHAT" in main|all) build_main ;; esac

echo "=== lintian"
cd /out
for deb in python3-*.deb unitotem-*.deb plymouth-*.deb; do echo "--- $deb"; lintian --info --display-info "$deb" 2>&1 | head -30 || true; done
'
ls -la "$REPO/dist"
