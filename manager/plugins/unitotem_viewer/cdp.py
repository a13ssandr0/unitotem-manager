"""
The viewer's own connection to its browser through the DevTools protocol (the port CEF is started with).

Two things cannot be done any other way. The page of a window shows its content in an iframe that is usually from
another origin, so nothing in the viewer's own page can see what happens inside it: a script is injected into every
frame (and every cross-origin frame, which the browser puts in a process of its own and exposes as a separate target) to
tell when an editable field gets or loses the focus. And text typed on an on-screen keyboard has to reach whatever has
the focus, in any frame: `Input.insertText` and `Input.dispatchKeyEvent` sent to the page do exactly that.

One more job: what the browser reports as wrong (a document that cannot be loaded, a page that refuses to be framed, a security
error such as mixed content, an exception in the viewer's own page) is raised as a notification, so it reaches the web UI
instead of dying in a log nobody reads.

Runs on the asyncio loop of the viewer's websocket thread. Reconnects by itself: the browser opens the port some time
after it starts.
"""
import asyncio
import itertools
import json
import re
import urllib.request
from typing import Awaitable, Callable, Optional

from loguru import logger
from utils.notifications import notifications
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

# Reports, from any frame, whether the focus is on something that takes text. Nothing here may throw into the page.
# Every frame reports with an identity of its own: the frames of one process (all the same-origin ones, which with several
# panes are many) arrive over the same DevTools session, and the focus can move from a field in one to a field in another.
# A frame that goes away (an iframe navigated, a pane closed) says it no longer has the focus, which nothing else can tell.
FOCUS_SCRIPT = r"""
(() => {
  if (window.__utFocusWatch) return;
  window.__utFocusWatch = true;
  const me = Math.random().toString(36).slice(2);
  const NO_TEXT = ['button', 'checkbox', 'radio', 'submit', 'reset', 'file', 'image', 'range', 'color', 'hidden'];
  const editable = el => !!el && !el.disabled && !el.readOnly && (el.isContentEditable || el.tagName === 'TEXTAREA' ||
    (el.tagName === 'INPUT' && !NO_TEXT.includes((el.type || 'text').toLowerCase())));
  const report = () => {
    try {
      // pages built from web components (Home Assistant) keep the real field inside shadow roots
      let el = document.activeElement;
      while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
      utFocus(JSON.stringify({on: editable(el), type: editable(el) ? (el.type || el.tagName.toLowerCase()) : null, frame: me}));
    } catch (e) {}
  };
  document.addEventListener('focusin', report, true);
  document.addEventListener('focusout', () => setTimeout(report, 0), true);
  window.addEventListener('pagehide', () => { try { utFocus(JSON.stringify({on: false, type: null, frame: me})); } catch (e) {} });
})();
"""

# Reports, from any frame, that somebody is using the window: pointer, touch, wheel and key events, at most one report
# per second per frame (a leading one, and a trailing one if there was more). Nothing here may throw into the page.
ACTIVITY_SCRIPT = r"""
(() => {
  if (window.__utActivityWatch) return;
  window.__utActivityWatch = true;
  let last = 0, timer = null;
  const send = () => { last = Date.now(); try { utActivity('{}'); } catch (e) {} };
  const hit = () => {
    const wait = 1000 - (Date.now() - last);
    if (wait <= 0) return send();
    if (!timer) timer = setTimeout(() => { timer = null; send(); }, wait);
  };
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'touchstart', 'touchmove', 'mousedown', 'mousemove',
                      'wheel', 'keydown'])
    window.addEventListener(type, hit, {capture: true, passive: true});
})();
"""

