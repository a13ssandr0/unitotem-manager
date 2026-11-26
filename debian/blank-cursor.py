#!/usr/bin/python3
"""Writes the cursor theme `unitotem-blank` (one transparent 1x1 pixel per cursor name) into the directory given as argument.

The compositor draws its own pointer at the top-left from the moment it starts until a client takes over; it is selected for
the compositor unit only (XCURSOR_THEME), so clients keep their own cursors.
"""
import os
import struct
import sys

NAMES = ("default", "left_ptr", "arrow", "top_left_arrow")
SIZE = 24
TYPE_IMAGE = 0xFFFD0002


def xcursor():
    image = struct.pack("<9I", 36, TYPE_IMAGE, SIZE, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
    header = struct.pack("<4s3I", b"Xcur", 16, 0x10000, 1)
    toc = struct.pack("<3I", TYPE_IMAGE, SIZE, 16 + 12)
    return header + toc + image


def main(root):
    cursors = os.path.join(root, "cursors")
    os.makedirs(cursors, exist_ok=True)
    with open(os.path.join(root, "index.theme"), "w") as f:
        f.write("[Icon Theme]\nName=unitotem-blank\nComment=Transparent pointer\n")
    with open(os.path.join(cursors, NAMES[0]), "wb") as f:
        f.write(xcursor())
    for name in NAMES[1:]:
        link = os.path.join(cursors, name)
        if not os.path.lexists(link):
            os.symlink(NAMES[0], link)


if __name__ == "__main__":
    main(sys.argv[1])
