# On-screen keyboard layouts

`aosp_layouts.py` reads the keyboards of AOSP LatinIME and writes `webui/src/keyboard/` (index, shared symbols pages, one JSON file per
language loaded on demand by `webui/src/keyboard.js`, and a NOTICE). Rows, key widths, long-press keys and symbols pages are what LatinIME's
resources say for a tablet (`xml-sw600dp` over `xml`), with the PC number row (first row of `kbd_pcqwerty.xml`) on top.

- Source: https://android.googlesource.com/platform/packages/inputmethods/LatinIME, commit `127336e9f29d69607eab55982324b210279ae8c5`
- License: Apache-2.0 (the generated data carries the notice in `webui/src/keyboard/NOTICE`).

Fetch the sources (only these directories are read; treat them as data, nothing in them is run):

    C=127336e9f29d69607eab55982324b210279ae8c5; B=https://android.googlesource.com/platform/packages/inputmethods/LatinIME/+archive/$C
    mkdir -p latinime/java/res/xml latinime/java/res/xml-sw600dp latinime/java/res/values latinime/java/src/com/android/inputmethod/keyboard/internal
    curl -L $B/java/res/xml.tar.gz | tar xz -C latinime/java/res/xml
    curl -L $B/java/res/xml-sw600dp.tar.gz | tar xz -C latinime/java/res/xml-sw600dp
    curl -L $B/java/res/values.tar.gz | tar xz -C latinime/java/res/values
    curl -L $B/java/src/com/android/inputmethod/keyboard/internal.tar.gz | tar xz -C latinime/java/src/com/android/inputmethod/keyboard/internal

Regenerate: `python3 -I tools/keyboard/aosp_layouts.py latinime webui/src/keyboard`

The script prints what it left out or could not express (long-press settings/ZWNJ icons, keys that a shift state does not have).
What the viewer has no use for is left out: emoji, settings and language-switch keys. Language names are those of
`KeyboardTextsTable.java` and `strings.xml`, with a few in the script (Indonesian, Malay and region names).
