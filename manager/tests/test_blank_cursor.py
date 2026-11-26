"""The transparent cursor theme of the compositor (debian/blank-cursor.py): valid Xcursor file, every name present."""
import importlib.util
import os
import struct

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "debian", "blank-cursor.py")
if not os.path.exists(SCRIPT):          # autopkgtest copies manager/tests only
    pytest.skip("debian/blank-cursor.py not present", allow_module_level=True)
SPEC = importlib.util.spec_from_file_location("blank_cursor", SCRIPT)
blank = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(blank)


def test_theme_files(tmp_path):
    blank.main(str(tmp_path))
    assert (tmp_path / "index.theme").read_text().startswith("[Icon Theme]\nName=unitotem-blank")
    for name in blank.NAMES:
        assert (tmp_path / "cursors" / name).read_bytes() == (tmp_path / "cursors" / "default").read_bytes()
    assert not os.path.islink(tmp_path / "cursors" / "default")
    assert all(os.path.islink(tmp_path / "cursors" / n) for n in blank.NAMES[1:])


def test_xcursor_is_one_transparent_pixel(tmp_path):
    blank.main(str(tmp_path))
    data = (tmp_path / "cursors" / "default").read_bytes()
    magic, header, version, ntoc = struct.unpack_from("<4s3I", data, 0)
    assert (magic, header, ntoc) == (b"Xcur", 16, 1)
    ctype, subtype, position = struct.unpack_from("<3I", data, 16)
    assert ctype == 0xFFFD0002 and position == 28
    chunk, ctype2, sub2, ver, width, height, xhot, yhot, delay = struct.unpack_from("<9I", data, position)
    assert (chunk, ctype2, width, height) == (36, ctype, 1, 1)
    assert struct.unpack_from("<I", data, position + 36)[0] == 0 and len(data) == position + 40


def test_rerun_is_idempotent(tmp_path):
    blank.main(str(tmp_path))
    blank.main(str(tmp_path))
