"""
First access to a node that has no password.

A node starts without accounts: nothing ships with a default password. As long as there is no administrator the node is
"unclaimed": the web UI only offers the setup page, and the API refuses everything. To prove that the person is
physically at the device, the setup asks for a CLAIM CODE that the node shows where only somebody standing at it can
read it:

- on the totem screen (the first-boot and "no assets" pages of the local viewer, over a loopback-only event stream);
- in the journal as a warning (see `_new_code` for why that takes a priority prefix), and in a root-only file, for a
  node without a screen.

The code is never sent to a client over the network. Wrong codes are rate-limited globally (not per address: the
attacker may have many), compared in constant time, and the code is replaced by a new one after too many failures.
A successful claim creates the administrator and removes the code; from then on `claim()` always refuses, also when
two people claim at the same time.
"""
from __future__ import annotations

import hmac
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from utils.passwords import validate_password, validate_username

# No 0/O, 1/I/L: the code is read from a screen and typed on another device
ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
CODE_LENGTH = 10                    # 31**10 is about 2**49
CODE_GROUP = 5                      # shown as XXXXX-XXXXX

FREE_ATTEMPTS = 4                   # the 5th wrong code starts the lock-outs
FAILURES_BEFORE_NEW_CODE = 10
FIRST_LOCKOUT = 30.0                # seconds, doubled by every further failure
MAX_LOCKOUT = 900.0


class ClaimError(Exception):
    """Base of the refusals; `status` is the HTTP status the router answers with."""
    status = 400


class AlreadyClaimed(ClaimError):
    status = 404                    # as if the page did not exist


class Locked(ClaimError):
    status = 429

    def __init__(self, retry_after: float):
        super().__init__(f'Too many wrong codes, try again in {int(retry_after) + 1} seconds')
        self.retry_after = int(retry_after) + 1


class WrongCode(ClaimError):
    status = 403

    def __init__(self):
        super().__init__('Wrong claim code')


class Invalid(ClaimError):
    status = 422

    def __init__(self, problems: list[str]):
        super().__init__('; '.join(problems))
        self.problems = problems


def normalize(code: str) -> str:
    """What the person typed, compared as the code is stored: no spaces or dashes, upper case."""
    return ''.join(c for c in str(code).upper() if c.isalnum())[:64]


def generate_code() -> str:
    return ''.join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def pretty(code: str) -> str:
    return '-'.join(code[i:i + CODE_GROUP] for i in range(0, len(code), CODE_GROUP))


class ClaimManager:
    def __init__(self, users, code_file: Path, clock: Callable[[], float] = time.monotonic):
        self._users = users
        self.code_file = Path(code_file)
        self._clock = clock
        self._lock = threading.Lock()
        self._code: Optional[str] = None
        self._failures = 0
        self._locked_until = 0.0
        self._listeners: list[Callable[[dict], None]] = []

    # ── state ────────────────────────────────────────────────────────────────

    def is_claimed(self) -> bool:
        from utils.models.user import UserPerms
        return any(UserPerms.admin in data.permissions for data in self._users.root.values())

    @property
    def unclaimed(self) -> bool:
        return not self.is_claimed()

    def display_code(self) -> Optional[str]:
        """The code for the screen and for nowhere else; None once the node is claimed."""
        return pretty(self._code) if self._code and self.unclaimed else None

    def state(self) -> dict:
        return {'claimed': self.is_claimed(), 'code': self.display_code()}

    # ── arming: a code exists exactly while the node is unclaimed ─────────────────

    def arm(self):
        """Called at start and after a reset of the users: a new code if the node is unclaimed, none otherwise."""
        with self._lock:
            if self.is_claimed():
                self._disarm()
            else:
                self._new_code()
        self._notify()

    def _new_code(self):
        self._code = generate_code()
        self._failures = 0
        self._write_file()
        message = ('This node is not set up yet. To create the administrator open the setup page of this node '
                   f'and enter the claim code {pretty(self._code)} (also written to {self.code_file}, readable by root only)')
        logger.warning(message)
        # Under systemd every line written to stderr is stored at priority "info", which the kiosk's journald discards
        # (MaxLevelStore=warning): the sd-daemon(3) "<4>" prefix is what makes this one a warning that is kept.
        if os.environ.get('JOURNAL_STREAM'):
            print(f'<4>{message}', file=sys.stderr, flush=True)

    def _disarm(self):
        self._code = None
        self._failures = 0
        self._locked_until = 0.0
        try:
            self.code_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning('Cannot remove {}: {}', self.code_file, exc)

    def _write_file(self):
        """Root-only, and never visible with looser permissions, not even for an instant."""
        try:
            self.code_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.code_file.with_name(self.code_file.name + '.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(pretty(self._code) + '\n')
            os.chmod(tmp, 0o600)
            tmp.replace(self.code_file)
        except OSError as exc:
            logger.warning('Cannot write the claim code to {}: {}', self.code_file, exc)

    # ── the screen: events, not polling ──────────────────────────────────────────

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        """`listener(state)` is called whenever the state shown on the screen changes (from any thread)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _notify(self):
        state = self.state()
        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception:
                logger.exception('A claim listener failed')

    # ── the claim ───────────────────────────────────────────────────────────────

    def claim(self, code: str, username: str, password: str) -> None:
        """Creates the administrator if `code` is the node's claim code. Raises a ClaimError otherwise.

        The whole check-and-create runs under one lock: of two simultaneous claims exactly one succeeds, the other
        finds the node claimed.
        """
        regenerated = False
        try:
            with self._lock:
                if self.is_claimed():
                    raise AlreadyClaimed('already claimed')
                if self._code is None:          # unclaimed but never armed (should not happen): arm now, refuse this one
                    self._new_code()
                    regenerated = True
                    raise WrongCode()
                now = self._clock()
                if now < self._locked_until:
                    raise Locked(self._locked_until - now)
                # Always compared in constant time, on equal-length byte strings
                if not hmac.compare_digest(normalize(code).encode().ljust(64, b'\0'),
                                           self._code.encode().ljust(64, b'\0')):
                    self._failures += 1
                    logger.warning('Wrong claim code ({} in a row)', self._failures)
                    if self._failures >= FAILURES_BEFORE_NEW_CODE:
                        self._new_code()
                        self._locked_until = now + MAX_LOCKOUT
                        regenerated = True
                    elif self._failures > FREE_ATTEMPTS:
                        delay = min(FIRST_LOCKOUT * 2 ** (self._failures - FREE_ATTEMPTS - 1), MAX_LOCKOUT)
                        self._locked_until = now + delay
                    raise WrongCode()
                problems = validate_username(username) + validate_password(password, username)
                if problems:
                    raise Invalid(problems)
                from utils.models.user import UserPerms
                try:
                    self._users.add_user(username, password, {UserPerms.admin})
                except OSError as exc:
                    self._users.root.pop(username, None)        # not saved: the node is still unclaimed
                    logger.error('Cannot save the users: {}', exc)
                    raise Invalid(['The administrator could not be saved, try again']) from exc
                self._disarm()
                logger.success('Node claimed: administrator {} created', username)
        finally:
            if regenerated:
                self._notify()
        self._notify()


def _default_instance() -> ClaimManager:
    from utils.models.user import user_manager
    return ClaimManager(user_manager, CODE_FILE)


CODE_FILE = Path('/var/lib/unitotem/claim-code')
claim_manager = _default_instance()
