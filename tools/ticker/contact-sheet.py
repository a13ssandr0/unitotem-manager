#!/usr/bin/env python3
"""Labelled montage of the screenshots of a venus-run.py output directory (Pillow): contact-sheet.py DIR"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

d = sys.argv[1].rstrip('/') + '/'
files = sorted(f for f in glob.glob(d + '*.png') if not os.path.basename(f).startswith(('screenshot', 'contact', 'crop')))
labels = {'m01': '1 row, overlay, top', 'm02': '1 row, overlay, bottom', 'm03': '1 row, reserve, top', 'm04': '1 row, reserve, bottom',
          'm05': '2 rows, overlay, top', 'm06': '2 rows, overlay, bottom', 'm07': '2 rows, reserve, top', 'm08': '2 rows, reserve, bottom'}
order = [f for f in files if os.path.basename(f).startswith('m')] + [f for f in files if not os.path.basename(f).startswith('m')]
W, H, cols, lab = 640, 360, 5, 34
rows = (len(order) + cols - 1) // cols
sheet = Image.new('RGB', (cols * W, rows * (H + lab)), (30, 30, 30))
draw = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 20)
except OSError:
    font = ImageFont.load_default()
for i, f in enumerate(order):
    name = os.path.basename(f)[:-4]
    x, y = (i % cols) * W, (i // cols) * (H + lab)
    sheet.paste(Image.open(f).convert('RGB').resize((W, H), Image.LANCZOS), (x, y + lab))
    draw.text((x + 8, y + 6), labels.get(name[:3], name)[:60], fill=(255, 255, 255), font=font)
sheet.save(d + 'contact-sheet.png')
print(sheet.size, len(order))
