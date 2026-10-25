# Window layout (area, panes) and the RSS ticker: design

Requested by the user on 2026-10-25. Everything below is configured from the web UI only. Names in `code` are the contract between the
two work packages (A: layout, B: ticker); a package may add to the contract but must not change it without telling the lead.

## What a window is made of

```
window (one per output, full screen, rotated/flipped as today)
 +- backdrop (black, or `margin_color`)            <- the "blank spaces"
 +- #area  (a rectangle of the window: `area`)     <- window.area, percent of the window in CONTENT space (after orientation)
 |    +- #panes   (the split tree, like Konsole)   <- window.tree, each leaf = one Stage = one playlist
 |    +- #ticker-host (RSS bar + clock)            <- window.ticker, anchored to the top or bottom edge of #area
 +- on-screen keyboard (keyboard.js)               <- covers the WHOLE window, unchanged
 +- screensaver overlay                            <- covers the WHOLE window (black behind the layout), unchanged semantics
```

Why `area`: the 55" vertical touch panel (192.168.1.8) is too tall and too low to use, so its view used to be reduced to about a third of the
height and centred by the window geometry set in the old xinit script. Under Wayland the window is fullscreen and the compositor owns its
geometry, so the content rectangle is a property of the page, set from the web UI.

## Persistence and permissions

`window_layouts.json` beside `viewer_settings.json` (`utils/models/layout.py`), keyed like `viewer_settings.keyboards`: `"<viewer id>|<screen name>"`
(the output name survives unplug/replug, the window id does not). A BackupPart `layouts` (core) exports/restores/resets it; the RSS sources
(`ticker_sources.json`) are a part `ticker` (core). Permissions: the layout (`area`, `tree`) and the ticker are PANEL CONFIGURATION: **admin**; what a pane
shows (assign/unassign playlist) stays **scheduler**.

```python
class Area(BaseModel):            # percent of the window, content space; clamped, never refused: a screen must never become unusable
    left: float = 0; top: float = 0; width: float = 100; height: float = 100     # min size 5 %, inside 0..100
    margin_color: str = 'rgb(0,0,0)'

class Leaf(BaseModel):  kind: Literal['leaf'] = 'leaf'; id: int                  # ids are never reused inside a window (monotonic `next_id`)
class Split(BaseModel): kind: Literal['split'] = 'split'; dir: Literal['h','v']  # 'h' = side by side (a | b), 'v' = stacked (a over b)
                        ratio: float = 0.5                                       # share of `a`, 0.1..0.9
                        a: Node; b: Node
class WindowLayout(BaseModel): area: Area; tree: Node = Leaf(id=1); next_id: int = 2; ticker: TickerSettings   # max 8 leaves, depth 4
```

The first leaf in tree order is the window's MAIN pane: the existing `assign_playlist(viewer_id, window_id, playlist_id)` (no pane) keeps meaning "the main pane",
so old clients and the stored assignments keep working. Other panes' playlists are stored in the layout (`panes: dict[int, playlist_id]`).
The sound output stays per window (all panes), a video's own volume stays per asset.

## Messages manager -> viewer page (all through `PageCommands` in `window.py` and `_handle_command` in `app.py`)

- `Show` gains an optional `pane` (default = main pane). `SetAssetAudio` too.
- `SetWindowLayout {window_id, area, tree}` (package A): the page builds/updates one Stage per leaf (a Stage = what `#stage` is today: boot/web/image/video/audio
  containers + zoom + audio + refresh), keeps running media of panes that survive a re-layout, destroys the others. Sent at window creation/restore and on every change.
- `SetTicker {window_id, ...TickerSettings}` and `TickerItems {window_id, items}` (package B), pushed by events only: when the settings change, when a fetch changes the
  merged list, and when a window appears (same payload as at initialisation). The page never polls the manager.

## The ticker (package B owns all of it)

Core, runs on a plain server: `utils/ticker/` (sources, fetcher with `feedparser` or the stdlib, merged list, per-source interval), `utils/models/ticker.py`
(`TickerSettings` of a window, `TickerSource`), `api/ticker.py` (`Ticker/*`, admin), part `ticker`. The page side is `webui/src/ticker.js` (a module loaded on demand like
`keyboard.js`, publishes `window.utTicker`; the page calls `utTicker.update(cfg, items)` (settings and list together; `configure`/`setItems` exist for tests)), UI in `webui/src/components/`.

- Sources: URL, name, `interval` seconds (clamped 60 s .. 1 day, default 15 min), enabled. Fetch with `If-None-Match`/`If-Modified-Since`, timeout, size cap, redirect cap,
  only http/https, no private-address special cases needed beyond that; one failing source never stops the others (notification once per source via `utils/notifications.py`,
  resolved when it works again). The merged list is deduplicated (guid/link), sorted newest first, capped (500 items), kept in memory + a small cache file in `/var/lib/unitotem`
  (not in the backup). Items are `{time (epoch), source, title, summary?, link}`.
