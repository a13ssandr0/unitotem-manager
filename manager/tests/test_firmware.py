"""The firmware card's thin client, its websocket API and its progress watch. The helper is never run: subprocesses are mocked."""
import asyncio
import struct

import pytest

from api.ws.permissions import APIPermissionError, check_permissions
from unitotem_node.api import firmware as api_firmware
from unitotem_node.system import firmware, firmware_progress as fp
from utils.notifications import NotificationCenter

STATUS = ('{"kind":"pi","supported":true,"model":"Raspberry Pi 4 Model B Rev 1.5","family":"pi4","op":null,"phase":null,'
          '"percent":null,"error":null,"throttled":"0x0","under_voltage":false,"reboot_required":false,'
          '"files":{"installed":"2026.11.1","available":null,"update":null,"pairs":[],"bak":false},"eeprom":null,'
          '"blockers":[],"checked_at":null}')


# ── parse_output and the exit codes ──────────────────────────────────────────

def test_absent_helper_means_unsupported():
    assert firmware.parse_output(127, '') == {'supported': False}


def test_status_is_passed_through_with_its_return_code():
    data = firmware.parse_output(0, STATUS)
    assert data['supported'] is True and data['family'] == 'pi4' and data['returncode'] == 0
    assert data['files']['installed'] == '2026.11.1'


def test_no_firmware_card_is_reported_by_the_helper_itself():
    data = firmware.parse_output(0, '{"kind":"none","supported":false}')
    assert data['supported'] is False and data['kind'] == 'none'


def test_nothing_to_do_is_a_normal_answer():
    data = firmware.parse_output(10, '{"checked":true,"files":{"update":false},"eeprom":null,"checked_at":5}')
    assert data['returncode'] == 10 and data['checked'] is True and 'error' not in data


def test_busy_gives_a_reason_when_the_helper_gave_none():
    data = firmware.parse_output(12, '')
    assert data['error_code'] == 'busy' and data['returncode'] == 12


def test_blocked_keeps_the_blockers_of_the_helper():
    data = firmware.parse_output(14, '{"error":"blocked","error_code":"blocked","blockers":["under-voltage","busy"]}')
    assert data['error_code'] == 'blocked' and data['blockers'] == ['under-voltage', 'busy']
    assert firmware.parse_output(14, '')['error_code'] == 'blocked'


def test_failure_keeps_its_error_code_and_defaults_without_one():
    assert firmware.parse_output(2, '{"error":"bad","error_code":"bad-signature"}')['error_code'] == 'bad-signature'
    data = firmware.parse_output(2, '')
    assert data['error_code'] == 'failed' and data['error']


def test_garbage_output_is_reported_not_raised():
    assert firmware.parse_output(0, 'not json')['error_code'] == 'bad-output'
    assert firmware.parse_output(0, '[1, 2]')['error_code'] == 'bad-output'


def test_the_last_line_is_the_answer():
    data = firmware.parse_output(0, 'some log line\n' + STATUS + '\n')
    assert data['family'] == 'pi4'


# ── running the helper (subprocess mocked) ───────────────────────────────────

class FakeProc:
    def __init__(self, returncode, out=b'', err=b'', hang=False):
        self.returncode, self._out, self._err, self._hang = returncode, out, err, hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(3600)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def mock_exec(monkeypatch, proc=None, missing=False):
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)
        if missing:
            raise FileNotFoundError(args[0])
        return proc

    monkeypatch.setattr(firmware.asyncio, 'create_subprocess_exec', fake)
    return calls


def test_run_uses_an_argument_list_and_json(monkeypatch):
    calls = mock_exec(monkeypatch, FakeProc(0, STATUS.encode()))
    data = asyncio.run(firmware.status())
    assert calls == [(firmware.FIRMWARE, '--json', 'status')]
    assert data['family'] == 'pi4'


def test_absent_helper_is_unsupported_without_running_anything(monkeypatch):
    mock_exec(monkeypatch, missing=True)
    assert asyncio.run(firmware.status()) == {'supported': False}


