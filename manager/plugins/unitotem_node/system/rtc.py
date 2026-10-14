"""
The hardware clock as the kernel exposes it (`/sys/class/rtc/rtcN`), used to wake a suspended node.

Why sysfs and not `rtcwake`, nor the RTC ioctls: `rtcwake` is a command line to spawn and parse; the ioctl interface
(`/dev/rtcN`, RTC_WKALM_SET) has no library in Debian that covers it and needs the ctypes structures by hand, while
`wakealarm` is the kernel's documented ABI (Documentation/ABI/testing/sysfs-class-rtc) and is what rtcwake itself uses
in its default mode. Both reach the same driver call.

**Time zone.** `wakealarm` takes *epoch seconds* and the kernel turns them into a broken-down time with plain
`rtc_time64_to_tm`, i.e. UTC, whatever `/etc/adjtime` says: the kernel has no notion of a "local" RTC. That setting only
matters to userspace (hwclock, systemd, installers), which decides whether the hardware holds UTC or local wall time.
If the hardware holds LOCAL time (a dual boot with Windows) the kernel still reads it as if it were UTC, so
`since_epoch` is off from the real epoch by the UTC offset, and an alarm has to be written in that same shifted frame.
Rather than trusting the adjtime flag alone we MEASURE the shift (RTC reading minus system clock) and add it to the
alarm; the flag is only used to say what shift is plausible, and a clock that is neither on UTC nor on local time (or
that drifted by minutes) is refused instead of being trusted to wake the node at a wrong time.
"""
import errno
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import time

SYSFS = Path('/sys/class/rtc')
ADJTIME = Path('/etc/adjtime')
# A UTC RTC is allowed to have drifted by this much from the system clock before we stop believing it.
MAX_DRIFT = 300


class SuspendRefused(Exception):
    """Suspending (or arming the wake-up) is not possible; the message is the reason, shown to the user as is."""


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def rtc_is_local(adjtime: Path = ADJTIME) -> bool:
    """Third line of /etc/adjtime: 'UTC' or 'LOCAL'. A missing file means UTC, as for hwclock."""
    lines = (_read(adjtime) or '').splitlines()
    return len(lines) >= 3 and lines[2].strip().upper() == 'LOCAL'


def local_utc_offset(now: float) -> int:
    return int(datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds())


@dataclass
class Rtc:
    path: Path
    shift: int          # what the RTC reads minus the real epoch (0 for a UTC RTC, the UTC offset for a local one)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def alarm_file(self) -> Path:
        return self.path / 'wakealarm'

    def alarm_for(self, wake_epoch: float) -> int:
        return int(round(wake_epoch)) + self.shift

    def current_alarm(self) -> Optional[int]:
        """The pending alarm in the RTC's own frame, or None (the file is empty when no alarm is set)."""
        text = _read(self.alarm_file)
        return (int(text) or None) if text and text.isdigit() else None


