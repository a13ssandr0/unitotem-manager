"""The caption under the wordmark of the boot splashes (plugins/unitotem_viewer/bootsplash.py)."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / 'plugins' / 'unitotem_viewer' / 'bootsplash.py'
spec = importlib.util.spec_from_file_location('bootsplash_caption_test', SOURCE)
bootsplash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootsplash)


def ink(image, top=0, bottom=None):
    """Bounding box (left, top, right, bottom) of the non-transparent pixels, optionally between two rows."""
    alpha = image.getchannel('A')
    if bottom is not None:
        cropped = alpha.crop((0, top, image.width, bottom))
        box = cropped.getbbox()
        return None if box is None else (box[0], box[1] + top, box[2], box[3] + top)
    return alpha.getbbox()


def line_boxes(width, index, text):
    """Ink box of one line drawn alone, in its own row of the caption (index 0 is line 1, 1 is line 2)."""
    lines = ['', '']
    lines[index] = text
    image, offset = bootsplash.caption(width, tuple(lines))
    return image, offset, ink(image)


@pytest.mark.parametrize('width, pt, px', [(1280, 14, 14 * 96 / 72), (1920, 21, 21 * 96 / 72), (3840, 43, 43 * 96 / 72)])
def test_caption_size_and_ink(width, pt, px):
    image, offset = bootsplash.caption(width, ('Slot A: UniTotem 2026.12', 'Starting viewer'))
    assert image is not None
    assert image.mode == 'RGBA'
    assert image.width == width
    assert offset == round(1.2 * px) + bootsplash.CAPTION_DY
    assert image.height > round(bootsplash.CAPTION_STEP * px)
    assert ink(image) is not None

    _, _, first = line_boxes(width, 0, 'Slot A: UniTotem 2026.12')
    _, _, second = line_boxes(width, 1, 'Starting viewer')
    assert first is not None and second is not None
    # line 1 above line 2
    assert first[1] < second[1]
    # the same text in both rows differs by exactly the step
    _, _, same_top = line_boxes(width, 0, 'Starting viewer')
    _, _, same_bottom = line_boxes(width, 1, 'Starting viewer')
    assert same_bottom[1] - same_top[1] == round(bootsplash.CAPTION_STEP * px)
    # both lines centred within 1 px of the image's centre
    for box in (first, second):
        assert abs((box[0] + box[2]) / 2 - width / 2) <= 1


def test_point_size_from_width():
    # PT = round(W / 90): 14, 21 and 43 for the three widths of the spec
    assert [round(w / bootsplash.FONT_WIDTH_PER_PT) for w in (1280, 1920, 3840)] == [14, 21, 43]


def test_alpha_of_lines():
    image, _ = bootsplash.caption(1920, ('AAAA', 'BBBB'))
    _, top, _, _ = ink(image)
    line1 = image.getchannel('A').crop((0, 0, image.width, round(bootsplash.CAPTION_STEP * 21 * 96 / 72))).getextrema()
    assert line1[1] == round(255 * 0.8)
    line2 = image.getchannel('A').crop((0, round(bootsplash.CAPTION_STEP * 21 * 96 / 72), image.width, image.height)).getextrema()
    assert line2[1] == round(255 * 0.5)


def test_non_ascii_is_replaced_by_question_mark():
    assert bootsplash._ascii('Caffè ☃') == 'Caff? ?'
    assert bootsplash._ascii('ok\tline') == 'ok?line'


def test_caption_truncates_to_48_characters():
    long = 'x' * 60
    assert bootsplash._ascii(long)[:bootsplash.CAPTION_CHARS] == 'x' * 48
    image_long, _ = bootsplash.caption(1920, (long, ''))
    image_48, _ = bootsplash.caption(1920, ('x' * 48, ''))
    assert image_long.tobytes() == image_48.tobytes()


@pytest.fixture
def boot_files(tmp_path, monkeypatch):
    cmdline = tmp_path / 'cmdline'
    version = tmp_path / 'release-version'
    monkeypatch.setattr(bootsplash, 'CMDLINE_PATH', cmdline)
    monkeypatch.setattr(bootsplash, 'RELEASE_VERSION_PATH', version)
    return cmdline, version


@pytest.mark.parametrize('cmdline, release, label', [
    ('quiet unitotem.slot=a splash', '2026.12\n', 'Slot A: UniTotem 2026.12'),
    ('unitotem.slot=b', '2026.12', 'Slot B: UniTotem 2026.12'),
    ('quiet splash', '2026.12\n', 'UniTotem 2026.12'),
    ('unitotem.slot=a', None, 'Slot A: UniTotem'),
    ('quiet', None, 'UniTotem'),
    ('unitotem.slot=c', '2026.12~rc1+x:y-z', 'UniTotem 2026.12~rc1+x:y-z'),
    ('unitotem.slot=a', 'bad version!\n', 'Slot A: UniTotem'),
    ('quiet', '', 'UniTotem'),
])
def test_boot_label(boot_files, cmdline, release, label):
    cmdline_file, version_file = boot_files
    cmdline_file.write_text(cmdline + '\n')
    if release is not None:
        version_file.write_text(release)
    assert bootsplash.boot_label() == label


def test_boot_label_missing_files_never_raises(boot_files):
    assert bootsplash.boot_label() == 'UniTotem'


def test_missing_font_draws_no_text(monkeypatch):
    monkeypatch.setattr(bootsplash, 'FONT', Path('/nonexistent/NotoSans.ttf'))
    image, offset = bootsplash.caption(1920, ('Slot A: UniTotem 2026.12', 'Starting viewer'))
    assert image is None
    assert offset == round(1.2 * 21 * 96 / 72)
    image, offset = bootsplash.viewer_caption(1920)
    assert image is None


def test_viewer_caption_uses_boot_label(boot_files, monkeypatch):
    cmdline_file, version_file = boot_files
    cmdline_file.write_text('unitotem.slot=b\n')
    version_file.write_text('2026.12\n')
    image, _ = bootsplash.viewer_caption(1920)
    expected, _ = bootsplash.caption(1920, ('Slot B: UniTotem 2026.12', 'Starting viewer'))
    assert image.tobytes() == expected.tobytes()
