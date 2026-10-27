#!/bin/bash
# Puts this checkout's manager code on an image board WITHOUT building a deb (CLAUDE.md, "Testing code on an image board"):
# the changed directories are copied under /var/lib/unitotem/ticker-test and bind-mounted over /usr/share/unitotem-manager
# (whole directories, because a bind needs an existing target and the ticker adds new files); feedparser (not in the image
# yet: python3-feedparser is a new Depends) is put on PYTHONPATH by a volatile drop-in. Nothing survives a reboot.
#
#   tools/ticker/deploy-board.sh HOST [PASSWORD]      (build the web UI first: cd webui && npx vite build)
#
# No graphical password prompt can ever appear: every call has SSH_ASKPASS_REQUIRE=never and uses sshpass.
set -eu
HOST=${1:?host}
PASS=${2:-unitotem}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
SSH_OPTS=(-o PreferredAuthentications=password -o PubkeyAuthentication=no -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR)
run() { env -u DISPLAY -u SSH_ASKPASS SSH_ASKPASS_REQUIRE=never sshpass -p "$PASS" "$@"; }
rsh() { run ssh "${SSH_OPTS[@]}" root@"$HOST" "$@"; }

mkdir -p "$STAGE/pylib"
PIP=${PIP:-"$ROOT/.venv/bin/pip"}
[ -x "$PIP" ] || PIP=/home/alessandro/Sviluppo/Python/unitotem-manager/.venv/bin/pip
"$PIP" install -q --target "$STAGE/pylib" feedparser
for d in api utils webview static templates routers; do cp -r "$ROOT/manager/$d" "$STAGE/$d"; done
cp "$ROOT/manager/main.py" "$STAGE/main.py"
cp -r "$ROOT/manager/plugins/unitotem_viewer" "$STAGE/unitotem_viewer"
cp -r "$ROOT/manager/plugins/unitotem_node" "$STAGE/unitotem_node"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +

# the OLD crawl (main thread moves the text every frame), bundled as a control for before/after measurements:
# tools/ticker/control/ticker-raf.js, served as /assets/ticker-control.js (never loaded by the product)
mkdir -p "$STAGE/ctl"
sed "s#'./ticker-core.js'#'$ROOT/webui/src/ticker-core.js'#" "$ROOT/tools/ticker/control/ticker-raf.js" > "$STAGE/ctl/ticker-raf.js"
"$ROOT/webui/node_modules/.bin/esbuild" "$STAGE/ctl/ticker-raf.js" --bundle --format=esm --log-level=error \
  --outfile="$STAGE/static/assets/ticker-control.js"
rm -rf "$STAGE/ctl"

# SHIM=1: the staged copy of boot-screen.html gets the page contract of the ticker (#area, #ticker-host and the CSS of
# tools/ticker/shim.css) until package A's page has it for real. Only the copy that goes to the board is patched.
if [ -n "${SHIM:-}" ]; then
python3 - "$STAGE/unitotem_viewer/static/boot-screen.html" "$ROOT/tools/ticker/shim.css" <<'PY'
import sys
path, css_path = sys.argv[1:3]
html = open(path).read()
css = open(css_path).read().replace('#panes', '#content-root').replace('#content-root { position: absolute;', '#content-root { width: auto; height: auto; position: absolute;')
assert '<main id="content-root">' in html and '</main>' in html and '</head>' in html
html = html.replace('</head>', '<style>\n' + css + '</style>\n</head>', 1)
html = html.replace('<main id="content-root">', '<div id="area" data-ticker="off" data-ticker-pos="bottom"><main id="content-root">', 1)
html = html.replace('</main>', '</main><div id="ticker-host"></div></div>', 1)
open(path, 'w').write(html)
PY
fi

rsh 'rm -rf /var/lib/unitotem/ticker-test.new && mkdir -p /var/lib/unitotem/ticker-test.new'
tar -C "$STAGE" -cf - . | rsh 'tar -C /var/lib/unitotem/ticker-test.new -xf -'
# the bind mounts are replaced in one go: umount what an earlier run mounted, swap the directory, mount again
rsh 'set -e
B=/usr/share/unitotem-manager
for t in $B/api $B/utils $B/webview $B/static $B/templates $B/routers $B/main.py $B/plugins/unitotem_viewer $B/plugins/unitotem_node; do umount "$t" 2>/dev/null || true; done
rm -rf /var/lib/unitotem/ticker-test && mv /var/lib/unitotem/ticker-test.new /var/lib/unitotem/ticker-test
T=/var/lib/unitotem/ticker-test
mount --bind $T/api $B/api
mount --bind $T/utils $B/utils
mount --bind $T/webview $B/webview
mount --bind $T/static $B/static
mount --bind $T/templates $B/templates
mount --bind $T/routers $B/routers
mount --bind $T/main.py $B/main.py
mount --bind $T/unitotem_viewer $B/plugins/unitotem_viewer
mount --bind $T/unitotem_node $B/plugins/unitotem_node
mkdir -p /run/systemd/system/unitotem-manager.service.d
printf "[Service]\nEnvironment=PYTHONPATH=$T/pylib\n" > /run/systemd/system/unitotem-manager.service.d/ticker-test.conf
systemctl daemon-reload
systemctl restart unitotem-manager
echo restarted'
