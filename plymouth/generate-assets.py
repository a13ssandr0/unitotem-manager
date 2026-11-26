#!/usr/bin/env python3
"""Generate the assets of the Plymouth "unitotem" theme (unitotem/unitotem.script).

Outputs, written to unitotem/:
  - wordmark-<width>.png and spinner-<width>.png, pre-rendered from
    NotoSans.ttf (Bold) and the layout of
    manager/plugins/unitotem_viewer/static/boot-screen.html. The wordmark is
    drawn once here so the Plymouth splash and the viewer's boot screen show
    the same picture.
  - NotoSans-subset.ttf, a static Regular instance (wght=400, wdth=100) of
    NotoSans.ttf, subset to U+0020-007E, without hinting. The theme draws its
    text lines with Image.Text() using this font, which replaces
    /usr/share/fonts/Plymouth.ttf in the initramfs. The image ships the
    plymouth-label package (freetype plugin) that provides Image.Text().

Usage:
  generate-assets.py              write the PNGs and the font
  generate-assets.py --font-only  write only NotoSans-subset.ttf

The font step needs fontTools (python3-fonttools); it is imported lazily.

All geometry ratios below were measured by rendering boot-screen.html in
headless Chrome and reading its computed CSS boxes (see the plan/commit that
introduced this script for the full derivation). They are expressed relative
to the wordmark's em size F:
  - total text advance width ("UniT" + 2*nbsp + "tem")   = 4.808 F
  - ring outer diameter                                   = 0.600 F
  - ring stroke width                                     = 0.130 F
  - ring center X, from the wordmark's left edge           = 2.490 F
  - ring center Y, above the text baseline                 = 0.288 F
  - text baseline, below the top of the glyph image        = ascent (from
    the font's hhea/OS2 metrics, which agree exactly for NotoSans.ttf)
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = REPO_ROOT / "manager" / "plugins" / "unitotem_viewer" / "static" / "NotoSans.ttf"
OUT_DIR = Path(__file__).resolve().parent / "unitotem"
SUBSET_FONT_PATH = OUT_DIR / "NotoSans-subset.ttf"

# Screen-width tiers the theme picks from at runtime (unitotem.script scales
# up to at most 1.5x between tiers, which we verified is visually lossless
# for this artwork even at Plymouth's naive bilinear Image.Scale).
TIERS = [1280, 1920, 2560, 3840]

# em = content_width / 8 reproduces the wordmark's current on-screen size
# exactly at 1920px (F=240, matching today's boot-screen.html) while keeping
# it at ~60% of the screen width at every other resolution.
EM_RATIO = 1 / 8

TEXT = "UniT  tem"
RING_DIAMETER_RATIO = 0.6
RING_STROKE_RATIO = 0.13
RING_SUPERSAMPLE = 8

# unitotem.script rotates the ring once per frame with Plymouth's own
# Image.Rotate(), which resamples with plain bilinear interpolation (no area
# filter). At the ring's true on-screen pixel size that leaves too few source
# pixels across the anti-aliased edge, so the rotated result visibly facets
# into a rough polygon instead of a smooth circle (confirmed on real captured
# Plymouth boot frames). Generating the ring at twice the final size forces
# Plymouth's own Image.Scale() to do a genuine 2x downsample (averaging two
# source pixels per destination pixel) before every rotation, widening the AA
# band enough that the subsequent rotate no longer visibly facets. The
# wordmark doesn't need this - it's drawn once and never rotated.
SPINNER_OVERSAMPLE = 2

WHITE = (255, 255, 255, 255)
ORANGE = (255, 61, 0, 255)


def make_subset_font() -> None:
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError:
        sys.exit("fontTools is missing: install python3-fonttools "
                 "(or pip install fonttools) to generate NotoSans-subset.ttf")

    # recalcTimestamp=False keeps head.modified from the source font, so the
    # output bytes are the same on every run.
    font = TTFont(str(FONT_PATH), recalcTimestamp=False)
    static = instancer.instantiateVariableFont(
        font, {"wght": 400, "wdth": 100}, inplace=False)

    options = subset.Options()
    options.hinting = False
    # Keep only the ligature and kerning features and all name records
    # (the OFL requires the copyright and licence notices to stay in the font).
    options.layout_features = ["kern", "liga"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=range(0x20, 0x7F))
    subsetter.subset(static)

    static.recalcTimestamp = False
    static.save(str(SUBSET_FONT_PATH))
    print(f"font: {SUBSET_FONT_PATH} ({SUBSET_FONT_PATH.stat().st_size} bytes)")


def load_font(size: int):
    from PIL import ImageFont

    font = ImageFont.truetype(str(FONT_PATH), size)
    try:
        font.set_variation_by_name("Bold")
    except OSError as exc:
        sys.exit(f"NotoSans.ttf has no 'Bold' named instance: {exc}")
    return font


def make_wordmark(em: int):
    from PIL import Image, ImageDraw

    font = load_font(em)
    width = round(font.getlength(TEXT))
    ascent, descent = font.getmetrics()
    height = ascent + descent
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), TEXT, font=font, fill=WHITE)
    return im


def make_ring(em: float):
    from PIL import Image, ImageDraw

    diameter = round(em * RING_DIAMETER_RATIO)
    stroke = round(em * RING_STROKE_RATIO)
    ss = RING_SUPERSAMPLE
    d, st = diameter * ss, stroke * ss

    im = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse((0, 0, d - 1, d - 1), fill=WHITE)
    # PIL pieslice angles start at 3-o'clock and increase clockwise; -90
    # rebases them to the 12-o'clock-zero convention the CSS ring uses, so
    # 135..225 here draws the bottom quarter in the accent color.
    draw.pieslice((0, 0, d - 1, d - 1), 135 - 90, 225 - 90, fill=ORANGE)
    draw.ellipse((st, st, d - 1 - st, d - 1 - st), fill=(0, 0, 0, 0))
    return im.resize((diameter, diameter), Image.LANCZOS)


def make_images() -> None:
    from PIL import features

    if not features.check("raqm"):
        sys.exit("Pillow was built without Raqm text shaping; layout would "
                  "not match boot-screen.html's Chromium rendering.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for width in TIERS:
        em = round(width * EM_RATIO)
        wordmark = make_wordmark(em)
        ring = make_ring(em * SPINNER_OVERSAMPLE)
        wordmark.save(OUT_DIR / f"wordmark-{width}.png")
        ring.save(OUT_DIR / f"spinner-{width}.png")
        print(f"tier {width:5d}px: em={em:3d}  wordmark={wordmark.size}  "
              f"spinner={ring.size}")


def main() -> None:
    font_only = "--font-only" in sys.argv[1:]
    unknown = [a for a in sys.argv[1:] if a != "--font-only"]
    if unknown:
        sys.exit(f"unknown argument(s): {' '.join(unknown)}\n"
                 "usage: generate-assets.py [--font-only]")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_subset_font()
    if not font_only:
        make_images()


if __name__ == "__main__":
    main()
