from math import ceil
from os.path import isfile, join, basename, getsize
from typing import Optional

from PIL import Image
from pydantic import BaseModel, ConfigDict
from pymediainfo import MediaInfo

from utils.units import human_readable_size


def get_file_info(b, *f):
    dur = None
    dur_s = None
    mime = None
    if isfile(f := join(b, *f)):
        for track in MediaInfo.parse(f).general_tracks:  # type: ignore
            track_data = track.to_data()
            mime = track.internet_media_type
            if 'duration' in track_data:
                dur = track_data.get('other_duration', [None])[0]
                dur_s = ceil(int(track_data['duration']) / 1000)
            break  # we only need the first general track
    return FileInfo(filename=basename(f), duration=dur, duration_s=dur_s,
                    size=human_readable_size(getsize(f)), mime=mime)


class FileInfo(BaseModel):
    model_config = ConfigDict(validate_assignment=True, frozen=True)
    filename: str
    duration: Optional[str] = None
    duration_s: Optional[int] = None
    size: str
    mime: Optional[str]


def get_dominant_color(pil_img: Image.Image, palette_size=16):  # https://stackoverflow.com/a/61730849/9655651
    # Resize image to speed up processing
    img = pil_img.copy()
    img.thumbnail((100, 100))
    # Reduce colors (uses k-means internally)
    paletted = img.convert('P', palette=Image.Palette.ADAPTIVE, colors=palette_size)
    # Find the color that occurs most often
    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index * 3:palette_index * 3 + 3]
    return hex((dominant_color[0] << 16) + (dominant_color[1] << 8) + dominant_color[2])
