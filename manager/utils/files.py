__all__ = [
    "human_readable_size",
    "get_file_info"
]



from os.path import basename, getsize, isfile, join
from typing import cast

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
