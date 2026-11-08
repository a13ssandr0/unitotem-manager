# UniTotem Manager

Web management and scheduling application of [UniTotem](https://github.com/a13ssandr0/unitotem). It runs on Linux nodes and provides:

- The scheduler: playlists, assets, uploads and routing of playlists to viewer windows
- The web UI (Vue, served over HTTPS) and a websocket API
- The viewer: one fullscreen Chromium (CEF) window per screen
- Node services (network, audio, displays, power, timers, updates, terminal, screen view) as a plugin
- Remote pairing of nodes, users and capabilities, backup and restore

Functional specification: [docs/manager/overview.md](https://github.com/a13ssandr0/unitotem/blob/master/docs/manager/overview.md). Technical documentation: [docs/manager](https://github.com/a13ssandr0/unitotem/tree/master/docs/manager) in the parent repository.

## Packages

| Package | Arch | Content |
|---|---|---|
| `unitotem` | all | Metapackage: the three packages below and the splash theme |
| `unitotem-manager` | all | Core: web server, websocket API, scheduler, users, uploads, backup |
| `unitotem-node` | all | Node plugin: network, audio, displays, power, timers, updates |
| `unitotem-viewer` | all | Viewer plugin: CEF windows, compositor and splash units |
| `plymouth-theme-unitotem` | all | Boot splash theme (recommended by `unitotem`) |
| `python3-cefpython3` | amd64, arm64 | CEF 147 Python bindings |
| `python3-fastapi-login`, `python3-sdbus-networkmanager` | all | Third-party Python libraries, not in Debian (`packaging/`) |

Build the packages with `tools/build-debs.sh` (needs Docker; output in `dist/`). Dependencies are listed in `debian/control`.

## Install

Debian 13 (trixie), amd64 or arm64. Add the apt repository (key and source list, as the image does):

```sh
sudo curl -fsSL https://a13ssandr0.github.io/unitotem-manager/KEY.gpg -o /etc/apt/trusted.gpg.d/unitotem-manager.asc
sudo curl -fsSL https://a13ssandr0.github.io/unitotem-manager/unitotem-manager.list -o /etc/apt/sources.list.d/unitotem-manager.list
sudo apt-get update
sudo apt-get install unitotem
```

`unitotem` installs a totem: manager, node integration, viewer and splash. A server that only orchestrates other nodes takes `unitotem-manager` alone; it touches nothing of the machine it runs on.

After the first start, open `https://<address>/setup` and create the first administrator account.

## Update

```sh
sudo apt-get update
sudo apt-get install unitotem
```

The packages depend on the exact same version of each other (`= ${source:Version}`), so they upgrade together. `apt-get purge` also removes the certificate `/etc/ssl/unitotem.pem` and the state in `/etc/unitotem` and `/var/lib/unitotem`.

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