def _rtc_epoch(path: Path) -> Optional[int]:
    text = _read(path / 'since_epoch')
    if text and text.isdigit():
        return int(text)
    date, hms = _read(path / 'date'), _read(path / 'time')      # kernels before 3.x have no since_epoch
    try:
        return int(datetime.strptime(f'{date} {hms}', '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _examine(path: Path, now: float, local_rtc: bool) -> Rtc:
    """Raises SuspendRefused with the reason if this RTC cannot be used."""
    if not path.joinpath('wakealarm').exists():
        raise SuspendRefused(f'{path.name} has no wake-up alarm: this clock cannot wake the machine')
    wakeup = _read(path / 'device' / 'power' / 'wakeup')
    if wakeup == 'disabled':
        raise SuspendRefused(f'{path.name} is not allowed to wake the machine '
                             f'(/sys/class/rtc/{path.name}/device/power/wakeup is disabled)')
    epoch = _rtc_epoch(path)
    if epoch is None:
        raise SuspendRefused(f'Cannot read the time of {path.name}')
    shift = epoch - round(now)
    expected = local_utc_offset(now) if local_rtc else 0
    if abs(shift - expected) > MAX_DRIFT:
        kind = 'local time' if local_rtc else 'UTC'
        raise SuspendRefused(
            f'{path.name} reads {shift:+d} s from the system clock, but /etc/adjtime says it holds {kind} '
            f'(expected {expected:+d} s): its time is wrong or /etc/adjtime is, a wake-up could not be trusted')
    return Rtc(path, shift)


def find_rtc(sysfs: Optional[Path] = None, adjtime: Optional[Path] = None, now: Optional[float] = None) -> Rtc:
    """The first RTC that can wake the machine. When none can, the reason of the first one is reported (rtc0 is the one
    that matters on a PC), or that there is no RTC at all."""
    now = time.time() if now is None else now
    sysfs, adjtime = sysfs or SYSFS, adjtime or ADJTIME
    try:
        candidates = sorted(p for p in sysfs.iterdir() if p.name.startswith('rtc'))
    except OSError:
        candidates = []
    if not candidates:
        raise SuspendRefused('This machine has no real-time clock (no /sys/class/rtc/rtc*): it cannot wake itself up')
    local_rtc = rtc_is_local(adjtime)
    reasons = []
    for path in candidates:
        try:
            return _examine(path, now, local_rtc)
        except SuspendRefused as exc:
            reasons.append(str(exc))
    raise SuspendRefused(reasons[0])


def clear(rtc: Rtc):
    try:
        rtc.alarm_file.write_text('0\n')
    except OSError as exc:
        raise SuspendRefused(f'Cannot clear the alarm of {rtc.name}: {exc}') from exc


def arm(rtc: Rtc, wake_epoch: float, now: Optional[float] = None) -> int:
    """Sets the alarm (clearing a previous one first: the kernel refuses to overwrite a pending one with EBUSY) and
    reads it back. Returns the alarm in the RTC's frame; raises SuspendRefused, leaving no alarm behind, on any doubt."""
    now = time.time() if now is None else now
    alarm = rtc.alarm_for(wake_epoch)
    if alarm <= round(now) + rtc.shift:
        raise SuspendRefused('The wake-up time is not in the future')
    clear(rtc)
    try:
        rtc.alarm_file.write_text(f'{alarm}\n')
    except OSError as exc:
        # EINVAL is what the kernel answers for an alarm in the past or one this RTC cannot hold (too far away)
        clear_quietly(rtc)
        ahead = wake_epoch - now
        # the errno name, not strerror(): that one is translated to the language of the system
        reason = errno.errorcode.get(exc.errno, str(exc.errno)) if exc.errno else str(exc)
        hint = (' Many real-time clocks can only hold an alarm less than 24 hours ahead (the CMOS RTC of the test VM '
                'refuses anything a day or more away); schedule an earlier wake-up.' if ahead >= 20 * 3600 else '')
        raise SuspendRefused(f'{rtc.name} refused a wake-up alarm {ahead / 3600:.0f} hours ahead ({reason}).{hint}') from exc
    if rtc.current_alarm() != alarm:
        clear_quietly(rtc)
        raise SuspendRefused(f'{rtc.name} did not keep the wake-up alarm (wrote {alarm}, read back '
                             f'{rtc.current_alarm()})')
    return alarm


def clear_quietly(rtc: Rtc):
    try:
        clear(rtc)
    except SuspendRefused:
        pass


def check_reach(rtc: Rtc, seconds: float, now: Optional[float] = None):
    """Whether this RTC accepts an alarm `seconds` ahead: tries it and clears it again. Used when a suspend timer is
    saved, so that a clock limited to 24 hours is found out then and not at night. An alarm already pending is left
    alone and nothing is tested (it would be overwritten)."""
    now = time.time() if now is None else now
    if rtc.current_alarm() is not None:
        return
    arm(rtc, now + seconds, now)
    clear(rtc)
