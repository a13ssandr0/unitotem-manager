#!/usr/bin/env python3
"""
Test of the notification center in headless Chrome: the bell and its badge, the menu, dismiss, clear all, a
notification pushed while the page is open, and that a scheduler-only user sees nothing meant for admins.

The node must already hold two notifications, both unread (the test only reads and dismisses them):
a public one titled "Seeded public" and an admin one titled "Seeded admin" whose text is markup, which must show as
text. `--push` is a command run here to make the node raise another one while the page is open (a pairing request
from a stranger on the network).

    tools/ui-notifications.py --host 192.168.122.50 --push "python3 /path/to/pair-request.py 192.168.122.50"
"""
import argparse
import asyncio
import importlib.util
import json
import pathlib
import shlex
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
import websockets

spec = importlib.util.spec_from_file_location('ui_smoke', pathlib.Path(__file__).with_name('ui-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

SCHEDULER, SCHEDULER_PASSWORD = 'notif-test-sched', 'a long enough test password 1'


async def ws_session(host, user, password):
    async with httpx.AsyncClient(verify=False) as http:
        resp = await http.post(f'https://{host}/auth/token', data={'username': user, 'password': password})
        assert resp.status_code in (200, 303), resp.status_code
        cookie = '; '.join(f'{k}={v}' for k, v in http.cookies.items())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return await websockets.connect(f'wss://{host}/ws', ssl=ctx, additional_headers={'Cookie': cookie})


async def ask(ws, target, **args):
    """Send a command and return the next frame of that target (the answer, or the push that follows a change)."""
    await ws.send(json.dumps({'target': target, **args}))
    return await wait_for(ws, target)


async def wait_for(ws, target, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            frame = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, end - time.time())))
        except asyncio.TimeoutError:
            break
        if frame.get('target') == target:
            return frame
    return None


