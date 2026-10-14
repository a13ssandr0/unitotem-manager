"""First access without a default password: claim code, rate limiting, the claim itself and the password policy."""
import os
import stat
import threading

import pytest

from utils import claim, passwords
from utils.claim import (ALPHABET, AlreadyClaimed, ClaimManager, CODE_LENGTH, FAILURES_BEFORE_NEW_CODE, Invalid, Locked,
                         WrongCode, generate_code, normalize, pretty)
from utils.models.user import UserData, UserPerms

GOOD = 'correct horse battery'


class FakeUsers:
    """What ClaimManager needs of the user manager, without writing /etc/unitotem."""

    def __init__(self):
        self.root = {}
        self.fail_saving = False

    def add_user(self, username, password, permissions=None):
        if self.fail_saving:
            self.root[username] = UserData(password='x', permissions=permissions or set())
            raise OSError('read-only file system')
        self.root[username] = UserData(password='hash:' + password, permissions=permissions or set())


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def users():
    return FakeUsers()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def manager(tmp_path, users, clock):
    m = ClaimManager(users, tmp_path / 'state' / 'claim-code', clock)
    m.arm()
    return m


def wrong(manager, times=1):
    for _ in range(times):
        with pytest.raises((WrongCode, Locked)):
            manager.claim('AAAAA-AAAAA', 'boss', GOOD)


# ── the code ─────────────────────────────────────────────────────────────────

