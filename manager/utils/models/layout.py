"""
Layout of a viewer window: the content rectangle (`Area`) and the tree of panes (`Leaf`/`Split`), see docs/layout-and-ticker.md.

A window is divided the way a terminal emulator divides its view: a pane is split in two, side by side (`h`) or stacked (`v`),
as often as wanted, and a pane can be closed, the neighbour taking its space. Every pane (a leaf of the tree) plays its own
playlist. The whole content sits in a rectangle of the window (`Area`, in percent), the rest of the window being a margin of a
chosen colour: this is how a tall touch panel is made to use only the part of it people can reach.

Everything here is pure data and pure functions: an operation returns a new layout and never changes its argument, which is
what makes them easy to test and to apply safely. Nothing here knows about windows, playlists' existence or the viewer; the
controller (webview/controller.py) applies the result.

Geometry is never refused, only clamped: a screen must not become unusable because of a number typed in the web UI or read
from a hand-edited file.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from utils.models.command_line import cmdargs
from utils.models.ticker import TickerSettings

MAX_LEAVES = 8          # panes in one window
MAX_DEPTH = 4           # splits on the longest path from the root to a pane
MIN_AREA = 5.0          # smallest width / height of the content rectangle, percent of the window
RATIO_MIN, RATIO_MAX = 0.1, 0.9
DEFAULT_MARGIN = 'rgb(0,0,0)'

_NUMBER = r'\s*(?:\d{1,3}(?:\.\d+)?%?)\s*'
_COLOR_RE = re.compile(
    r'(?:#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})'
    rf'|rgba?\({_NUMBER}(?:,{_NUMBER}){{2}}(?:,\s*(?:0|1|0?\.\d+|\d{{1,3}}%)\s*)?\))')


class LayoutError(ValueError):
    """An operation that cannot be done on this layout (a pane that is not there, too many panes...)."""


def _number(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def normalize_color(value) -> Optional[str]:
    """The colour as CSS, or None if it is not a plain colour (#rgb, #rrggbb, rgb(), rgba()): nothing else ever reaches the
    page, a colour is never a way to put a url() or an expression in a style."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if len(value) <= 40 and _COLOR_RE.fullmatch(value) else None


class Area(BaseModel):
    """The rectangle of the window the content is drawn in, percent of the window, in the space of the content (after the
    orientation of the window, not of the physical screen). Clamped, never refused. Immutable by convention."""
    left: float = 0
    top: float = 0
    width: float = 100
    height: float = 100
    margin_color: str = DEFAULT_MARGIN       # what is behind the content: the blank space around the area

    @model_validator(mode='before')
    @classmethod
    def _clamped(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        width = _clamp(_number(data.get('width'), 100), MIN_AREA, 100)
        height = _clamp(_number(data.get('height'), 100), MIN_AREA, 100)
        data['width'], data['height'] = round(width, 2), round(height, 2)
        data['left'] = round(_clamp(_number(data.get('left'), 0), 0, 100 - width), 2)
        data['top'] = round(_clamp(_number(data.get('top'), 0), 0, 100 - height), 2)
        return data

    @field_validator('margin_color', mode='before')
    @classmethod
    def _color(cls, value):
        return normalize_color(value) or DEFAULT_MARGIN

    @property
    def is_full(self) -> bool:
        return (self.left, self.top, self.width, self.height) == (0, 0, 100, 100)


def clamp_ratio(value) -> float:
    return round(_clamp(_number(value, 0.5), RATIO_MIN, RATIO_MAX), 4)


class Leaf(BaseModel):
    """One pane, which plays one playlist. Ids are never reused inside a window."""
    kind: Literal['leaf'] = 'leaf'
    id: int = Field(ge=1)


class Split(BaseModel):
    """Two nodes sharing a rectangle: 'h' puts them side by side (a | b), 'v' stacks them (a over b). `ratio` is the share of `a`."""
    kind: Literal['split'] = 'split'
    dir: Literal['h', 'v']
    ratio: float = 0.5
    a: Node
    b: Node

    @field_validator('ratio', mode='before')
    @classmethod
    def _ratio(cls, value):
        return clamp_ratio(value)


Node = Annotated[Union[Leaf, Split], Field(discriminator='kind')]
Split.model_rebuild()


# ── reading the tree ─────────────────────────────────────────────────────────

def leaves(node) -> list[int]:
    """The ids of the panes, in tree order (left to right, top to bottom)."""
    if isinstance(node, Leaf):
        return [node.id]
    return leaves(node.a) + leaves(node.b)


def main_leaf(node) -> int:
    """The main pane is the first one: what the assignments that know nothing of panes refer to."""
    while isinstance(node, Split):
        node = node.a
    return node.id


def depth(node) -> int:
    return 0 if isinstance(node, Leaf) else 1 + max(depth(node.a), depth(node.b))


def rects(node, x: float = 0.0, y: float = 0.0, w: float = 100.0, h: float = 100.0) -> dict[int, tuple[float, float, float, float]]:
    """The rectangle of every pane inside the area, percent of the area: (left, top, width, height)."""
    if isinstance(node, Leaf):
        return {node.id: (x, y, w, h)}
    if node.dir == 'h':
        wa = w * node.ratio
        return {**rects(node.a, x, y, wa, h), **rects(node.b, x + wa, y, w - wa, h)}
    ha = h * node.ratio
    return {**rects(node.a, x, y, w, ha), **rects(node.b, x, y + ha, w, h - ha)}


class WindowLayout(BaseModel):
    """The layout of one window. `panes` holds the playlist of every pane but the main one: the main pane's playlist is the
    window's assignment (viewer_assignments.json), which is what keeps old clients and stored assignments working."""
    area: Area = Field(default_factory=Area)
    tree: Node = Field(default_factory=lambda: Leaf(id=1))
    next_id: int = 2
    panes: dict[int, str] = Field(default_factory=dict)
    ticker: TickerSettings = Field(default_factory=TickerSettings)

    @model_validator(mode='after')
    def _consistent(self):
        ids = leaves(self.tree)
        if len(set(ids)) != len(ids):
            raise ValueError('The panes of a window must have different ids')
        if len(ids) > MAX_LEAVES:
            raise ValueError(f'A window has at most {MAX_LEAVES} panes')
        if depth(self.tree) > MAX_DEPTH:
            raise ValueError(f'Panes can be split at most {MAX_DEPTH} levels deep')
        self.next_id = max(self.next_id, max(ids) + 1)
        # a playlist is only kept for a pane that exists, and never for the main one (that is the window's assignment)
        main = ids[0]
        self.panes = {pane: pid for pane, pid in self.panes.items() if pane in ids and pane != main and pid}
        return self

    @property
    def leaf_ids(self) -> list[int]:
        return leaves(self.tree)

    @property
    def main(self) -> int:
        return main_leaf(self.tree)

    @property
    def is_default(self) -> bool:
        return self.area.is_full and isinstance(self.tree, Leaf) and not self.panes

    def page_payload(self) -> dict:
        """What the page of the window needs (`SetWindowLayout`): the area and the tree, nothing about playlists."""
        return {'area': self.area.model_dump(), 'tree': self.tree.model_dump()}


# ── operations: each returns a new layout ────────────────────────────────────

def _rebuild(layout: WindowLayout, **changes) -> WindowLayout:
    """A copy with some fields changed, validated again (model_copy does not): ids, depth, `next_id` and `panes` stay consistent."""
    fields = {'area': layout.area, 'tree': layout.tree, 'next_id': layout.next_id, 'panes': layout.panes,
              'ticker': layout.ticker}
    fields.update(changes)
    return WindowLayout(**fields)


def _replace(node, leaf_id: int, make):
    """The tree with the leaf `leaf_id` replaced by make(leaf), or None when there is no such leaf."""
    if isinstance(node, Leaf):
        return make(node) if node.id == leaf_id else None
    for side, other in (('a', 'b'), ('b', 'a')):
        child = _replace(getattr(node, side), leaf_id, make)
        if child is not None:
            return node.model_copy(update={side: child})
    return None


def split_leaf(layout: WindowLayout, leaf_id: int, direction: str) -> tuple[WindowLayout, int]:
    """Divides a pane in two. The pane keeps its id (and its playlist), the new one gets the next id and goes to the right
    ('h') or below ('v'). Returns the new layout and the id of the new pane."""
    if direction not in ('h', 'v'):
        raise LayoutError("A pane is split 'h' (side by side) or 'v' (stacked)")
    if leaf_id not in layout.leaf_ids:
        raise LayoutError(f'There is no pane {leaf_id}')
    if len(layout.leaf_ids) >= MAX_LEAVES:
        raise LayoutError(f'A window has at most {MAX_LEAVES} panes')
    new_id = layout.next_id
    tree = _replace(layout.tree, leaf_id, lambda leaf: Split(dir=direction, a=leaf, b=Leaf(id=new_id)))
    if depth(tree) > MAX_DEPTH:
        raise LayoutError(f'Panes can be split at most {MAX_DEPTH} levels deep')
    return _rebuild(layout, tree=tree, next_id=new_id + 1), new_id


def _without(node, leaf_id: int):
    """The tree without the leaf: its sibling takes the place of their parent split. None when there is no such leaf."""
    if isinstance(node, Leaf):
        return None
    if isinstance(node.a, Leaf) and node.a.id == leaf_id:
        return node.b
    if isinstance(node.b, Leaf) and node.b.id == leaf_id:
        return node.a
    for side in ('a', 'b'):
        child = _without(getattr(node, side), leaf_id)
        if child is not None:
            return node.model_copy(update={side: child})
    return None


def close_leaf(layout: WindowLayout, leaf_id: int) -> tuple[WindowLayout, bool, Optional[str]]:
    """Closes a pane: the neighbour takes its space. The last pane cannot be closed.

    Returns (layout, main_changed, promoted): closing the main pane makes the next one the main one, and `promoted` is the
    playlist that pane had (the window's assignment must become that one); None when the main pane did not change."""
    ids = layout.leaf_ids
    if leaf_id not in ids:
        raise LayoutError(f'There is no pane {leaf_id}')
    if len(ids) == 1:
        raise LayoutError('The last pane of a window cannot be closed')
    tree = _without(layout.tree, leaf_id)
    new_main = main_leaf(tree)
    main_changed = new_main != layout.main
    promoted = layout.panes.get(new_main) if main_changed else None
    panes = {pane: pid for pane, pid in layout.panes.items() if pane != leaf_id}
    return _rebuild(layout, tree=tree, panes=panes), main_changed, promoted


def _set_ratio(node, path: str, ratio: float):
    if not isinstance(node, Split):
        raise LayoutError('There is no split there')
    if not path:
        return node.model_copy(update={'ratio': clamp_ratio(ratio)})
    if path[0] not in 'ab':
        raise LayoutError('A split is addressed by a path of a and b')
    side = path[0]
    return node.model_copy(update={side: _set_ratio(getattr(node, side), path[1:], ratio)})


def set_ratio(layout: WindowLayout, path: str, ratio: float) -> WindowLayout:
    """Moves the splitter of the split at `path` (the root is '', its first child 'a', the second child of that 'ab'...).
    The ratio is clamped to 10 %..90 %."""
    if len(path) > MAX_DEPTH or not re.fullmatch(r'[ab]*', path):
        raise LayoutError('A split is addressed by a path of a and b')
    return _rebuild(layout, tree=_set_ratio(layout.tree, path, ratio))


def set_area(layout: WindowLayout, area: Area) -> WindowLayout:
    return _rebuild(layout, area=area)


def with_pane_playlist(layout: WindowLayout, pane: int, playlist_id: Optional[str]) -> WindowLayout:
    """The playlist of a pane that is not the main one (None removes it)."""
    if pane not in layout.leaf_ids:
        raise LayoutError(f'There is no pane {pane}')
    if pane == layout.main:
        raise LayoutError('The playlist of the main pane is the assignment of the window')
    panes = {p: pid for p, pid in layout.panes.items() if p != pane}
    if playlist_id:
        panes[pane] = playlist_id
    return _rebuild(layout, panes=panes)


# ── storage ──────────────────────────────────────────────────────────────────

class WindowLayouts:
    """The layout of every window, kept beside viewer_settings.json and keyed like its keyboards: "<viewer id>|<screen name>",
    the name of the output surviving an unplug where the id of the window does not. A window with no entry has the default
    layout (one pane filling the window)."""

    VERSION = 1

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.windows: dict[str, WindowLayout] = {}

    @staticmethod
    def default_path() -> Path:
        return Path(cmdargs.assets_file).parent / 'window_layouts.json'

    @classmethod
    def load(cls, path: Optional[Path] = None) -> 'WindowLayouts':
        store = cls(path)
        file = store.file
        try:
            with open(file) as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            return store
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the window layouts from {} ({}), using the defaults', file, exc)
            return store
        store.windows = store.parse(stored)
        return store

    @staticmethod
    def parse(stored) -> dict[str, WindowLayout]:
        """The layouts of a stored or backed-up document; what is not valid is left out (that window gets the default)."""
        windows = stored.get('windows') if isinstance(stored, dict) else None
        found = {}
        for key, raw in (windows.items() if isinstance(windows, dict) else []):
            try:
                found[str(key)] = WindowLayout.model_validate(raw)
            except ValueError as exc:
                logger.error('Ignoring the invalid layout of {}: {}', key, exc)
        return found

    @property
    def file(self) -> Path:
        return self.path or self.default_path()

    def get(self, key: str) -> WindowLayout:
        return self.windows.get(key) or WindowLayout()

    def put(self, key: str, layout: WindowLayout):
        """Stores a layout (a default one is not kept: it is what a window without an entry has)."""
        if layout.is_default and layout.ticker == TickerSettings():
            self.windows.pop(key, None)
        else:
            self.windows[key] = layout
        self.save()

    def replace_all(self, windows: dict[str, WindowLayout]):
        self.windows = dict(windows)
        self.save()

    def rename(self, old: str, new: str):
        if old in self.windows and new not in self.windows:
            self.windows[new] = self.windows.pop(old)
            self.save()

    def export(self) -> dict:
        return {'version': self.VERSION, 'windows': {k: v.model_dump(mode='json') for k, v in self.windows.items()}}

    def save(self):
        path = self.file
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as handle:
                json.dump(self.export(), handle, indent=4)
            tmp.replace(path)       # atomic: a power cut cannot leave a half file
        except OSError as exc:
            logger.error('Cannot save the window layouts to {}: {}', path, exc)


window_layouts = WindowLayouts.load()
