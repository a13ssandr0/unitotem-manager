"""Usage of an unmounted ext filesystem read from its superblock must match what a mounted one shows (statvfs, like df): the metadata
overhead is left out of the size and of the used space, so the two root slots of an A/B device with the same content show the same numbers."""
import io
from struct import pack_into

import pytest

from utils.system.lsblk import _probe_ext

MIB = 1 << 20


def superblock(blocks, free, log_block=2, overhead=0, log_cluster=None, big=True, magic=0xEF53):
    """a 1024-byte ext4 superblock with the fields the probe reads"""
    sb = bytearray(1024)
    pack_into('<I', sb, 4, blocks & 0xFFFFFFFF)
    pack_into('<I', sb, 12, free & 0xFFFFFFFF)
    pack_into('<I', sb, 24, log_block)                         # block size = 1024 << log_block
    pack_into('<I', sb, 28, log_block if log_cluster is None else log_cluster)
    pack_into('<H', sb, 56, magic)
    if big:
        pack_into('<I', sb, 96, 0x80)                          # INCOMPAT_64BIT
        pack_into('<I', sb, 0x150, blocks >> 32)
        pack_into('<I', sb, 0x158, free >> 32)
    pack_into('<I', sb, 0x248, overhead)                       # s_overhead_clusters
    return bytes(1024) + bytes(sb)


def probe(**kw):
    return _probe_ext(io.BytesIO(superblock(**kw)))


def test_the_g5_root_slots_measured_on_the_device():
    """the real superblock of both ROOT_A and ROOT_B of the HP 250 G5: 1310720 blocks, 271605 free, overhead 42072 clusters; the mounted root
    reported 1268648 blocks (= 1310720 - 42072) of 4096 bytes and 271605 free through statvfs"""
    total, used = probe(blocks=1310720, free=271605, overhead=42072)
    assert total == 1268648 * 4096
    assert used == (1268648 - 271605) * 4096
    assert (total // MIB, used // MIB) == (4955, 3894)


def test_the_two_slots_with_the_same_content_show_the_same_numbers():
    a = probe(blocks=1310720, free=271605, overhead=42072)
    b = probe(blocks=1310720, free=271605, overhead=42072)
    assert a == b


def test_a_filesystem_without_a_stored_overhead_keeps_the_plain_numbers():
    total, used = probe(blocks=262144, free=100000)
    assert (total, used) == (262144 * 4096, 162144 * 4096)


@pytest.mark.parametrize('overhead', [0xFFFFFFFF, 262144, 200000])
def test_an_overhead_that_does_not_fit_is_ignored(overhead):
    """larger than the used space or the whole filesystem: a corrupt or unrelated field, never a negative or wrong size"""
    total, used = probe(blocks=262144, free=100000, overhead=overhead)
    assert (total, used) == (262144 * 4096, 162144 * 4096)


def test_bigalloc_overhead_is_counted_in_clusters():
    total, used = probe(blocks=262144, free=100000, log_block=2, log_cluster=4, overhead=100)   # cluster = 16 KiB
    assert total == 262144 * 4096 - 100 * 16384
    assert used == 162144 * 4096 - 100 * 16384


def test_a_32_bit_filesystem_is_read_the_same_way():
    total, used = probe(blocks=100000, free=40000, overhead=1000, big=False)
    assert (total, used) == (99000 * 4096, 59000 * 4096)


def test_a_block_size_of_1024_bytes():
    total, used = probe(blocks=102400, free=50000, log_block=0, overhead=2000)
    assert (total, used) == ((102400 - 2000) * 1024, (52400 - 2000) * 1024)


def test_not_an_ext_filesystem_is_not_probed():
    assert _probe_ext(io.BytesIO(superblock(blocks=1000, free=10, magic=0x1234))) is None
    assert _probe_ext(io.BytesIO(b'\x00' * 100)) is None
