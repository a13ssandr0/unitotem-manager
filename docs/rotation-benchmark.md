# Rotating the content in the page or the output in the compositor (2026-10-29)

Question: a panel mounted turned is shown upright either by the page (`body[r]`, content orientation of the window) or by the
compositor (`wlr-randr --transform`). Does it cost anything, and what changes for the rest of the system?

Method: `tools/gfx-bench/rotation/` (README at the top of each script). Boards: Pi 4 A (192.168.0.254, 1920x1200), Pi 4 B
(192.168.1.173, 1080p), venus (Intel Iris Xe, 1080p). Test pages served from the board and shown in the real window through the
real playlist path; per configuration the content orientation is set through the manager's API and the transform with
`wlr-randr`. Configurations: A baseline, B1/B3 page turns 90/270, C90/C270 compositor turns 90/270. Three rounds
(7-8 workloads x 30 s; 3 workloads x 8 reloads x 10 s; crawl/video/medium shader/idle x 3 reloads x 20 s).

## Result: no measurable difference
Frames drawn per second, frames slower than 25 ms, dropped video frames, CPU of the viewer and of the compositor and GPU busy
are the same within noise for A, B and C on all three boards (last round, table below: `report.py`). Examples (Pi 4 B, mean of
3 runs): 1080p H.264 30 fps, 0 dropped, viewer 165-170 % in every configuration; crawl 60 fps, 60-61 % viewer, 7 % compositor;
venus: 4K60 H.264 and every WebGL load 60 fps in all configurations. No thermal throttling (`vcgencmd get_throttled` 0x0).
What does vary is NOT the rotation: on the Pi 4 the same page sometimes comes up in a "fast" mode (WebGL 41 -> 60 fps, a heavy
shader 1 -> 60 fps, a canvas 32 -> 61 fps) in any configuration, one reload in ~5; it follows the page load, not the transform.
A turned 1920x1200 board draws the same 1200x1920 pixels either way.

## What differs: the screen window (VNC) and everything that reads the screen
wayvnc captures what the compositor scans out. With the page turning the content, the VNC window shows the picture lying on its
side and in Control mode a click lands where the page is turned (clicking the top-left square of the picture reached the page at
x=0.911 for B3). With the compositor turning the output the VNC window is upright and the click reaches the same square
(0.089, 0.089) for 90, 270, 180 and flipped, on both Pis (`rotation-vnc.py`, headless Chrome, real mouse events, page-side click log).


### 192.168.0.254  (1920x1200 first row)

frames drawn per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 61.5 | 61.5 | 61.5 | 61.5 |
| video | 29.6 | 29.6 | 29.6 | 29.5 |
| webgl_mid | 3.1 | 3.2 | 3.2 | 3.2 |
| idle | 61.5 | 61.5 | 61.5 | 61.5 |

frames slower than 25 ms per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 0.0 | 0.0 | 0.0 | 0.0 |
| video | 0.0 | 0.0 | 0.0 | 0.0 |
| webgl_mid | 2.4 | 2.3 | 2.3 | 2.4 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

dropped video frames %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | - | - | - | - |
| video | 0.0 | 0.0 | 0.0 | 0.1 |
| webgl_mid | - | - | - | - |
| idle | - | - | - | - |

viewer CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 63.7 | 63.7 | 66.3 | 63.3 |
| video | 190.7 | 186.0 | 186.0 | 185.0 |
| webgl_mid | 17.0 | 17.0 | 17.0 | 17.0 |
| idle | 22.0 | 22.0 | 22.3 | 22.0 |

compositor CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 9.0 | 9.0 | 9.0 | 9.0 |
| video | 4.0 | 4.0 | 4.0 | 4.0 |
| webgl_mid | 0.3 | 0.0 | 0.0 | 0.3 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

busiest GPU engine %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 28.0 | 29.0 | 28.3 | 28.0 |
| video | 41.7 | 40.0 | 40.0 | 40.0 |
| webgl_mid | 99.0 | 99.0 | 99.0 | 99.0 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

### 192.168.1.128  (1920x1080 first row)

frames drawn per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 60.0 | 60.0 | 60.0 | 60.0 |
| video | 30.0 | 30.0 | 30.0 | 30.0 |
| webgl_mid | 60.0 | 60.0 | 60.0 | 60.0 |
| idle | 60.0 | 60.0 | 60.0 | 60.0 |

frames slower than 25 ms per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 0.0 | 0.0 | 0.0 | 0.0 |
| video | 0.0 | 0.0 | 0.0 | 0.0 |
| webgl_mid | 0.0 | 0.0 | 0.0 | 0.0 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

dropped video frames %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | - | - | - | - |
| video | 0.0 | 0.0 | 0.0 | 0.0 |
| webgl_mid | - | - | - | - |
| idle | - | - | - | - |

viewer CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 35.7 | 35.0 | 33.7 | 34.0 |
| video | 36.0 | 36.0 | 36.7 | 37.0 |
| webgl_mid | 39.3 | 38.0 | 37.0 | 37.7 |
| idle | 16.7 | 16.7 | 16.7 | 16.7 |

compositor CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 5.0 | 5.0 | 6.0 | 6.0 |
| video | 2.3 | 2.0 | 3.0 | 2.7 |
| webgl_mid | 5.0 | 5.0 | 5.0 | 5.7 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

busiest GPU engine %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 1.0 | 1.0 | 1.0 | 1.0 |
| video | 5.7 | 6.0 | 4.3 | 4.3 |
| webgl_mid | 20.0 | 19.3 | 18.0 | 18.0 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

### 192.168.1.173  (1920x1080 first row)

frames drawn per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 60.0 | 60.0 | 60.0 | 60.0 |
| video | 29.8 | 29.9 | 29.9 | 30.0 |
| webgl_mid | 3.4 | 3.4 | 3.5 | 3.5 |
| idle | 60.0 | 60.0 | 60.0 | 60.0 |

frames slower than 25 ms per second

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 0.0 | 0.0 | 0.0 | 0.0 |
| video | 0.0 | 0.0 | 0.0 | 0.0 |
| webgl_mid | 2.5 | 2.6 | 2.5 | 2.6 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

dropped video frames %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | - | - | - | - |
| video | 0.0 | 0.0 | 0.0 | 0.0 |
| webgl_mid | - | - | - | - |
| idle | - | - | - | - |

viewer CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 64.3 | 60.7 | 62.7 | 61.0 |
| video | 169.0 | 167.3 | 167.0 | 167.7 |
| webgl_mid | 17.0 | 17.0 | 16.7 | 16.7 |
| idle | 21.7 | 21.0 | 21.7 | 22.0 |

compositor CPU % of one core

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 7.3 | 7.0 | 7.3 | 7.0 |
| video | 4.0 | 4.0 | 4.0 | 4.0 |
| webgl_mid | 0.3 | 0.0 | 0.0 | 0.3 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |

busiest GPU engine %

| workload | A | B1 | C90 | C270 |
|---|---|---|---|---|
| crawl | 26.0 | 25.3 | 25.0 | 25.0 |
| video | 36.0 | 35.3 | 35.7 | 35.3 |
| webgl_mid | 99.0 | 99.0 | 99.0 | 99.0 |
| idle | 0.0 | 0.0 | 0.0 | 0.0 |
