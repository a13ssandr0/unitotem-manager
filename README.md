# UniTotem Manager

Web management and scheduling application of [UniTotem](https://github.com/a13ssandr0/unitotem). It runs on Linux nodes and provides:

- The scheduler: playlists, assets, uploads and routing of playlists to viewer windows
- The web UI (Vue, served over HTTPS) and a websocket API
- The viewer: one fullscreen Chromium (CEF) window per screen
- Node services (network, audio, displays, power, timers, updates, terminal, screen view) as a plugin
- Remote pairing of nodes, users and capabilities, backup and restore

Functional specification: [docs/manager/overview.md](https://github.com/a13ssandr0/unitotem/blob/master/docs/manager/overview.md). Technical documentation: [docs/manager](https://github.com/a13ssandr0/unitotem/tree/master/docs/manager) in the parent repository.

## Packages

| Package | Content |
|---|---|
| `unitotem-manager` | Core: web server, websocket API, scheduler, users, uploads, backup |
| `unitotem-node` | Node plugin: network, audio, displays, power, timers, updates |
| `unitotem-viewer` | Viewer plugin: CEF windows, compositor and splash units |
| `unitotem` | Metapackage of the three above |
| `python3-cefpython3` | CEF 147 Python bindings |
| `plymouth-theme-unitotem` | Boot splash theme |

Build the packages with `tools/build-debs.sh` (needs Docker; output in `dist/`). Dependencies are listed in `debian/control`.

## Run for development

Build the web UI once:

```sh
cd webui
npm ci
npm run build
```

Create a scratch directory with a self-signed certificate (certificate then key in one file, as the package does):

```sh
mkdir -p dev
openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj "/CN=localhost" -keyout dev/key.pem -out dev/crt.pem
cat dev/crt.pem dev/key.pem > dev/unitotem.pem
```

Start the manager headless from the repository root, with unprivileged ports and every state file in `./dev/` (`unitotem.env` and `remote.json` are created on first start):

```sh
python3 manager/init.py --no_gui=true --port 8080 --port_secure 8443 \
  --assets_file ./dev/assets.json --users_file ./dev/users.json --remote_file ./dev/remote.json \
  --envfile ./dev/unitotem.env --certfile ./dev/unitotem.pem --keyfile ./dev/unitotem.pem
```

Run the unit tests (no hardware or root needed):

```sh
python -m pytest
```

The UI tests in `tools/` need a running node and headless Chrome. See [docs/manager/testing.md](https://github.com/a13ssandr0/unitotem/blob/master/docs/manager/testing.md).