def test_timeout_kills_the_helper(monkeypatch):
    proc = FakeProc(0, hang=True)
    mock_exec(monkeypatch, proc)
    monkeypatch.setattr(firmware, 'TIMEOUT', 0.01)
    data = asyncio.run(firmware.check())
    assert data['error_code'] == 'timeout' and proc.killed


def test_apply_forwards_the_flags_as_arguments(monkeypatch):
    calls = mock_exec(monkeypatch, FakeProc(0, b'{"started":true,"op":"apply"}'))
    asyncio.run(firmware.apply(True, True))
    asyncio.run(firmware.apply(files=True, eeprom=False))
    asyncio.run(firmware.apply(files=False, eeprom=True))
    assert calls == [
        (firmware.FIRMWARE, '--json', 'apply', '--files', '--eeprom'),
        (firmware.FIRMWARE, '--json', 'apply', '--files'),
        (firmware.FIRMWARE, '--json', 'apply', '--eeprom'),
    ]


def test_apply_refuses_nothing_selected_and_non_booleans_without_running(monkeypatch):
    calls = mock_exec(monkeypatch, FakeProc(0, b'{}'))
    assert asyncio.run(firmware.apply(False, False))['error_code'] == 'nothing-selected'
    assert asyncio.run(firmware.apply('false', True))['error_code'] == 'bad-argument'
    assert asyncio.run(firmware.apply(True, 1))['error_code'] == 'bad-argument'
    assert calls == []


def test_apply_reports_a_helper_refusal(monkeypatch):
    mock_exec(monkeypatch, FakeProc(14, b'{"error":"blocked","error_code":"blocked","blockers":["under-voltage"]}'))
    data = asyncio.run(firmware.apply())
    assert data['returncode'] == 14 and data['blockers'] == ['under-voltage']


# ── websocket API: status polling, pushes, permission ────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast(self, name, **kwargs):
        self.sent.append((name, kwargs))


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(api_firmware, 'POLL_INTERVAL', 0)
    monkeypatch.setattr(api_firmware.Firmware, '_poller', None)
    ws = FakeWS()
    return api_firmware.Firmware(ws, None), ws


def test_status_polls_and_pushes_until_the_operation_ends(api, monkeypatch):
    instance, ws = api
    answers = [{'supported': True, 'op': 'apply', 'phase': 'swap', 'percent': 40},
               {'supported': True, 'op': 'apply', 'phase': 'eeprom', 'percent': 80},
               {'supported': True, 'op': None, 'percent': None}]

    async def status():
        return answers.pop(0) if answers else {'supported': True, 'op': None}

    monkeypatch.setattr(firmware, 'status', status)

    async def scenario():
        reply = await instance.status()
        assert reply is not None
        await api_firmware.Firmware._poller

    asyncio.run(scenario())
    pushed = [kw['status']['op'] for name, kw in ws.sent if name == 'Settings/Firmware/status']
    assert pushed == ['apply', None]        # the first answer is the status() call itself, not pushed
    assert answers == []


def test_no_polling_when_nothing_runs(api, monkeypatch):
    instance, ws = api

    async def status():
        return {'supported': True, 'op': None}

    monkeypatch.setattr(firmware, 'status', status)

    async def scenario():
        await instance.status()
        assert api_firmware.Firmware._poller is None

    asyncio.run(scenario())
    assert ws.sent == []


