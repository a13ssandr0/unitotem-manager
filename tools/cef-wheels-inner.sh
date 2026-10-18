#!/bin/bash
# Runs INSIDE a debian:trixie (amd64) container, started by tools/build-cef-wheels.sh. Builds the cefpython3 wheels for
# Python 3.10-3.14 on x86_64 (native) and aarch64 (CROSS compiled with gcc-aarch64-linux-gnu and multiarch arm64 libraries:
# nothing runs under emulation) against the CEF distributions built from source with the proprietary codecs.
#
# /work/src        the vendor/cefpython sources (read only copy of the submodule, without build/)
# /work/distrib    binary_distrib of the CEF build (cef_binary_*_linux64 and *_linuxarm64 directories)
# /work/out        the wheels
# /work/tmp        scratch (kept between runs: wrapper builds are reused)
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
VERSION="${WHEEL_VERSION:-147.0+codecs}"
PYTHONS="${PYTHONS:-3.10 3.11 3.12 3.13 3.14}"
ARCHES="${ARCHES:-x86_64 aarch64}"
JOBS="$(nproc)"
W=/work/tmp
mkdir -p "$W" /work/out

log() { echo "=== $(date +%T) $*"; }

log "packages"
dpkg --add-architecture arm64
apt-get update -qq
apt-get install -y -qq --no-install-recommends build-essential cmake ninja-build pkg-config curl ca-certificates rsync \
    python3 python3-venv binutils gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    libgtk-3-dev libx11-dev libglib2.0-dev libgtk-3-dev:arm64 libx11-dev:arm64 libglib2.0-dev:arm64 >/dev/null

log "uv + interpreters (build tools run on the host; for aarch64 only the headers of its own Python are used)"
python3 -m venv "$W/uv-venv"
"$W/uv-venv/bin/pip" -q install uv
UV="$W/uv-venv/bin/uv"
export UV_PYTHON_INSTALL_DIR="$W/pythons"
for v in $PYTHONS; do
    "$UV" python install "$v" >/dev/null
    if [ ! -x "$W/venv-$v/bin/python" ]; then
        "$UV" venv -q --python "$v" "$W/venv-$v"
        "$UV" pip install -q --python "$W/venv-$v/bin/python" "cython==3.2.9" tomli
    fi
done