# What a special key is for the browser: (key, code, windowsVirtualKeyCode, text)
SPECIAL_KEYS = {
    'Backspace': ('Backspace', 'Backspace', 8, None),
    'Tab': ('Tab', 'Tab', 9, None),
    'Enter': ('Enter', 'Enter', 13, '\r'),
    'Escape': ('Escape', 'Escape', 27, None),
    'Delete': ('Delete', 'Delete', 46, None),
    'Home': ('Home', 'Home', 36, None),
    'End': ('End', 'End', 35, None),
    'ArrowLeft': ('ArrowLeft', 'ArrowLeft', 37, None),
    'ArrowUp': ('ArrowUp', 'ArrowUp', 38, None),
    'ArrowRight': ('ArrowRight', 'ArrowRight', 39, None),
    'ArrowDown': ('ArrowDown', 'ArrowDown', 40, None),
}

_WINDOW_RE = re.compile(r'[?&]window=(\d+)')

# (window, frame, on, kind): `frame` tells the frames apart, the focus can move from a field in one to a field in another
FocusCallback = Callable[[int, str, bool, Optional[str]], None]


class Cdp:
    def __init__(self, port: int, on_focus: FocusCallback, on_page_ready: Optional[Callable[[int], None]] = None):
        self._port = port
        self._on_focus = on_focus
        self._on_page_ready = on_page_ready
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._ws = None
        self._sessions: dict[str, dict] = {}        # sessionId -> {'type', 'window', 'page'}
        self._pages: dict[int, str] = {}            # window id -> session of its top-level page
        self._attached_targets: set[str] = set()    # pages we are attached to or about to be: one session each
        self._documents: dict[tuple[str, str], str] = {}    # (session, requestId) -> URL of a document being loaded
        self._handlers: dict[str, Callable[[int, dict], Awaitable[None] | None]] = {}

    def on_binding(self, name: str, handler):
        """`handler(window_id, payload)` is called when the page calls the function `name` (only top-level pages)."""
        self._handlers[name] = handler

    # ── connection ────────────────────────────────────────────────────────

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                url = await asyncio.to_thread(self._browser_url)
                async with connect(url, max_size=None, open_timeout=5) as ws:
                    self._ws = ws
                    reader = asyncio.create_task(self._read(ws))
                    await self._send('Target.setDiscoverTargets', {'discover': True})
                    await reader
            except (OSError, ConnectionClosed, asyncio.TimeoutError):
                pass            # the browser is not up yet, or went away: try again
            except Exception:
                logger.exception('DevTools connection failed')
            finally:
                self._ws = None
                self._sessions.clear()
                self._pages.clear()
                self._attached_targets.clear()
                self._documents.clear()
                for future in self._pending.values():
                    future.cancel()
                self._pending.clear()
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    def _browser_url(self) -> str:
        with urllib.request.urlopen(f'http://127.0.0.1:{self._port}/json/version', timeout=2) as response:
            return json.load(response)['webSocketDebuggerUrl']

    async def _send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None):
        ws = self._ws
        if ws is None:
            raise ConnectionError('not connected')
        message_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        message = {'id': message_id, 'method': method, 'params': params or {}}
        if session:
            message['sessionId'] = session
        await ws.send(json.dumps(message))
        return await future

    async def _read(self, ws):
        async for raw in ws:
            message = json.loads(raw)
            if 'id' in message:
                future = self._pending.pop(message['id'], None)
                if future and not future.done():
                    if 'error' in message:
                        future.set_exception(RuntimeError(message['error'].get('message', 'DevTools error')))
                    else:
                        future.set_result(message.get('result', {}))
            else:
                # events are handled in their own task: a handler may itself wait for a reply, which only this loop reads
                asyncio.create_task(self._event(message))

    # ── events ────────────────────────────────────────────────────────────

    async def _event(self, message: dict):
        try:
            method, params, session = message['method'], message.get('params', {}), message.get('sessionId')
            if method in ('Target.targetCreated', 'Target.targetInfoChanged'):
                info = params['targetInfo']
                if info['type'] == 'page' and info['targetId'] not in self._attached_targets:
                    self._attached_targets.add(info['targetId'])
                    await self._send('Target.attachToTarget', {'targetId': info['targetId'], 'flatten': True})
            elif method == 'Target.attachedToTarget':
                await self._attached(params['sessionId'], params['targetInfo'], parent=session)
            elif method == 'Target.detachedFromTarget':
                self._forget(params['sessionId'])
            elif method == 'Runtime.bindingCalled':
                await self._binding(session, params['name'], params['payload'])
            elif method == 'Network.requestWillBeSent':
                if params.get('type') == 'Document' and session:
                    self._documents[(session, params['requestId'])] = params.get('request', {}).get('url', '')
            elif method == 'Network.loadingFailed':
                self._load_failed(session, params)
            elif method == 'Log.entryAdded':
                self._log_entry(session, params.get('entry', {}))
            elif method == 'Runtime.exceptionThrown':
                self._exception(session, params.get('exceptionDetails', {}))
        except Exception:
            logger.exception('DevTools event failed: {}', message.get('method'))

    async def _attached(self, session: str, info: dict, parent: Optional[str]):
        if info['type'] in ('page', 'iframe'):
            await self._set_up(session, info, parent)

    async def _set_up(self, session: str, info: dict, parent: Optional[str]):
        kind = info['type']
        page = session if kind == 'page' else (self._sessions.get(parent, {}).get('page') if parent else None)
        window = None
        if kind == 'page':
            match = _WINDOW_RE.search(info.get('url', ''))
            if not match:
                return          # a page that is not one of ours (the DevTools front end, an about:blank)
            window = int(match.group(1))
            self._pages[window] = session
        self._sessions[session] = {'type': kind, 'window': window, 'page': page, 'target': info['targetId']}
        await self._send('Runtime.enable', session=session)
        await self._send('Runtime.addBinding', {'name': 'utFocus'}, session)
        await self._send('Runtime.addBinding', {'name': 'utActivity'}, session)
        if kind == 'page':
            await self._send('Runtime.addBinding', {'name': 'utKey'}, session)
            await self._send('Runtime.addBinding', {'name': 'utFrame'}, session)
            await self._send('Runtime.addBinding', {'name': 'utSaver'}, session)
        await self._send('Page.enable', session=session)
        await self._send('Network.enable', session=session)
        await self._send('Log.enable', session=session)
        await self._send('Page.addScriptToEvaluateOnNewDocument', {'source': FOCUS_SCRIPT + ACTIVITY_SCRIPT}, session)
        await self._send('Runtime.evaluate', {'expression': FOCUS_SCRIPT + ACTIVITY_SCRIPT}, session)
        # frames of other origins appear as targets of their own
        await self._send('Target.setAutoAttach',
                         {'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}, session)
        if kind == 'page' and self._on_page_ready:
            self._on_page_ready(window)

    def _forget(self, session: str):
        gone = self._sessions.pop(session, None)
        if gone:
            self._attached_targets.discard(gone['target'])
        if gone and gone['window'] is not None and self._pages.get(gone['window']) == session:
            del self._pages[gone['window']]

    # ── framing and errors ────────────────────────────────────────────────

    def _window_of(self, session: Optional[str]) -> Optional[int]:
        info = self._sessions.get(session or '')
        page = info['page'] if info else None
        return self._sessions.get(page, {}).get('window') if page else None

    def _load_failed(self, session: Optional[str], params: dict):
        url = self._documents.pop((session or '', params.get('requestId', '')), '')
        window = self._window_of(session)
        if params.get('type') != 'Document' or params.get('canceled') or window is None:
            return
        if not url.startswith(('http://', 'https://', 'ftp://')):
            return
        reason = params.get('errorText', 'unknown error')
        if 'BLOCKED_BY_RESPONSE' in reason:
            reason += ' (the page forbids being shown inside another page, see the other notification)'
        logger.warning('Window {}: {} could not be loaded: {}', window, url, reason)
        notifications.notify(f'viewer.load.{window}', 'warning', f'Window {window}: a page cannot be shown',
                             f'{url}\n{reason}', source='viewer', audience='scheduler')

    def _log_entry(self, session: Optional[str], entry: dict):
        # only what the browser itself calls a security problem (mixed content, CORS, CSP) or a refusal to display a frame: javascript
        # and network noise from the pages the totem shows is not the operator's to fix
        window = self._window_of(session)
        text = entry.get('text', '')
        if entry.get('level') != 'error' or window is None or not (entry.get('source') == 'security' or text.startswith('Refused to')):
            return
        if 'X-Frame-Options' in text or 'frame-ancestors' in text:
            text += ('\nThe page forbids being shown inside another page. The site has to allow it '
                     '(Home Assistant: `http: use_x_frame_options: false` in configuration.yaml).')
        logger.warning('Window {}: {}', window, text)
        notifications.notify(f'viewer.security.{window}', 'warning', f'Window {window}: the browser refused something',
                             text, source='viewer', audience='scheduler')

    def _exception(self, session: Optional[str], details: dict):
        info = self._sessions.get(session or '')
        window = info['window'] if info and info['type'] == 'page' else None
        if window is None or not str(details.get('url', '')).startswith('file://'):
            return              # an exception in somebody else's page is not ours (with site isolation off it arrives here too)
        text = (details.get('exception') or {}).get('description') or details.get('text', 'exception')
        logger.warning('Window {}: exception in the viewer page: {}', window, text)
        notifications.notify(f'viewer.page.{window}', 'error', f'Window {window}: error in the viewer page',
                             f"{text}\n{details.get('url', '')}".strip(), source='viewer', audience='admin')

    async def _binding(self, session: Optional[str], name: str, payload: str):
        info = self._sessions.get(session or '')
        if info is None:
            return
        page = info['page']
        window = self._sessions.get(page, {}).get('window') if page else None
        if window is None:
            return
        data = json.loads(payload)
        if name == 'utFocus':
            self._on_focus(window, f"{session}/{data.get('frame', '')}", bool(data.get('on')), data.get('type'))
        elif name in ('utKey', 'utSaver', 'utActivity', 'utFrame') and (name == 'utActivity' or info['type'] == 'page'):
            # utActivity comes from every frame, the others from the page of the window only
            handler = self._handlers.get(name)
            if handler:
                result = handler(window, data)
                if asyncio.iscoroutine(result):
                    await result

    # ── what the viewer asks for ──────────────────────────────────────────

    async def gpu_info(self) -> dict:
        """Chromium's own report of its graphics (SystemInfo.getInfo on the browser): feature status and GL implementation."""
        return (await self._send('SystemInfo.getInfo')).get('gpu', {})

    async def evaluate(self, window: int, expression: str):
        session = self._pages.get(window)
        if session:
            await self._send('Runtime.evaluate', {'expression': expression}, session)

    async def call(self, window: int, expression: str):
        """Evaluates in the page of a window and returns the value (None if there is no such page or it threw)."""
        session = self._pages.get(window)
        if not session:
            return None
        result = await self._send('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)
        return result.get('result', {}).get('value')

    async def insert_text(self, window: int, text: str):
        session = self._pages.get(window)
        if session and text:
            await self._send('Input.insertText', {'text': text}, session)

    async def press(self, window: int, key: str):
        session = self._pages.get(window)
        special = SPECIAL_KEYS.get(key)
        if not session or special is None:
            return
        name, code, vk, text = special
        down = {'type': 'keyDown' if text else 'rawKeyDown', 'key': name, 'code': code,
                'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk}
        if text:
            down['text'] = text
        await self._send('Input.dispatchKeyEvent', down, session)
        await self._send('Input.dispatchKeyEvent', {**down, 'type': 'keyUp', 'text': ''}, session)