- Selection, evaluated by the PAGE on every cycle with the device clock (items age out): `select: {mode: 'count', count: N} | {mode: 'today'} | {mode: 'range', seconds: S}`
  (UI: last 2 hours, last 2 days, ... presets plus a custom value).
- Template: **Rich/Textual console markup** (`[bold red]..[/]`, `[on blue]`, `[#ff8800]`, `[rgb(1,2,3)]`, `[dim]`, `[italic]`, `[underline]`, `[strike]`, `[/bold]` closes, `\[` escapes,
  colour names as in Rich/CSS) plus f-string style fields: `{time:HH:mm}`, `{title}`, `{source}`, `{summary}`, with a Python-like format spec for text (`{title:.80}` truncates,
  `{source:>12}`, `{title!u}` upper/`!l` lower/`!t` title). Time tokens follow LDML (`HH` hour 00-23, `hh` 01-12, `mm` minutes, `ss`, `a` am/pm, `dd`, `MM` month number, `MMM`
  `MMMM` month names, `EEE` `EEEE` weekday, `yyyy` `yy`), formatted in the browser locale of the window (`Intl`), so "hh:MM - text" style users get `{time:HH:mm} - {title}`.
  UI presets (with a live preview): `[b]{time:HH:mm}[/b] - {title}`, `{time:dd MMM} - {title}`, `{time:dd/MM HH:mm} [dim]{source}[/dim] {title}`, `[yellow]{source:.12}[/] {title}`,
  `{time:EEE HH:mm}  {title}`, plain `{title}`. Invalid markup never breaks the bar: it falls back to plain text and the UI shows the error.
- Display options (per window): `enabled`, `position` top|bottom of the area, `mode` overlay|reserve (reserve shrinks the panes), `rows` 1|2 (two lanes crawling in
  parallel, items alternate between lanes), `font_size` (% of the window's smaller side, or px), `color`, `background` (with alpha), `speed`, `separator`.
- Speed: units are font-size relative (em per second) so it reads the same on every resolution; the default comes from research (broadcast crawls, digital signage defaults 50-80 px/s
  at 1080p with 24-32 px text, i.e. roughly 2-3 em/s): package B must search the web for the best default and range and justify it in the code comment; adjustable by the user.
- Clock block: `clock: {enabled, position: 'start'|'end', seconds: bool, date: bool, rows: 1|2}` fixed at the start or end of the bar, not scrolling.
- Scrolling is done by the COMPOSITOR (user requirement, 2026-10-25: "the headlines jumping is really serious"; a crawl positioned by JavaScript every frame judders by construction: main thread, stepped,
  rounded to whole pixels). Each lane is a recycler of PAGES: a page is one element with consecutive headlines (2-3 bar widths, at most 8192 px) and ONE linear `transform` Web Animation (`will-change:
  transform`, fractional px, nothing else animated); the next page is built ahead (three pages per lane) and gets an exact `startTime` on `document.timeline` (start of the previous + the time it needs to
  move by its own width), so there is no gap and the speed is the same on every page; a page that has left returns to a pool. 500 headlines cost what 5 do; a change of the list never touches the pages
  already built (only pages not built yet use it), so nothing jumps; a busy main thread cannot make the crawl miss a frame (measured, see below). The clock block is static text. A rebuild (new settings,
  resize, rotation) restarts the crawl, pre-filled. Measurements (venus, GPU compositing, 1080p, `tools/ticker/venus-run.py --only trace`): see CLAUDE.md "RSS ticker" and the table below.

## Page contract (package A restructures boot-screen.html, package B only plugs in)

`#area` contains `#panes` and `#ticker-host`. A sets the attributes `#area[data-ticker="off|overlay|reserve"]` and `[data-ticker-pos="top|bottom"]` and writes the CSS that
places `#ticker-host` accordingly; B's `ticker.js` sets the CSS variable `--ticker-h` (px) on `#area` and draws inside `#ticker-host`. Nothing outside `#area` is touched by B.

## Testing, boards

Unit tests for every pure part (models/validation, tree operations, Rich-markup + field parser, time formatting, selection, fetcher with local test feeds, merge/dedup).
Real runs: A on the Pi (192.168.1.173, 1080p on the HDMI capture card, CEF DevTools `Page.captureScreenshot` gives the real pixels), B on venus (192.168.1.128).
Procedure for code on a board without building a deb: CLAUDE.md "Testing code on an image board". Never `pkill -f` inside an ssh line, no graphical password prompts
(env -u DISPLAY -u SSH_ASKPASS SSH_ASKPASS_REQUIRE=never sshpass ... as in CLAUDE.md). One package per board at a time.
