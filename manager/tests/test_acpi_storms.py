"""The ACPI storm record: strict parser, state read, push on change, notifications per boot. Every path is in tmp."""
import asyncio
import os
import struct

import pytest

from api.ws.permissions import check_permissions
from unitotem_node.api import acpi_storms as api_storms
from unitotem_node.system import acpi_storms as ast
from utils.notifications import NotificationCenter

BOOT_A = '11111111-2222-3333-4444-555555555555'
BOOT_B = '66666666-7777-8888-9999-000000000000'


def line(name='gpe03', first=1700000000, last=1700000100, rate=10000, boots=1, boot=BOOT_A, masked=1):
    return f'{name} {first} {last} {rate} {boots} {boot} {masked}\n'


def entries(text: str, boot=BOOT_A):
    return ast.parse(text.encode(), boot)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    storms = tmp_path / 'acpi-storms'
    boot = tmp_path / 'boot_id'
    boot.write_text(BOOT_A + '\n')
    monkeypatch.setattr(ast, 'STORMS_FILE', storms)
    monkeypatch.setattr(ast, 'BOOT_ID_FILE', boot)
    return storms, boot


@pytest.fixture
def center(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    monkeypatch.setattr(ast, 'notifications', center)
    return center


# ── parser ───────────────────────────────────────────────────────────────────

def test_valid_file_gives_the_entries_with_their_flags():
    out = entries(line(masked=1) + line('gpe1F', 5, 6, 7, 2, BOOT_B, 0))
    assert out == [
        {'name': 'gpe03', 'first_seen': 1700000000, 'last_seen': 1700000100, 'rate': 10000, 'boots': 1,
         'masked_now': True, 'seen_now': True},
        {'name': 'gpe1F', 'first_seen': 5, 'last_seen': 6, 'rate': 7, 'boots': 2, 'masked_now': False, 'seen_now': False},
    ]


def test_entries_are_sorted_by_name():
    out = entries(line('gpe2A') + line('gpe03') + line('gpeFF') + line('gpe10'))
    assert [e['name'] for e in out] == ['gpe03', 'gpe10', 'gpe2A', 'gpeFF']


def test_masked_and_seen_follow_the_current_boot():
    out = entries(line(boot=BOOT_A, masked=1) + line('gpe04', boot=BOOT_B, masked=1), boot=BOOT_B)
    assert out[0]['seen_now'] is False and out[0]['masked_now'] is False
    assert out[1]['seen_now'] is True and out[1]['masked_now'] is True


def test_masked_zero_is_seen_but_not_masked():
    out = entries(line(masked=0))
    assert out[0]['seen_now'] is True and out[0]['masked_now'] is False


def test_no_boot_id_means_nothing_is_seen_now():
    out = ast.parse(line().encode(), None)
    assert out[0]['seen_now'] is False and out[0]['masked_now'] is False


def test_duplicates_keep_the_first_line_only():
    out = entries(line(rate=5) + line(rate=9) + line('gpe03', rate=100))
    assert len(out) == 1 and out[0]['rate'] == 5


def test_a_bad_line_is_skipped_and_the_others_kept():
    out = entries('garbage here\n' + line('gpe05'))
    assert [e['name'] for e in out] == ['gpe05']


@pytest.mark.parametrize('bad', [
    'gpe0g 1 2 3 1 ' + BOOT_A + ' 1',
    'gpe123 1 2 3 1 ' + BOOT_A + ' 1',
    'GPE03 1 2 3 1 ' + BOOT_A + ' 1',
    'gpe03/../x 1 2 3 1 ' + BOOT_A + ' 1',
    '../gpe03 1 2 3 1 ' + BOOT_A + ' 1',
    'gpe03 4102444801 2 3 1 ' + BOOT_A + ' 1',
    'gpe03 1 2 1000000001 1 ' + BOOT_A + ' 1',
    'gpe03 1 2 3 1000001 ' + BOOT_A + ' 1',
    'gpe03 01 2 3 1 ' + BOOT_A + ' 1',
    'gpe03 1 2 3 1 ' + BOOT_A + ' 2',
    'gpe03 1 2 3 1 ABCDEF01-2222-3333-4444-555555555555 1',
    'gpe03 1 2 3 1 ' + BOOT_A + ' 1 extra',
    'gpe03 1 2 3 1 ' + BOOT_A + ' 1\r',
])
def test_out_of_range_or_malformed_lines_are_skipped(bad):
    assert entries(bad + '\n') == []


def test_bounds_are_inclusive():
    out = entries('gpe03 0 4102444800 1000000000 1000000 ' + BOOT_A + ' 0\n')
    assert out[0]['last_seen'] == 4102444800 and out[0]['rate'] == 10 ** 9 and out[0]['boots'] == 10 ** 6


def test_huge_file_is_empty():
    assert entries(line() + 'x' * 4096) == []


def test_file_at_the_cap_is_read():
    text = line()
    text += '#' * (4096 - len(text) - 1) + '\n'
    assert len(text) == 4096
    assert len(entries(text)) == 1       # read in full: the valid first line counts, the comment line is skipped
    assert entries(text + 'x') == []     # one byte over the cap: refused


def test_a_thousand_lines_are_refused():
    assert entries(line('gpe03') * 1000) == []


def test_sixty_five_lines_are_refused_and_sixty_four_are_read():
    assert entries(line('gpe03') * 65) == []
    short = ''.join(f'gpe{i:02X} 1 1 1 1 0 0\n' for i in range(64))     # 64 lines within the byte cap
    assert len(entries(short, boot='0')) == 64


def test_a_two_thousand_char_line_is_refused():
    assert entries('gpe03 ' + '1' * 2000 + '\n') == []


def test_nul_bytes_make_the_file_empty():
    assert ast.parse(line().encode() + b'\0', BOOT_A) == []


def test_non_utf8_makes_the_file_empty():
    assert ast.parse(b'gpe03 \xff\xfe 2 3 1 ' + BOOT_A.encode() + b' 1\n', BOOT_A) == []


def test_binary_noise_makes_the_file_empty():
    assert ast.parse(bytes(range(256)) * 4, BOOT_A) == []


# ── reading the file ─────────────────────────────────────────────────────────

def test_missing_file_gives_an_empty_list(paths):
    assert ast.read_storms() == []


def test_directory_instead_of_file_gives_an_empty_list(paths):
    storms, _ = paths
    storms.mkdir()
    assert ast.read_storms() == []


def test_fifo_gives_an_empty_list_without_blocking(paths):
    storms, _ = paths
    os.mkfifo(storms)
    assert ast.read_storms() == []


def test_symlink_is_not_followed(paths, tmp_path):
    storms, _ = paths
    real = tmp_path / 'real'
    real.write_text(line())
    os.symlink(real, storms)
    assert ast.read_storms() == []


def test_unreadable_boot_id_means_nothing_is_seen(paths):
    storms, boot = paths
    storms.write_text(line())
    boot.unlink()
    out = ast.read_storms()
    assert out[0]['seen_now'] is False


def test_read_storms_reads_the_file_and_the_boot_id(paths):
    storms, _ = paths
    storms.write_text(line(masked=1))
    out = ast.read_storms()
    assert out == [{'name': 'gpe03', 'first_seen': 1700000000, 'last_seen': 1700000100, 'rate': 10000, 'boots': 1,
                    'masked_now': True, 'seen_now': True}]


# ── the watch: pushes and notifications ──────────────────────────────────────

def run_watch(monkeypatch, *writes, paths=None):
    """Starts a watch (no inotify: one read per refresh call), applies each file state in `writes` and refreshes after it.
    Returns the list of pushes."""
    pushed = []

    async def on_state(storms):
        pushed.append(storms)

    async def run():
        monkeypatch.setattr(ast, '_inotify', lambda path: None)
        watcher = ast.AcpiStorms()
        await watcher.start(on_state)
        for write in writes:
            write()
            await watcher.refresh()
        watcher.stop()

    asyncio.run(run())
    return pushed


def test_push_once_at_start_and_then_only_on_change(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line())
    pushed = run_watch(monkeypatch,
                       lambda: None,                                   # unchanged: no push
                       lambda: storms.write_text(line()),              # same content: no push
                       lambda: storms.write_text(line(rate=20000)))    # changed: push
    assert len(pushed) == 2
    assert pushed[0][0]['rate'] == 10000 and pushed[1][0]['rate'] == 20000


def test_push_of_an_absent_file_is_an_empty_list_once(paths, center, monkeypatch):
    pushed = run_watch(monkeypatch, lambda: None, lambda: None)
    assert pushed == [[]]


def test_a_notification_is_raised_for_an_entry_seen_now(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line(rate=12345, boots=3, masked=1))
    run_watch(monkeypatch)
    items = center.snapshot(['admin'])
    assert len(items) == 1
    item = items[0]
    assert item['title'] == 'ACPI event gpe03 masked'
    assert item['text'] == 'ACPI event gpe03 was firing 12345 times a second and has been masked'
    assert item['level'] == 'warning' and item['source'] == 'acpi-storm-guard'
    assert item['audience'] == 'admin' and item['link'] == '/info'
    assert center._items[0].key == 'acpi.storm.gpe03.3'


def test_masked_zero_text_says_it_cannot_be_masked(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line(rate=50000, masked=0))
    run_watch(monkeypatch)
    item = center.snapshot(['admin'])[0]
    assert item['title'] == 'ACPI event gpe03 cannot be masked'
    assert item['text'] == 'ACPI event gpe03 fires 50000 times a second and cannot be masked'


def test_an_entry_of_an_earlier_boot_is_not_notified(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line(boot=BOOT_B, masked=1))
    pushed = run_watch(monkeypatch)
    assert center.snapshot(['admin']) == []
    assert pushed[0][0]['seen_now'] is False


def test_the_same_boot_read_again_does_not_duplicate(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line())
    run_watch(monkeypatch, lambda: None, lambda: None, lambda: None)
    assert len(center._items) == 1
    assert center.snapshot(['admin'])[0]['read'] is False


def test_a_dismissed_notification_stays_dismissed_in_the_same_boot(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line())
    run_watch(monkeypatch)
    center.dismiss_all(['admin'])
    run_watch(monkeypatch)
    assert center.snapshot(['admin']) == []


def test_a_new_boot_raises_a_new_notification(paths, center, monkeypatch):
    storms, boot = paths
    storms.write_text(line(boots=1))
    run_watch(monkeypatch)
    boot.write_text(BOOT_B + '\n')
    storms.write_text(line(boots=2, boot=BOOT_B))
    run_watch(monkeypatch)
    keys = sorted(i.key for i in center._items)
    assert keys == ['acpi.storm.gpe03.1', 'acpi.storm.gpe03.2']


def test_the_watch_file_name_is_the_contract_name():
    assert ast.STORMS_FILE.name == 'acpi-storms'
    assert ast.STORMS_FILE.parent.as_posix() == '/var/lib/unitotem'


def test_a_file_event_is_throttled_only_for_its_own_name(monkeypatch):
    watcher = ast.AcpiStorms()
    scheduled = []
    watcher._schedule = lambda: scheduled.append(True)
    name = b'acpi-storms\0'
    event = struct.pack('iIII', 1, 0x80, 0, len(name)) + name
    other = b'unitotem-update.progress\0'
    other_event = struct.pack('iIII', 1, 0x80, 0, len(other)) + other
    watcher._fd = None
    monkeypatch.setattr(ast.os, 'read', lambda fd, n: other_event)
    watcher._readable()
    assert scheduled == []
    monkeypatch.setattr(ast.os, 'read', lambda fd, n: event + event)
    watcher._readable()
    assert scheduled == [True]


def test_without_inotify_a_warning_is_logged_and_the_state_is_read_once(paths, center, monkeypatch):
    storms, _ = paths
    storms.write_text(line())
    monkeypatch.setattr(ast, '_inotify', lambda path: None)
    warnings = []
    monkeypatch.setattr(ast.logger, 'warning', lambda *a, **k: warnings.append(a))
    pushed = []

    async def on_state(s):
        pushed.append(s)

    async def run():
        watcher = ast.AcpiStorms()
        await watcher.start(on_state)
        watcher.stop()

    asyncio.run(run())
    assert len(warnings) == 1 and len(pushed) == 1


# ── websocket API ────────────────────────────────────────────────────────────

def test_list_answers_the_state_and_is_open_to_any_logged_in_user(paths):
    storms, _ = paths
    storms.write_text(line())

    class Visitor:
        name = 'visitor'
        perms = set()

        class has_perm:
            admin = False

    check_permissions(api_storms.AcpiStorms.list, Visitor())
    answer = asyncio.run(api_storms.AcpiStorms.list())
    assert answer.kwargs['storms'][0]['name'] == 'gpe03'