def test_apply_starts_polling_and_check_pushes_the_new_status(api, monkeypatch):
    instance, ws = api
    seen = []

    async def apply(files=True, eeprom=True):
        seen.append((files, eeprom))
        return {'started': True, 'op': 'apply', 'files': files, 'eeprom': eeprom}

    async def check():
        return {'checked': True, 'files': {'update': True}}

    async def status():
        return {'supported': True, 'op': None, 'files': {'update': True}}

    monkeypatch.setattr(firmware, 'apply', apply)
    monkeypatch.setattr(firmware, 'check', check)
    monkeypatch.setattr(firmware, 'status', status)

    async def scenario():
        await instance.apply(files=False, eeprom=True)
        await api_firmware.Firmware._poller
        await instance.check()

    asyncio.run(scenario())
    assert seen == [(False, True)]
    assert ('Settings/Firmware/status', {'status': {'supported': True, 'op': None, 'files': {'update': True}}}) in ws.sent


def test_firmware_api_is_admin_only():
    class User:
        name = 'visitor'
        perms = set()

        class has_perm:
            admin = False

    user = User()
    for method in (api_firmware.Firmware.status, api_firmware.Firmware.check, api_firmware.Firmware.apply):
        with pytest.raises(APIPermissionError):
            check_permissions(method, user)


# ── progress watch: notifications and the status push ─────────────────────────