def test_generated_codes_are_long_unambiguous_and_different():
    codes = {generate_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert len(code) == CODE_LENGTH >= 8
        assert set(code) <= set(ALPHABET)
    assert not set('01OIL') & set(ALPHABET)


def test_the_code_is_displayed_in_groups_and_typed_loosely():
    assert pretty('ABCDEFGHJK') == 'ABCDE-FGHJK'
    assert normalize(' abcde-fghjk ') == 'ABCDEFGHJK'


def test_the_code_file_is_root_only_and_has_the_code(manager):
    path = manager.code_file
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_text().strip() == manager.display_code()


# ── states ───────────────────────────────────────────────────────────────────

def test_a_node_without_users_is_unclaimed_and_gets_a_code(manager):
    assert manager.unclaimed
    assert manager.display_code()


def test_a_node_with_an_administrator_is_claimed_and_has_no_code(tmp_path, users, clock):
    users.root['boss'] = UserData(password='x', permissions={UserPerms.admin})
    m = ClaimManager(users, tmp_path / 'claim-code', clock)
    m.arm()
    assert m.is_claimed()
    assert m.display_code() is None
    assert not (tmp_path / 'claim-code').exists()


def test_users_without_an_administrator_still_leave_the_node_unclaimed(tmp_path, users, clock):
    users.root['guest'] = UserData(password='x', permissions={UserPerms.scheduler})
    m = ClaimManager(users, tmp_path / 'claim-code', clock)
    m.arm()
    assert m.unclaimed and m.display_code()


def test_arming_again_replaces_the_code(manager):
    first = manager.display_code()
    manager.arm()
    assert manager.display_code() != first
    assert manager.code_file.read_text().strip() == manager.display_code()


def test_a_reset_of_the_users_arms_a_new_code(manager, users):
    manager.claim(manager.display_code(), 'boss', GOOD)
    assert manager.is_claimed() and not manager.code_file.exists()
    users.root.clear()
    manager.arm()
    assert manager.unclaimed and manager.display_code() and manager.code_file.exists()


# ── claiming ─────────────────────────────────────────────────────────────────

def test_a_good_claim_creates_the_administrator_and_removes_the_code(manager, users):
    code = manager.display_code()
    manager.claim(code.lower(), 'boss', GOOD)         # typed in lower case
    assert UserPerms.admin in users.root['boss'].permissions
    assert manager.is_claimed()
    assert manager.display_code() is None
    assert not manager.code_file.exists()


def test_a_claimed_node_can_never_be_claimed_again(manager):
    code = manager.display_code()
    manager.claim(code, 'boss', GOOD)
    for attempt in (code, 'AAAAA-AAAAA'):
        with pytest.raises(AlreadyClaimed):
            manager.claim(attempt, 'intruder', GOOD)


def test_a_wrong_code_creates_nothing(manager, users):
    wrong(manager)
    assert users.root == {} and manager.unclaimed


def test_policy_problems_are_reported_only_to_somebody_with_the_code(manager, users):
    with pytest.raises(WrongCode):
        manager.claim('AAAAA-AAAAA', 'boss', 'x')
    with pytest.raises(Invalid) as info:
        manager.claim(manager.display_code(), 'boss', 'short')
    assert info.value.problems
    assert users.root == {} and manager.unclaimed


def test_a_failure_to_save_leaves_the_node_unclaimed(manager, users):
    users.fail_saving = True
    with pytest.raises(Invalid):
        manager.claim(manager.display_code(), 'boss', GOOD)
    assert users.root == {} and manager.unclaimed and manager.display_code()


def test_of_many_simultaneous_claims_exactly_one_wins(manager, users):
    code = manager.display_code()
    barrier = threading.Barrier(16)
    results = []

    def attempt(n):
        barrier.wait()
        try:
            manager.claim(code, f'boss{n}', GOOD)
            results.append('won')
        except AlreadyClaimed:
            results.append('late')

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count('won') == 1 and results.count('late') == 15
    assert len(users.root) == 1


# ── rate limiting ────────────────────────────────────────────────────────────

def test_four_wrong_codes_are_free_the_fifth_locks_out(manager, clock):
    wrong(manager, 4)
    with pytest.raises(WrongCode):
        manager.claim('AAAAA-AAAAA', 'boss', GOOD)       # the fifth
    with pytest.raises(Locked) as info:
        manager.claim(manager.display_code(), 'boss', GOOD)     # even the right code is refused while locked
    assert 0 < info.value.retry_after <= 31


def test_the_lockout_ends_and_grows(manager, clock):
    wrong(manager, 5)                                    # locked 30 s
    clock.now += 31
    with pytest.raises(WrongCode):                       # the sixth is compared again...
        manager.claim('AAAAA-AAAAA', 'boss', GOOD)
    with pytest.raises(Locked) as info:                  # ...and locks for twice as long
        manager.claim('AAAAA-AAAAA', 'boss', GOOD)
    assert 30 < info.value.retry_after <= 61


def test_a_locked_attempt_does_not_count_as_a_failure(manager, clock):
    wrong(manager, 5)
    for _ in range(50):
        with pytest.raises(Locked):
            manager.claim('AAAAA-AAAAA', 'boss', GOOD)
    clock.now += 31
    manager.claim(manager.display_code(), 'boss', GOOD)  # still the same code, still valid
    assert manager.is_claimed()


def test_after_ten_failures_the_code_is_replaced_and_listeners_hear_it(manager, clock):
    heard = []
    manager.subscribe(heard.append)
    old = manager.display_code()
    for _ in range(FAILURES_BEFORE_NEW_CODE):
        clock.now += 1000
        with pytest.raises(WrongCode):
            manager.claim('AAAAA-AAAAA', 'boss', GOOD)
    assert manager.display_code() != old
    assert heard and heard[-1]['code'] == manager.display_code()
    clock.now += 1000
    with pytest.raises(WrongCode):
        manager.claim(old, 'boss', GOOD)                 # the old code is dead


def test_listeners_hear_the_claim_and_can_unsubscribe(manager):
    heard = []
    unsubscribe = manager.subscribe(heard.append)
    manager.claim(manager.display_code(), 'boss', GOOD)
    assert heard[-1] == {'claimed': True, 'code': None}
    unsubscribe()
    count = len(heard)
    manager.arm()
    assert len(heard) == count


def test_the_comparison_is_constant_time():
    import inspect
    assert 'compare_digest' in inspect.getsource(claim.ClaimManager.claim)


def test_the_code_is_not_in_the_state_of_a_claimed_node(manager):
    manager.claim(manager.display_code(), 'boss', GOOD)
    assert manager.state() == {'claimed': True, 'code': None}


# ── password and username policy ─────────────────────────────────────────────

@pytest.mark.parametrize('password', ['', 'short', 'admin', 'password', 'Password123', 'aaaaaaaaaaaa', 'x' * 129,
                                      'line\nbreak and more', 'tab\tseparated'])
def test_weak_passwords_are_refused(password):
    assert passwords.validate_password(password, 'boss')


def test_the_password_must_differ_from_the_username():
    assert passwords.validate_password('Zebra-Quartz-99', 'zebra-quartz-99')
    assert not passwords.validate_password('Zebra-Quartz-99', 'boss')


@pytest.mark.parametrize('password', [GOOD, 'Tr0ub4dor&3-long', 'pässwörd-lunga-123', 'ab' * 64])
def test_reasonable_passwords_are_accepted(password):
    assert not passwords.validate_password(password, 'boss')


@pytest.mark.parametrize('name', ['', ' ', 'a b', '../x', 'a' * 33, '-dash', 'ro:ot', 'new\nline', 'tab\t', None])
def test_bad_usernames_are_refused(name):
    assert passwords.validate_username(name)


@pytest.mark.parametrize('name', ['admin', 'Boss', 'a.b-c_d', 'x' * 32, '7up'])
def test_good_usernames_are_accepted(name):
    assert not passwords.validate_username(name)
