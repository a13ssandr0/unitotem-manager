#!/bin/sh
# Build the Debian packages in a clean Debian 13 container, so the result does not depend on what is installed on this
# machine, and check them with lintian. Output goes to dist/.
#
#   tools/build-debs.sh third-party     # python3-fastapi-login, python3-sdbus-networkmanager
#   tools/build-debs.sh main            # unitotem-manager, unitotem-viewer, python3-cefpython3, plymouth-theme-unitotem
#   tools/build-debs.sh cefpython       # only python3-cefpython3 (the one architecture-dependent package), no npm
#   tools/build-debs.sh labwc           # labwc 0.8.4 for Debian 13 (amd64 image: trixie has 0.8.3, which lacks the HideCursor action)
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

build_labwc() {
    # Debian 13 ships labwc 0.8.3. The pointer of the compositor is hidden with the HideCursor action, added in 0.8.4 (same wlroots 0.18,
    # no other dependency change). Debian source package 0.8.3-1 (its packaging has no patches) with the upstream 0.8.4 tarball.
    lw_version=0.8.4
    lw_sha256=2d3ded90f78efb5060f7057ea802c78a79dc9b2e82ae7a2ad902af957b8b9797
    apt-get install -y -qq --no-install-recommends curl >/dev/null
    sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/debian.sources
    apt-get update -qq
    rm -rf /tmp/build/labwc; mkdir -p /tmp/build/labwc; cd /tmp/build/labwc
    apt-get source --download-only labwc >/dev/null 2>&1
    dpkg-source -x labwc_*.dsc old >/dev/null 2>&1
    curl -fsSL -o "labwc_$lw_version.orig.tar.gz" "https://github.com/labwc/labwc/archive/refs/tags/$lw_version.tar.gz"
    echo "$lw_sha256  labwc_$lw_version.orig.tar.gz" | sha256sum -c
    mkdir "labwc-$lw_version"; tar -xzf "labwc_$lw_version.orig.tar.gz" -C "labwc-$lw_version" --strip-components=1
    cp -r old/debian "labwc-$lw_version/debian"
    cd "labwc-$lw_version"
    DEBFULLNAME="Alessandro Campolo" DEBEMAIL=campolo.alex@gmail.com dch -v "$lw_version-1~unitotem1" -D trixie --force-distribution \
        "Upstream $lw_version (HideCursor action, touch override with --merge-config) rebuilt for Debian 13; no other change."
    apt-get build-dep -y -qq . >/dev/null
    dpkg-buildpackage -us -uc -b >/dev/null
    cp ../labwc_*unitotem1_*.deb /out/
}

case "$WHAT" in main|all) build_main ;; esac
case "$WHAT" in labwc|all) build_labwc ;; esac
case "$WHAT" in cefpython) build_cefpython ;; esac

echo "=== lintian"
cd /out
for deb in $(find /out -newer /tmp/started -name "*.deb"); do echo "--- $deb"; lintian --info --display-info "$deb" 2>&1 | head -30 || true; done
'
ls -la "$REPO/dist"
