You are working on **UniTotem Manager** a kiosk program for linux, from now on called simply unitotem.

Unitotem is made of three key components working together:
- scheduler: the key part of the program, handles assets scheduling from one or more playlists
- management ui+backend: a user friendly and simple but powerful web ui that controls all the settings through a 
websocket-based API
- viewer: a chromium based fullscreen frameless window with a custom page that can handle several file formats

## Scheduler
The core of unitotem is the scheduler, it allows both local files and content urls.
Local files are uploaded through the web ui and are referenced through a special prefix `file:` resembling an url scheme,
it only references files directly in the uploads folder, does not allow subfolders and most importantly upper folders.

There may be multiple instances of the scheduler, exactly one for each playlist created, each scheduler broadcasts
when to change asset to the corresponding local windows and to all connected clients, a single scheduler might 
control multiple windows both local and remote.

Each instance of the scheduler must be able to set a default asset duration that applies only assets withouh an intrinsic
duration (yes to web pages and images, no to videos and audios).

Each instance of the scheduler must be able to select the output audio device and volume based on a per-window policy, if a 
scheduler control multiple windows both locally and remotely each window must be able to send its audio to the specified 
device, a 'default' selector must be included to let the system decide.
Volume control (included mute) must be provided per-asset (only for assets supporting it), this is independent from
system level volume control.

Each asset supports the following additional properties:
- `fit`: how the asset fills the viewer area — contain (default), cover, fill (CSS object-fit semantics)
- `bg_color`: background color shown behind the asset (e.g. for images with transparency or letterboxed video)
- `ena_date` / `dis_date`: optional datetime to schedule automatic enable and disable of the asset

The uploads manager provides a download button for individual uploaded files.

## Management
Each device running unitotem (also called `node`) provides a web page via https (http port 80 is open and redirects to
port 443). The web page is written in vue an uses websockets to connect to the backend bidirectionally.

In the top bar of the page the Unitotem logo followed by the hostname of the device, on the right theme switcher and
reboot/shutdown/logout controls.
A dropdown menu with optional badge should contain system notifications.

### Login
When connecting to the device running unitotem, a login page asks for username and password and allows saving login
for a week. The login page shows device hostname, ip and OS version to immediately check if we are connected to the right
device.

### Scheduler
Root page of the program. Made of two main parts: the playlists manager and the uploads manager.
The playlists manager has a selector to choose the playlist to edit and a button to add a new playlist.
Contains a table that shows info about the assets and provides access to quick controls, assset edit and supports drag
and drop reordering.
The uploads manager shows uploaded files with their names, sizes and optionally duration, provides checkboxes for multiple
selections and controls to delete, download or add the file(s) to the playlist.

### Viewers
A dedicated page (route `/viewers`) that shows all connected viewer windows grouped by host and allows assigning a
playlist to each window. This is how the output of a scheduler instance is routed to specific displays.

### Volume control
Global device volume must be controlled too, previously we discussed asset and window volumes, now we are talking about
the audio device itself, this control requires a different ui from the scheduler volumes and must allow selecting default
output device.

### Remote control
Manages client/server modes. By default, out-of-the-box, it must be in server mode (standalone mode is a subset of server
mode without any client attached, each standalone node can become a server at any time).
In server mode the configuration page must display the list of connected and known clients (known clients are paired
clients not currently connected, maybe because offline) and provide buttons to enable/disable clients temporarily,
to unpair the client and to open its configuration page.

In client mode, an unpaired client broadcasts a special packet that allows servers to discover it, through the web ui
servers must highlight discovered clients and propose adoption, meanwhile both clients and servers must provide a 
manual way of start pairing by requesting the user to enter the ip of the other party and optionally the port.
Adoption must require approval of the other party, except for unpaired clients (similar to ubiquiti device adoption).
Adopting a client with preconfigured playlists requires moving playlists and uploads from client to server during pairing and restoring
the connection between the playlists and the client it came from; unpairing a client requires transfering playlists and uploads
to the soon-to-be standalone node, playlists and uploads previously referenced ONLY from unpaired device must be removed
after transfer, playlists and uploads referenced also by other nodes must remain untouched.

Client and servers connect through a websocket over https and mutual authentication must be implemented through 
certificates exchanged during pairing phase and surviving through the entire life of the installation. Certificates
must be part of the backup and must prevent the confidentiality and integrity of the websocket "conversation":
intermediate proxies performing ssl strip to spy on the traffic from the server or trying to display unwanted assets on 
the client will provide a different certificate, connections with unkwnown certificates must be immediately rejected.

### Security
Must provide user and capabilities management. Each user can be assigned one or more of the following capabilities:
- scheduler (control asset scheduling (including per-asset volume) and file upload)
- audio (allows changing screen volume and audio device volume) — referred to as "volume" in earlier specs
- power allows rebooting/powering off the node
- admin (implies all of the above and allows changing every other settings)

There must be at least one admin account in the system and no "suicidal" actions should be allowed:
- no user can delete itself
- no admin can remove their own admin role (unconditionally, even if other admins exist)

Each user must be able to change its password.

### Network
Network management must be allowed through a page that replicates KDE network manager gui and controls
NetworkManager via DBus ensuring netplan is used as backend (renderer: NetworkManager in netplan config).
Hostname change is performed via the `org.freedesktop.hostname1` (systemd-hostnamed) DBus service, which also
keeps `/etc/hostname` in sync; `/etc/hosts` (the `127.0.1.1` entry) must be updated accordingly.

### Display
Must control orientation and flip of connected displays, an overview of the current monitor layout (position and rotation)
must be provided at the top of the view, each screen will have its independent viewer window so for each
window there must be controls to change content orientation (like screen rotation but without affecting it) and to change
content flip (ex: for backwards projection).

### Timers
Based on user crontab allows setting reboot/poweroff timers.

### Updates
A simple frontend for apt-get update and apt-get dist-upgrade -y.
A periodic task should run in background every day to check for updates and display an hint in the navbar in the webui.
The webui must provide a xterm.js console to show update/upgrade logs. Logs of the last run must be cached for the entire
lifecycle of the program, its ok to discard them as soon as another update/upgrade task is run or if the program exits

### Backup/Restore
Must provide and easy way to backup, restore and reset unitotem configuration.
Each one of the controls must allow selection of the parts to backup/restore/reset.
Certificates for client/server authentication must be saved/restored if that part of the configuration is selected to
be backed-up/restored.

### Info
Displays system metrics collected from many sensors:
- System uptime
- CPU usage
- RAM usage
- Disk partitions and usage
- Temperature sensors (if available)
- Fans (if available)
- Batteries (if available)

This data must be broadcast every 3 second to every logged-in user.


## Viewer
Exactly one per-screen, the program must detect at all times the number of screens connected and add/remove windows
accordingly, if a new screen is connected it must be uniquely identified, registered in the system and a window
must be associated to it but no playlist should be connected to that window automatically.
If a screen is removed the corresponding window must be closed, the playlist outputting to that window must be unaware of 
the change.


# Running
Running the program must allow the following modes:
- standalone
- headless (for servers running on independent machines)

At startup program must check for configuration files presence and for internet connection:
if no configuration file is present this is the first run after installation/reset, shows welcome screen,
if internet is also not available starts a hotspot and displays SSID and password on the welcome screen together with a
QR code, if possible configuration page should be advertised as captive portal 