# python-build-standalone aarch64 builds: only include/ is needed (an extension is not linked against libpython)
aarch64_include() {
    local v="$1" dir="$W/py-aarch64-$v"
    if [ ! -d "$dir/python/include" ]; then
        mkdir -p "$dir"
        url=$("$UV" python list "$v" --all-platforms --all-arches --output-format json \
              | python3 -c 'import json,sys
for e in json.load(sys.stdin):
    if e.get("url") and e["implementation"]=="cpython" and e["variant"]=="default" and e["arch"]=="aarch64" and e["os"]=="linux" and e["libc"]=="gnu":
        print(e["url"]); break')
        [ -n "$url" ] || { echo "no aarch64 python $v" >&2; exit 1; }
        curl -fsSL "$url" | tar -xz -C "$dir"
    fi
    ls -d "$dir"/python/include/python*
}

cat > "$W/aarch64.cmake" <<'EOF'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_LIBRARY_ARCHITECTURE aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
EOF

# One prebuilt CEF layout per architecture: bin/ (Release + Resources, libcef.so stripped of debug info), lib/ (the wrapper), include/
prepare_cef() {
    local arch="$1" suffix triple strip
    if [ "$arch" = x86_64 ]; then suffix=linux64; triple=x86_64-linux-gnu; strip=strip; else suffix=linuxarm64; triple=aarch64-linux-gnu; strip=aarch64-linux-gnu-strip; fi
    local src; src=$(ls -d /work/distrib/cef_binary_*_"$suffix")
    local root="$W/cef-$arch"
    [ -f "$root/.ready" ] && return 0
    log "CEF $arch: wrapper from $src"
    rm -rf "$root"; mkdir -p "$root/bin" "$root/lib"
    local dist="$W/dist-$arch"; rm -rf "$dist"; cp -a "$src" "$dist"
    # the distribution's cmake adds -m64/-march=x86-64 to every 64 bit target
    if [ "$arch" = aarch64 ]; then
        sed -i -E 's/[ \t]+-m64\b//; s/[ \t]+-march=x86-64\b//' "$dist/cmake/cef_variables.cmake"
    fi
    local tc=(); [ "$arch" = aarch64 ] && tc=(-DCMAKE_TOOLCHAIN_FILE="$W/aarch64.cmake")
    cmake -S "$dist" -B "$dist/build_wrapper" -G Ninja -DCMAKE_BUILD_TYPE=Release "${tc[@]}" \
        -DCMAKE_CXX_FLAGS="-Wno-self-move -Wno-deprecated-declarations" >/dev/null
    cmake --build "$dist/build_wrapper" --target libcef_dll_wrapper --parallel "$JOBS"
    cp -a "$dist/Release/." "$root/bin/"
    cp -a "$dist/Resources/." "$root/bin/"
    cp "$dist/build_wrapper/libcef_dll_wrapper/libcef_dll_wrapper.a" "$root/lib/"
    cp -a "$dist/include" "$root/include"
    "$strip" --strip-debug "$root/bin/libcef.so"
    readelf -h "$root/bin/libcef.so" | grep -E "Machine"
    touch "$root/.ready"
}

build_wheel() {
    local arch="$1" v="$2" tag="cp${2/./}"
    local b="$W/build-$arch-$v" pkg="$W/pkg-$arch-$v" py="$W/venv-$v/bin/python"
    log "wheel $arch cp${v/./}"
    ls /work/out/cefpython3-"$VERSION"-"$tag"-"$tag"-*"$arch"*.whl >/dev/null 2>&1 && return 0     # already built
    rm -rf "$b" "$pkg"
    rsync -a --exclude build --exclude .git --include cefpython3/__init__.py --exclude "cefpython3/*" /work/src/ "$pkg/"
    [ "$(ls "$pkg/cefpython3" | wc -l)" = 1 ] || { echo "stale files in the package directory" >&2; exit 1; }
    local cm=(-DCEF_ROOT="$W/cef-$arch" -DPython_EXECUTABLE="$py" -DCYTHON_EXECUTABLE="$W/venv-$v/bin/cython"
          # libcef.so needs NSS, ALSA, ... : resolved when it is loaded, not here (their -dev packages cannot be co-installed for two architectures)
          -DCMAKE_MODULE_LINKER_FLAGS=-Wl,--allow-shlib-undefined -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined)
    local pcfg=()
    if [ "$arch" = aarch64 ]; then
        cm+=(-DCMAKE_TOOLCHAIN_FILE="$W/aarch64.cmake" -DPython_INCLUDE_DIR="$(aarch64_include "$v")" -DPython_FIND_STRATEGY=LOCATION)
        pcfg=(env PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig)
    fi
    "${pcfg[@]}" cmake -S /work/src -B "$b" -G Ninja -DCMAKE_BUILD_TYPE=Release "${cm[@]}" >/dev/null
    "${pcfg[@]}" cmake --build "$b" --parallel "$JOBS"
    "${pcfg[@]}" cmake --install "$b" --prefix "$pkg"
    # cefpython3/__init__.py imports `cefpython_py<XY>`: drop the interpreter-specific ABI suffix, which on a cross build
    # would name the HOST architecture
    for f in "$pkg"/cefpython3/cefpython_py${v/./}.*.so; do [ -e "$f" ] && mv "$f" "$pkg/cefpython3/cefpython_py${v/./}.so"; done
    [ "$(ls "$pkg"/cefpython3/cefpython_py*.so | wc -l)" = 1 ] || { echo "expected exactly one extension module" >&2; exit 1; }
    local ptag; [ "$arch" = x86_64 ] && ptag=linux_x86_64 || ptag=linux_aarch64
    # build_distrib.py names the wheel after the RUNNING interpreter and platform: run it under the matching Python and
    # pin the platform tag; libcef.so was stripped when the CEF layout was prepared
    (cd "$pkg" && "$py" - "$VERSION" "$ptag" <<'EOF'
import sys
sys.path.insert(0, 'tools')
import build_distrib as bd
version, ptag = sys.argv[1:3]
bd._wheel_platform_tag = lambda: ptag
bd._reduce_package_size_issue262 = lambda pkg_dir: None
sys.argv = ['build_distrib.py', '--out-dir', '/work/out', '--version', version]
bd.main()
EOF
    )
    rm -rf "$b" "$pkg"
}

for arch in $ARCHES; do prepare_cef "$arch"; done
for arch in $ARCHES; do for v in $PYTHONS; do build_wheel "$arch" "$v"; done; done
log "DONE"
ls -la /work/out
