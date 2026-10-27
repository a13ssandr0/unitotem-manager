# Gboard layouts

`gboard_layouts.py` reads the keyboards of Gboard from its APK and writes `webui/src/gboard/` (index, shared symbols pages and one
JSON file per language, loaded on demand by `webui/src/keyboard.js`). Nothing is reconstructed: rows, key weights, popups and
symbols pages are what Gboard's own resources say for a large tablet with the number row and symbols on keys.

1. Get the APK (a browser User-Agent is needed): `https://d.apkpure.net/b/APK/com.google.android.inputmethod.latin?version=latest`
2. Decode it (resources only): `docker run --rm -v "$PWD":/w -w /w <image with apktool> apktool d -s -f gb.apk -o dec`
   (any apktool 2.x works; `dec/res/xml`, `dec/res/layout`, `dec/res/values` are what is read).
3. `python3 -I tools/gboard/gboard_layouts.py dec webui/src/gboard`

How the format works is in the docstring of the script. Settings the script does not model count as off, except those it sets on
purpose (`context()`): tablet/large, number row, secondary symbols on keys, comma and period keys.
