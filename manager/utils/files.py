__all__ = [
    "get_file_info",
    "get_dominant_color",
    "human_readable_size"
]



from os.path import basename, getsize, isfile, join
from typing import cast

from PIL import Image
from pymediainfo import MediaInfo, Track

from .configuration import Config


def human_readable_size(size, decimal_places=2):
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{unit}" # type: ignore

def get_file_info(b, *f, def_dur = Config.def_duration):
    dur = None
    dur_s = def_dur
    if isfile(f := join(b, *f)):
        for track in cast(list[Track], MediaInfo.parse(f).tracks): # type: ignore
            track_data = track.to_data()
            if 'duration' in track_data:
                dur = track_data.get('other_duration', [None])[0]
                dur_s = round(int(track_data['duration'])/1000)
                break
    return {
        'filename': basename(f), 'duration': dur, 'duration_s': dur_s,
        'size': human_readable_size(getsize(f))
    }

def get_dominant_color(pil_img, palette_size=16): # https://stackoverflow.com/a/61730849/9655651
    # Resize image to speed up processing
    img = pil_img.copy()
    img.thumbnail((100, 100))
    # Reduce colors (uses k-means internally)
    paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)
    # Find the color that occurs most often
    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index*3:palette_index*3+3]
    return hex((dominant_color[0]<<16) + (dominant_color[1]<<8) + dominant_color[2])