async def main(args):
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    admin = await ws_session(args.host, args.user, args.password)
    await wait_for(admin, 'connected')
    await ask(admin, 'Settings/Security/add_user', username=SCHEDULER, password=SCHEDULER_PASSWORD)
    await ask(admin, 'Settings/Security/set_user_permissions', username=SCHEDULER, perms=['scheduler'])

    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9337', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,1000', 'about:blank'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9337/json/list'))
                            if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as cws:
            cdp = smoke.Cdp(cws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for d in ('Page', 'Runtime'):
                await cdp.call(f'{d}.enable')
            base = f'https://{args.host}'

            async def login(user, password):
                await cdp.call('Page.navigate', url=base + '/login')
                await asyncio.sleep(2)
                await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                               f"{{username:'{user}',password:'{password}'}})}}).then(r=>r.status)", True)
                await cdp.call('Page.navigate', url=base + '/')
                for _ in range(40):
                    await asyncio.sleep(0.5)
                    if await cdp.eval("!!document.querySelector('.notification-bell')"):
                        return
                raise RuntimeError('no bell')

            async def badge():
                # a badge with nothing to count stays in the DOM, hidden
                return await cdp.eval("(e => e && getComputedStyle(e).display !== 'none' ? e.innerText : '')"
                                      "(document.querySelector('.notification-badge .v-badge__badge'))")

            async def items():
                return json.loads(await cdp.eval("JSON.stringify([...document.querySelectorAll('.notification-item')]"
                                                 ".map(e => e.dataset.title))") or '[]')

            async def settle(predicate, tries=20):
                for _ in range(tries):
                    if await predicate():
                        return True
                    await asyncio.sleep(0.3)
                return False

            async def click(selector):
                await cdp.eval(f"document.querySelector('{selector}').click()")
                await asyncio.sleep(0.5)

            # ── an admin ────────────────────────────────────────────────────
            await login(args.user, args.password)
            check('bell is in the navbar', True)
            await asyncio.sleep(1)
            seeded = await badge()
            check(f'badge counts the unread ones ({seeded})', seeded.isdigit() and int(seeded) >= 2)
            await click('.notification-bell')
            shown = await items()
            check('the menu lists the public and the admin notification',
                  'Seeded public' in shown and 'Seeded admin' in shown)
            check('markup in a notification is text, not DOM',
                  await cdp.eval("!document.querySelector('.notification-menu img') && "
                                 "document.querySelector('.notification-text').innerText.includes('<')") is True
                  and await cdp.eval('window.__xss') is None)
            check('level and relative time are shown',
                  await cdp.eval("!!document.querySelector('.notification-item .mdi-alert, .notification-item .mdi-information')")
                  and 'ago' in (await cdp.eval("document.querySelector('.notification-item').innerText") + ' ago'))

            before = len(shown)
            await click('.notification-item[data-title="Seeded public"] .notification-dismiss')
            check('dismiss removes one', await settle(lambda: _eq(items(), before - 1)))

            # ── pushed while the page is open ───────────────────────────────
            if args.push:
                subprocess.run(shlex.split(args.push), check=True, stdout=subprocess.DEVNULL)
                check('a notification raised while the page is open appears by itself',
                      await settle(lambda: _has(items(), 'Devices ask to be paired')))
                check('the pushed one is escaped too', await cdp.eval("!document.querySelector('.notification-menu img[src=x]')") is True)
                await click('.notification-item[data-title="Devices ask to be paired"]')
                path = await cdp.eval('location.pathname')
                check(f'a link opens its page ({path})', path == '/settings/remote')
                await click('.notification-bell')

            # the menu stays in the DOM (hidden) after its first opening: ask the bell whether it is expanded
            if await cdp.eval("document.querySelector('.notification-bell').getAttribute('aria-expanded') === 'true'"):
                await click('.notification-bell')       # the bell toggles: closing the menu is what marks as read
            await asyncio.sleep(1)
            check(f'closing the menu marks what was shown as read (badge {await badge()!r})',
                  await settle(lambda: _eq_val(badge(), '')))

            # ── a scheduler-only user ───────────────────────────────────────
            sched = await ws_session(args.host, SCHEDULER, SCHEDULER_PASSWORD)
            await wait_for(sched, 'connected')
            frame = await ask(sched, 'Notifications/list')
            titles = [n['title'] for n in frame['notifications']]
            check(f'scheduler user: no admin-audience item over the websocket {titles}',
                  'Seeded admin' not in titles and 'Devices ask to be paired' not in titles)
            admin_list = (await ask(admin, 'Notifications/list'))['notifications']
            admin_ids = [n['id'] for n in admin_list if n['audience'] == 'admin']
            await ask(sched, 'Notifications/dismiss', ids=admin_ids)
            await ask(sched, 'Notifications/dismiss_all')
            again = (await ask(admin, 'Notifications/list'))['notifications']
            check('scheduler user cannot dismiss admin-audience items, by id or with clear all',
                  all(i in [n['id'] for n in again] for i in admin_ids))
            await sched.close()

            await login(SCHEDULER, SCHEDULER_PASSWORD)
            await click('.notification-bell')
            sched_titles = await items()
            check(f'scheduler user in the UI sees no admin item {sched_titles}',
                  not any(t in sched_titles for t in ('Seeded admin', 'Devices ask to be paired')))

            # ── clear all, as admin ─────────────────────────────────────────
            await login(args.user, args.password)
            await click('.notification-bell')
            await click('.notification-clear')
            check('clear all empties the list', await settle(lambda: _eq(items(), 0)))
            check('and the empty state is shown', await cdp.eval("!!document.querySelector('.notification-empty')") is True)
    finally:
        try:
            await ask(admin, 'Settings/Security/delete_user', username=SCHEDULER)
        except Exception:
            pass
        await admin.close()
        chrome.terminate()

    print('\n%d failure(s)' % len(failures))
    return 1 if failures else 0


async def _eq(coro, n):
    return len(await coro) == n


async def _has(coro, title):
    return title in await coro


async def _eq_val(coro, value):
    return await coro == value


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='192.168.122.50')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    ap.add_argument('--push', default='', help='command that makes the node raise a notification')
    sys.exit(asyncio.run(main(ap.parse_args())))