@pytest.fixture
def center(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    monkeypatch.setattr(fp, 'notifications', center)
    return center


def run_watch(monkeypatch, answers):
    pushed = []

    async def status():
        return answers.pop(0)

    monkeypatch.setattr(fp.firmware, 'status', status)

    async def on_status(s):
        pushed.append(s)

    async def scenario():
        watcher = fp.FirmwareProgress()
        watcher._loop = asyncio.get_running_loop()
        watcher._on_status = on_status
        return watcher

    return scenario, pushed


def test_an_apply_is_followed_from_start_to_a_restart_request(center, monkeypatch):
    running = {'supported': True, 'op': 'apply', 'phase': 'swap', 'percent': 40}
    answers = [running, {**running, 'phase': 'eeprom', 'percent': 80},
               {'supported': True, 'op': None, 'reboot_required': True, 'error': None}]
    scenario, pushed = run_watch(monkeypatch, answers)

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        first = center.snapshot(['admin'])[0]
        assert first['title'] == 'Updating the firmware' and first['progress'] == 40 and first['persistent']
        assert first['text'] == 'Writing'
        await watcher.refresh()
        assert center.snapshot(['admin'])[0]['progress'] == 80
        await watcher.refresh()
        shown = {n['title']: n for n in center.snapshot(['admin'])}
        assert list(shown) == ['Firmware updated'] and shown['Firmware updated']['level'] == 'info'
        assert shown['Firmware updated']['text'] == 'Restart the device to start the new firmware.'

    asyncio.run(run())
    assert len(pushed) == 3


def test_a_failed_apply_is_an_error_and_the_bar_goes(center, monkeypatch):
    answers = [{'supported': True, 'op': 'apply', 'phase': 'verify', 'percent': 10},
               {'supported': True, 'op': None, 'error': {'error': 'Signature does not match', 'error_code': 'bad-signature'}}]
    scenario, _ = run_watch(monkeypatch, answers)

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        await watcher.refresh()

    asyncio.run(run())
    shown = center.snapshot(['admin'])
    assert [(n['title'], n['level']) for n in shown] == [('Firmware update failed', 'error')]
    assert shown[0]['text'] == 'Signature does not match'


def test_a_check_leaves_no_done_notification(center, monkeypatch):
    answers = [{'supported': True, 'op': 'check', 'phase': 'fetch', 'percent': None},
               {'supported': True, 'op': None, 'error': None}]
    scenario, _ = run_watch(monkeypatch, answers)

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        assert [n['title'] for n in center.snapshot(['admin'])] == ['Checking the firmware']
        await watcher.refresh()

    asyncio.run(run())
    assert center.snapshot(['admin']) == []


def test_an_absent_helper_shows_nothing(center, monkeypatch):
    scenario, pushed = run_watch(monkeypatch, [{'supported': False}])

    async def run():
        watcher = await scenario()
        await watcher.refresh()

    asyncio.run(run())
    assert center.snapshot(['admin']) == [] and pushed == []


def test_a_bar_left_by_a_dead_manager_is_removed_at_start(center, monkeypatch):
    center.notify(fp.KEY, 'info', 'Updating the firmware', progress=30, persistent=True)
    scenario, _ = run_watch(monkeypatch, [{'supported': True, 'op': None}])

    async def run():
        watcher = await scenario()
        await watcher.refresh()

    asyncio.run(run())
    assert center.snapshot(['admin']) == []


def test_a_file_event_is_throttled_only_for_its_own_name(monkeypatch):
    watcher = fp.FirmwareProgress()
    scheduled = []
    watcher._schedule = lambda: scheduled.append(True)
    name = b'unitotem-firmware.progress\0\0\0\0\0\0\0\0'
    event = struct.pack('iIII', 1, 0x80, 0, len(name)) + name
    other = b'unitotem-update.progress\0'
    other_event = struct.pack('iIII', 1, 0x80, 0, len(other)) + other
    watcher._fd = None
    monkeypatch.setattr(fp.os, 'read', lambda fd, n: other_event)
    watcher._readable()
    assert scheduled == []
    monkeypatch.setattr(fp.os, 'read', lambda fd, n: event + event)
    watcher._readable()
    assert scheduled == [True]


def test_without_inotify_the_watch_still_reads_the_status_once(center, monkeypatch):
    monkeypatch.setattr(fp, '_inotify', lambda path: None)
    answers = [{'supported': True, 'op': None}]
    pushed = []

    async def status():
        return answers.pop(0)

    async def on_status(s):
        pushed.append(s)

    monkeypatch.setattr(fp.firmware, 'status', status)

    async def run():
        watcher = fp.FirmwareProgress()
        await watcher.start(on_status)
        watcher.stop()

    asyncio.run(run())
    assert pushed == [{'supported': True, 'op': None}]


def test_the_watch_file_and_keys_are_the_contract_names():
    assert fp.PROGRESS_FILE == 'unitotem-firmware.progress'
    assert fp.KEY == 'firmware.progress'


# ── restart after an apply ────────────────────────────────────────────────────

RUNNING_APPLY = {'supported': True, 'op': 'apply', 'phase': 'swap', 'percent': 40}


@pytest.fixture(autouse=True)
def _restart_request_reset():
    firmware.request_restart(False)
    yield
    firmware.request_restart(False)


@pytest.fixture
def restarts(monkeypatch):
    calls = []

    async def restart():
        calls.append(True)

    monkeypatch.setattr(fp.restart, 'restart', restart)
    return calls


def fake_apply_answer(monkeypatch, answer):
    async def apply(files=True, eeprom=True):
        return answer

    async def status():
        return {'supported': True, 'op': None}

    monkeypatch.setattr(firmware, 'apply', apply)
    monkeypatch.setattr(firmware, 'status', status)


def test_apply_asks_for_a_restart_only_when_the_apply_started(api, monkeypatch):
    instance, _ = api
    fake_apply_answer(monkeypatch, {'started': True, 'op': 'apply'})

    async def scenario():
        await instance.apply(restart=True)
        await api_firmware.Firmware._poller

    asyncio.run(scenario())
    assert firmware._restart_requested is True


def test_a_refused_apply_does_not_ask_for_a_restart(api, monkeypatch):
    instance, _ = api
    fake_apply_answer(monkeypatch, {'supported': True, 'error': 'A firmware operation is already running', 'error_code': 'busy'})

    async def scenario():
        await instance.apply(restart=True)
        await api_firmware.Firmware._poller

    asyncio.run(scenario())
    assert firmware._restart_requested is False


def test_apply_without_restart_clears_the_request(api, monkeypatch):
    instance, _ = api
    firmware.request_restart(True)
    fake_apply_answer(monkeypatch, {'started': True, 'op': 'apply'})

    async def scenario():
        await instance.apply(restart=False)
        await api_firmware.Firmware._poller

    asyncio.run(scenario())
    assert firmware._restart_requested is False


def test_apply_restart_must_be_a_boolean(api, monkeypatch):
    instance, _ = api
    calls = mock_exec(monkeypatch, FakeProc(0, b'{"started":true}'))
    reply = asyncio.run(instance.apply(restart='yes'))
    assert reply.kwargs['result'] == {'supported': True, 'error': 'restart is true or false', 'error_code': 'bad-argument'}
    assert calls == [] and firmware._restart_requested is False


def test_status_tells_whether_a_restart_is_pending(monkeypatch):
    mock_exec(monkeypatch, FakeProc(0, STATUS.encode()))
    assert asyncio.run(firmware.status())['restart_pending'] is False
    firmware.request_restart(True)
    assert asyncio.run(firmware.status())['restart_pending'] is True


def test_status_without_a_card_has_no_restart_key(monkeypatch):
    mock_exec(monkeypatch, missing=True)
    firmware.request_restart(True)
    assert 'restart_pending' not in asyncio.run(firmware.status())


def test_an_apply_with_restart_restarts_once_when_it_ends(center, monkeypatch, restarts):
    firmware.request_restart(True)
    end = {'supported': True, 'op': None, 'reboot_required': True, 'error': None, 'restart_pending': True}
    scenario, pushed = run_watch(monkeypatch, [dict(RUNNING_APPLY), end])

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        await watcher.refresh()

    asyncio.run(run())
    assert restarts == [True]
    assert [n['text'] for n in center.snapshot(['admin']) if n['title'] == 'Firmware updated'] == ['The device restarts now.']
    assert pushed[-1]['restart_pending'] is True
    assert firmware._restart_requested is True


def test_a_failed_restart_keeps_the_normal_text(center, monkeypatch):
    firmware.request_restart(True)

    async def boom():
        raise RuntimeError('no system bus')

    monkeypatch.setattr(fp.restart, 'restart', boom)
    end = {'supported': True, 'op': None, 'reboot_required': True, 'error': None, 'restart_pending': True}
    scenario, pushed = run_watch(monkeypatch, [dict(RUNNING_APPLY), end])

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        await watcher.refresh()

    asyncio.run(run())
    assert [n['text'] for n in center.snapshot(['admin']) if n['title'] == 'Firmware updated'] == [
        'Restart the device to start the new firmware.']
    assert pushed[-1]['restart_pending'] is False
    assert firmware._restart_requested is False


def test_an_error_end_does_not_restart_and_clears_the_request(center, monkeypatch, restarts):
    firmware.request_restart(True)
    end = {'supported': True, 'op': None, 'error': {'error': 'Signature does not match', 'error_code': 'bad-signature'},
           'restart_pending': True}
    scenario, pushed = run_watch(monkeypatch, [dict(RUNNING_APPLY), end])

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        await watcher.refresh()

    asyncio.run(run())
    assert restarts == []
    assert firmware._restart_requested is False
    assert pushed[-1]['restart_pending'] is False


def test_no_reboot_required_means_no_restart(center, monkeypatch, restarts):
    firmware.request_restart(True)
    end = {'supported': True, 'op': None, 'reboot_required': False, 'error': None, 'restart_pending': True}
    scenario, pushed = run_watch(monkeypatch, [dict(RUNNING_APPLY), end])

    async def run():
        watcher = await scenario()
        await watcher.refresh()
        await watcher.refresh()

    asyncio.run(run())
    assert restarts == []
    assert [n['text'] for n in center.snapshot(['admin']) if n['title'] == 'Firmware updated'] == [
        'The firmware is up to date.']
    assert pushed[-1]['restart_pending'] is False


def test_power_reboot_goes_through_restart(monkeypatch):
    from unitotem_node.api import power as api_power
    from unitotem_node.system import restart as restart_module
    calls = []

    async def restart():
        calls.append(True)

    monkeypatch.setattr(restart_module, 'restart', restart)
    asyncio.run(api_power.Power.reboot())
    assert calls == [True]
