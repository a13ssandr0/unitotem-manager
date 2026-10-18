#!/bin/sh
# Build the Debian packages in a clean Debian 13 container, so the result does not depend on what is installed on this
# machine, and check them with lintian. Output goes to dist/.
#
#   tools/build-debs.sh third-party     # python3-fastapi-login, python3-sdbus-networkmanager
#   tools/build-debs.sh main            # unitotem-manager, unitotem-viewer, python3-cefpython3, plymouth-theme-unitotem
#   tools/build-debs.sh cefpython       # only python3-cefpython3 (the one architecture-dependent package), no npm
#   tools/build-debs.sh all
#
# `TARGET_ARCH=arm64 tools/build-debs.sh cefpython` CROSS builds the arm64 python3-cefpython3 (arch-all packages are the same on every
# architecture, so that is all an arm64 target needs). Wheel: vendor/cefpython-wheels/.
#
# The container is amd64. arm64 packages are built the same way on an arm64 machine (or under qemu).
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WHAT="${1:-third-party}"
mkdir -p "$REPO/dist"

docker run --rm --platform "${PLATFORM:-linux/amd64}" -e TARGET_ARCH="${TARGET_ARCH:-amd64}" -v "$REPO:/src:ro" -v "$REPO/dist:/out" debian:trixie sh -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends build-essential devscripts debhelper dh-python dh-sequence-python3 pybuild-plugin-pyproject \
    python3-all nodejs npm python3-pytest python3-pip python3-poetry-core python3-setuptools lintian fakeroot ca-certificates >/dev/null
WHAT='"$WHAT"'
touch /tmp/started

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
build_cefpython() {
    # Architecture-dependent package only: nothing of the web UI is built, and only the wheel for this architecture is
    # copied (the wheels are hundreds of MB each). arm64 is CROSS built in this amd64 container (crossbuild-essential,
    # multiarch libraries for dpkg-shlibdeps): no emulation.
    host_cpu=x86_64; debarch=amd64
    if [ "${TARGET_ARCH:-amd64}" = arm64 ]; then
        host_cpu=aarch64; debarch=arm64
        dpkg --add-architecture arm64; apt-get update -qq
        apt-get install -y -qq --no-install-recommends crossbuild-essential-arm64 binutils-aarch64-linux-gnu >/dev/null
    fi
    rm -rf /tmp/build/cef; mkdir -p /tmp/build/cef/vendor/cefpython-wheels
    cp -a /src/debian /tmp/build/cef/debian
    cp /src/vendor/cefpython-wheels/cefpython3-*-cp313-*-linux_$host_cpu.whl /tmp/build/cef/vendor/cefpython-wheels/
    cd /tmp/build/cef
    apt-get build-dep -y -qq -a$debarch --arch-only . >/dev/null
    dpkg-buildpackage -us -uc -B -a$debarch
    cp ../python3-cefpython3_*.deb /out/
}

case "$WHAT" in main|all) build_main ;; esac
case "$WHAT" in cefpython) build_cefpython ;; esac

echo "=== lintian"
cd /out
for deb in $(find /out -newer /tmp/started -name "*.deb"); do echo "--- $deb"; lintian --info --display-info "$deb" 2>&1 | head -30 || true; done
'
ls -la "$REPO/dist"
