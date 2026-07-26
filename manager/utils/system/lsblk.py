__all__ = ["lsblk", "start_fs_usage_cache"]



import select
from os import major, minor, stat, statvfs
from pathlib import Path
from struct import unpack_from
from threading import Thread
from typing import Optional

from loguru import logger
from natsort import natsorted
from pydantic import BaseModel, Field, field_validator, ConfigDict


_SYS_BLOCK = Path('/sys/block')
_EXCLUDED_MAJORS = {7}  # loop devices, same as the old `lsblk -e7`


class Blk(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name:str                       = Field(description='device name')
    rm:bool                        = Field(description='removable device')
    size:int                       = Field(description='size of the device')
    ro:bool                        = Field(description='read-only device')
    type:str                       = Field(description='device type')
    hotplug:bool                   = Field(description='removable or hotplug device (usb, pcmcia, ...)')
    mountpoint:Optional[str]       = Field(description='where the device is mounted')
    fssize:Optional[int]           = Field(description='filesystem size')
    fstype:Optional[str]           = Field(description='filesystem type')
    fsused:Optional[int]           = Field(description='filesystem size used')
    fsuse_perc:Optional[int]       = Field(description='filesystem use percentage', alias='fsuse%')
    fsavail:Optional[int]          = Field(description='filesystem size available')
    children:'Optional[list[Blk]]' = []

    # noinspection PyNestedDecorators
    @field_validator('fsuse_perc', mode='before')
    @classmethod
    def validate_percentage(cls, v:str):
        if not v:
            return None
        return int(v.split('%')[0])



def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _parse_mountinfo(lines) -> dict[str, tuple[str, str]]:
    mounts: dict[str, tuple[str, str]] = {}
    try:
        for line in lines:
            fields = line.split()
            sep = fields.index('-')
            mountpoint = fields[4].replace('\\040', ' ').replace('\\011', '\t')
            mounts.setdefault(fields[2], (mountpoint, fields[sep + 1]))
    except (ValueError, IndexError):
        pass
    return mounts


def _mounts_by_devno() -> dict[str, tuple[str, str]]:
    """major:minor → (mountpoint, fstype). Resolving through mountinfo device
    numbers also covers aliases like /dev/root that /proc/mounts may report."""
    try:
        with open('/proc/self/mountinfo') as f:
            return _parse_mountinfo(f)
    except OSError:
        return {}


def _udev_fstype(devno: str) -> Optional[str]:
    """filesystem type of a (possibly unmounted) device from the udev cache,
    which is how lsblk itself knows it without superblock-probing as root"""
    data = _read(Path('/run/udev/data') / f'b{devno}')
    if data:
        for line in data.splitlines():
            if line.startswith('E:ID_FS_TYPE='):
                return line.split('=', 1)[1] or None
    return None


def _swap_devnos() -> set[str]:
    devnos = set()
    try:
        with open('/proc/swaps') as f:
            next(f)  # header
            for line in f:
                st = stat(line.split()[0])
                devnos.add(f'{major(st.st_rdev)}:{minor(st.st_rdev)}')
    except (OSError, StopIteration, IndexError):
        pass
    return devnos


# ── usage of unmounted filesystems ────────────────────────────────────────
# GParted-style: read used/total straight from the on-disk structures
# (ext superblock, FAT boot sector + FSInfo). Devices are only touched at
# program startup and when a filesystem gets unmounted, so spun-down
# rotational disks are never woken up periodically. While a filesystem is
# mounted the live statvfs numbers are used instead of this cache.

_FS_USAGE_CACHE: dict[str, tuple[int, int]] = {}  # devno → (fssize, fsused)
_fs_watcher: Optional[Thread] = None


def _probe_ext(f) -> Optional[tuple[int, int]]:
    f.seek(1024)
    sb = f.read(1024)
    if len(sb) < 1024 or unpack_from('<H', sb, 56)[0] != 0xEF53:
        return None
    blocks = unpack_from('<I', sb, 4)[0]
    free = unpack_from('<I', sb, 12)[0]
    block_size = 1024 << unpack_from('<I', sb, 24)[0]
    if unpack_from('<I', sb, 96)[0] & 0x80:  # INCOMPAT_64BIT
        blocks |= unpack_from('<I', sb, 0x150)[0] << 32
        free |= unpack_from('<I', sb, 0x158)[0] << 32
    return blocks * block_size, (blocks - free) * block_size


def _probe_fat(f) -> Optional[tuple[int, int]]:
    bs = f.read(512)
    if len(bs) < 512 or bs[510:512] != b'\x55\xaa':
        return None
    bps = unpack_from('<H', bs, 11)[0]
    spc = bs[13]
    reserved = unpack_from('<H', bs, 14)[0]
    nfats = bs[16]
    root_entries = unpack_from('<H', bs, 17)[0]
    total = unpack_from('<H', bs, 19)[0] or unpack_from('<I', bs, 32)[0]
    fatsz = unpack_from('<H', bs, 22)[0] or unpack_from('<I', bs, 36)[0]
    if not (bps and spc and total and fatsz):
        return None
    root_dir_sectors = (root_entries * 32 + bps - 1) // bps
    clusters = (total - reserved - nfats * fatsz - root_dir_sectors) // spc
    if clusters < 4085:  # FAT12: 12-bit packed entries, not worth parsing
        return None
    fat32 = clusters >= 65525
    free = None
    if fat32:
        fsinfo_sec = unpack_from('<H', bs, 48)[0]
        if 0 < fsinfo_sec < 0xFFFF:
            f.seek(fsinfo_sec * bps)
            info = f.read(512)
            if len(info) == 512 and info[:4] == b'RRaA' and info[484:488] == b'rrAa':
                count = unpack_from('<I', info, 488)[0]
                if count <= clusters:  # 0xFFFFFFFF = unknown
                    free = count
    if free is None:  # FAT16, or FAT32 with stale/missing FSInfo: count in the FAT
        entry_size = 4 if fat32 else 2
        want = (clusters + 2) * entry_size
        if want > 64 * 1024 * 1024:  # implausible FAT size: corrupt boot sector
            return None
        f.seek(reserved * bps)
        data = f.read(want)
        # FAT entries are little-endian, as are all the platforms we run on;
        # truncate to a whole number of entries in case of a short read
        entries = memoryview(data[:len(data) - len(data) % entry_size]).cast('I' if fat32 else 'H')
        mask = 0x0FFFFFFF if fat32 else 0xFFFF
        free = sum(1 for i in range(2, min(len(entries), clusters + 2)) if entries[i] & mask == 0)
    cluster_size = spc * bps
    return clusters * cluster_size, (clusters - free) * cluster_size


_FS_PROBES = {'ext2': _probe_ext, 'ext3': _probe_ext, 'ext4': _probe_ext,
              'vfat': _probe_fat, 'msdos': _probe_fat}


def _sysfs_devices() -> dict[str, Path]:
    """devno → sysfs path for every non-excluded disk and partition"""
    devices = {}
    for disk_path in _SYS_BLOCK.iterdir():
        devno = _read(disk_path / 'dev')
        if not devno or int(devno.split(':')[0]) in _EXCLUDED_MAJORS:
            continue
        devices[devno] = disk_path
        for part in disk_path.iterdir():
            if (part / 'partition').is_file():
                devices[_read(part / 'dev') or ''] = part
    return devices


def _probe_devno(devno: str, sysfs_path: Path) -> str:
    """Probe one device into the cache. Returns 'ok', 'skip', 'denied' or 'error';
    never raises, so a single odd device cannot take the watcher down."""
    probe = _FS_PROBES.get(_udev_fstype(devno) or '')
    if not probe:
        return 'skip'
    device = Path('/dev') / sysfs_path.name
    try:
        with device.open('rb') as f:
            usage = probe(f)
    except PermissionError:
        logger.debug('Cannot probe {}: permission denied', device)
        return 'denied'
    except OSError as e:
        logger.debug('Cannot probe {}: {}', device, e)
        return 'error'
    except Exception as e:
        logger.warning('Unexpected error probing {}: {}', device, e)
        return 'error'
    if usage:
        _FS_USAGE_CACHE[devno] = usage
    return 'ok'


def _fs_usage_worker():
    with open('/proc/self/mountinfo') as mi:
        poller = select.poll()
        poller.register(mi.fileno(), select.POLLPRI | select.POLLERR)
        mounted = set(_parse_mountinfo(mi))
        swaps = _swap_devnos()
        # startup: the only moment every unmounted filesystem is read
        denied = 0
        for devno, sysfs_path in _sysfs_devices().items():
            if devno not in mounted and devno not in swaps:
                denied += _probe_devno(devno, sysfs_path) == 'denied'
        if denied:
            logger.warning(
                    '{} unmounted partitions are not readable: their disk usage will not be '
                    'shown. Reading them requires root or membership in the "disk" group.',
                    denied)
        while True:
            poller.poll()  # wakes up (only) when the mount table changes
            mi.seek(0)
            now = set(_parse_mountinfo(mi))
            for devno in now - mounted:
                # mounted: live statvfs takes over
                _FS_USAGE_CACHE.pop(devno, None)
            for devno in mounted - now:
                # unmounted: back to the cache, refreshed once while the disk
                # is certainly still spinning
                sysfs_path = _sysfs_devices().get(devno)
                if sysfs_path:
                    _probe_devno(devno, sysfs_path)
                else:
                    _FS_USAGE_CACHE.pop(devno, None)  # device is gone
            mounted = now


def _fs_usage_worker_safe():
    try:
        _fs_usage_worker()
    except Exception:
        logger.exception('Filesystem usage watcher stopped unexpectedly')


def start_fs_usage_cache():
    """Start the startup probe + mount watcher thread (call once at startup)"""
    global _fs_watcher
    if _fs_watcher is None or not _fs_watcher.is_alive():
        _fs_watcher = Thread(target=_fs_usage_worker_safe, name='fs_usage', daemon=True)
        _fs_watcher.start()


def _blk_dict(path: Path, dev_type: str, rm: bool, hotplug: bool,
              mounts: dict[str, tuple[str, str]], swaps: set[str]) -> dict:
    devno = _read(path / 'dev') or ''
    dev = {
        'name': path.name,
        'rm': rm,
        'size': int(_read(path / 'size') or 0) * 512,
        'ro': _read(path / 'ro') == '1',
        'type': dev_type,
        'hotplug': hotplug,
        'mountpoint': None,
        'fssize': None,
        'fstype': _udev_fstype(devno),
        'fsused': None,
        'fsuse%': None,
        'fsavail': None,
    }
    mount = mounts.get(devno)
    if mount:
        dev['mountpoint'] = mount[0]
        dev['fstype'] = dev['fstype'] or mount[1]
        try:
            st = statvfs(mount[0])
        except OSError:
            pass
        else:
            dev['fssize'] = st.f_frsize * st.f_blocks
            dev['fsavail'] = st.f_frsize * st.f_bavail
            dev['fsused'] = st.f_frsize * (st.f_blocks - st.f_bfree)
            if dev['fssize']:
                dev['fsuse%'] = f"{round(dev['fsused'] / dev['fssize'] * 100)}%"
    elif devno in swaps:
        dev['mountpoint'] = '[SWAP]'
    elif devno in _FS_USAGE_CACHE:
        dev['fssize'], dev['fsused'] = _FS_USAGE_CACHE[devno]
        dev['fsavail'] = dev['fssize'] - dev['fsused']
        if dev['fssize']:
            dev['fsuse%'] = f"{round(dev['fsused'] / dev['fssize'] * 100)}%"
    return dev


def lsblk():
    # reads sysfs/statvfs directly instead of spawning /usr/bin/lsblk:
    # info_loop used to pay the fork+exec (~10-30ms on a Pi) every 3 seconds
    mounts = _mounts_by_devno()
    swaps = _swap_devnos()
    devices = []
    for disk_path in natsorted(_SYS_BLOCK.iterdir(), key=lambda p: p.name):
        devno = _read(disk_path / 'dev')
        if not devno or int(devno.split(':')[0]) in _EXCLUDED_MAJORS:
            continue
        rm = _read(disk_path / 'removable') == '1'
        hotplug = rm or 'usb' in str(disk_path.resolve())
        disk = _blk_dict(disk_path, 'rom' if disk_path.name.startswith('sr') else 'disk',
                         rm, hotplug, mounts, swaps)
        disk['children'] = [
            # partitions have no 'removable' attribute in sysfs: inherit the disk's
            _blk_dict(part, 'part', rm, hotplug, mounts, swaps)
            for part in natsorted(disk_path.iterdir(), key=lambda p: p.name)
            if (part / 'partition').is_file()
        ]
        devices.append(Blk.model_validate(disk))
    return devices




# if __name__ == '__main__':
#     from subprocess import run, PIPE
#     from json import loads, dumps
#     # from utils.lsblk import Blk
#     blks = []
#     for dev in loads(run(['/usr/bin/lsblk', '-JMbe7', '-oNAME,RM,SIZE,RO,TYPE,HOTPLUG,MOUNTPOINT,FSSIZE,FSTYPE,FSUSED,FSUSE%,FSAVAIL'], stdout=PIPE).stdout.decode())['blockdevices']:
#         b= Blk.model_validate(dev)
#         blks.append(b.model_dump())
#     print(dumps(blks, indent=4))
