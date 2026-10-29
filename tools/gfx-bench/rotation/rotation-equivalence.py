#!/usr/bin/env python3
"""
Which output transform shows the same picture as turning/mirroring the content in the page, by pixels. Needs a board whose HDMI
output is visible from this PC (here: OBS's virtual camera on the dev PC).

    .venv/bin/python tools/gfx-bench/rotation/rotation-equivalence.py --host 192.168.1.173 --camera /dev/video4 --out DIR

For every content orientation 0..3 and flip 0..2 it grabs the screen with the page turning the content, then with the content
untouched and each of the 8 transforms, and prints the transform whose picture is closest (and how much closer than the runner-up).
"""
import argparse
import asyncio
import json
import os
import subprocess

from PIL import Image, ImageChops, ImageStat

import rotlib

TRANSFORMS = ['normal', '90', '180', '270', 'flipped', 'flipped-90', 'flipped-180', 'flipped-270']


def grab(camera, path):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'v4l2', '-i', camera, '-frames:v', '4', '-update', '1', path], check=True, timeout=30)
    return Image.open(path).convert('L').resize((96, 54))


def distance(a, b):
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--camera', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    async with rotlib.Board(args.host, 'corners') as board:
        pictures = {}
        board.set_transform('normal')
        await board.set_content(0, 0)
        await asyncio.sleep(4)
        for t in TRANSFORMS:
            board.set_transform(t)
            await asyncio.sleep(3)
            pictures[t] = grab(args.camera, os.path.join(args.out, f't-{t}.png'))
        board.set_transform('normal')
        table = {}
        for r in range(4):
            for f in range(3):
                await board.set_content(r, f)
                await asyncio.sleep(5)       # the page animates the change
                ref = grab(args.camera, os.path.join(args.out, f'page-r{r}-f{f}.png'))
                ranked = sorted((distance(ref, img), t) for t, img in pictures.items())
                table[f'r{r}f{f}'] = {'transform': ranked[0][1], 'distance': round(ranked[0][0], 2), 'runner_up': [ranked[1][1], round(ranked[1][0], 2)]}
                print(f'page r={r} flip={f} -> transform {ranked[0][1]} (distance {ranked[0][0]:.2f}, next {ranked[1][1]} {ranked[1][0]:.2f})', flush=True)
        json.dump(table, open(os.path.join(args.out, 'table.json'), 'w'), indent=1)


asyncio.run(main())
