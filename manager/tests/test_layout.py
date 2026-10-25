"""Window layout: the area, the tree of panes and its operations, the stored file."""
import json

import pytest
from pydantic import ValidationError

from utils.models.layout import (MAX_DEPTH, MAX_LEAVES, Area, Leaf, LayoutError, Split, WindowLayout, WindowLayouts,
                                 close_leaf, leaves, main_leaf, normalize_color, rects, set_area, set_ratio, split_leaf,
                                 with_pane_playlist)


# ── area ─────────────────────────────────────────────────────────────────

def test_default_area_is_the_whole_window():
    a = Area()
    assert (a.left, a.top, a.width, a.height) == (0, 0, 100, 100)
    assert a.is_full and a.margin_color == 'rgb(0,0,0)'


@pytest.mark.parametrize('given, expected', [
    (dict(left=-10, top=-5, width=50, height=50), (0, 0, 50, 50)),
    (dict(left=90, top=90, width=50, height=50), (50, 50, 50, 50)),          # pushed back inside
    (dict(width=0, height=-3), (0, 0, 5, 5)),                                 # minimum size
    (dict(width=500, height=500), (0, 0, 100, 100)),
    (dict(left='abc', top=None, width='nan', height=float('inf')), (0, 0, 100, 100)),
    (dict(left=33.333333, top=0, width=33.333333, height=100), (33.33, 0, 33.33, 100)),
])
def test_area_is_clamped_never_refused(given, expected):
    a = Area(**given)
    assert (a.left, a.top, a.width, a.height) == expected


@pytest.mark.parametrize('color', ['rgb(0,0,0)', 'rgb(255, 128, 0)', 'rgba(0,0,0,0.5)', '#fff', '#FF8800', '#ff880080',
                                   'rgb(10%,20%,30%)'])
def test_plain_colours_are_accepted(color):
    assert normalize_color(color) == color
    assert Area(margin_color=color).margin_color == color


@pytest.mark.parametrize('color', ['red; background:url(x)', 'url(http://x)', 'expression(1)', 'rgb(1,2)', '#12', '',
                                   None, 5, '#' + 'a' * 50, 'var(--x)', 'rgb(1,2,3);x'])
def test_other_colours_become_black(color):
    assert normalize_color(color) is None
    assert Area(margin_color=color).margin_color == 'rgb(0,0,0)'


# ── tree ─────────────────────────────────────────────────────────────────

def test_default_layout_has_one_pane():
    layout = WindowLayout()
    assert layout.tree == Leaf(id=1) and layout.next_id == 2 and layout.main == 1 and layout.is_default
    assert layout.leaf_ids == [1]


def test_defaults_are_not_shared_between_layouts():
    a, b = WindowLayout(), WindowLayout()
    a2, _ = split_leaf(a, 1, 'h')
    assert b.tree == Leaf(id=1) and a.tree == Leaf(id=1) and isinstance(a2.tree, Split)


def test_split_keeps_the_pane_and_adds_one_after_it():
    layout, new = split_leaf(WindowLayout(), 1, 'h')
    assert new == 2 and layout.next_id == 3
    assert layout.tree == Split(dir='h', ratio=0.5, a=Leaf(id=1), b=Leaf(id=2))
    assert layout.leaf_ids == [1, 2] and layout.main == 1
    layout, new = split_leaf(layout, 2, 'v')
    assert new == 3
    assert leaves(layout.tree) == [1, 2, 3]
    assert layout.tree.b == Split(dir='v', ratio=0.5, a=Leaf(id=2), b=Leaf(id=3))


def test_split_does_not_change_its_argument():
    original = WindowLayout()
    split_leaf(original, 1, 'v')
    assert original.tree == Leaf(id=1) and original.next_id == 2


def test_ids_are_never_reused():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')       # 1 | 2
    layout, _, _ = close_leaf(layout, 2)
    layout, new = split_leaf(layout, 1, 'h')
    assert new == 3


def test_split_refuses_nonsense():
    layout = WindowLayout()
    with pytest.raises(LayoutError):
        split_leaf(layout, 9, 'h')
    with pytest.raises(LayoutError):
        split_leaf(layout, 1, 'x')


def test_at_most_eight_panes():
    layout = WindowLayout()
    for _ in range(3):              # 1 -> 2 -> 4 -> 8, balanced, so the depth limit does not get in the way
        for target in layout.leaf_ids:
            layout, _ = split_leaf(layout, target, 'h')
    assert len(layout.leaf_ids) == MAX_LEAVES
    with pytest.raises(LayoutError, match='at most'):
        split_leaf(layout, 1, 'h')


def test_depth_is_limited():
    layout = WindowLayout()
    for _ in range(MAX_DEPTH):
        layout, _ = split_leaf(layout, layout.leaf_ids[-1], 'h')
    with pytest.raises(LayoutError, match='levels'):
        split_leaf(layout, layout.leaf_ids[-1], 'h')


def test_close_gives_the_space_to_the_neighbour():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout, _ = split_leaf(layout, 2, 'v')              # 1 | (2 over 3)
    closed, main_changed, promoted = close_leaf(layout, 2)
    assert closed.tree == Split(dir='h', ratio=0.5, a=Leaf(id=1), b=Leaf(id=3))
    assert (main_changed, promoted) == (False, None)
    closed, _, _ = close_leaf(layout, 1)                # (2 over 3) takes everything
    assert closed.tree == Split(dir='v', ratio=0.5, a=Leaf(id=2), b=Leaf(id=3))


def test_last_pane_cannot_be_closed():
    with pytest.raises(LayoutError, match='last'):
        close_leaf(WindowLayout(), 1)
    with pytest.raises(LayoutError):
        close_leaf(WindowLayout(), 7)


def test_closing_the_main_pane_promotes_the_next_one_and_its_playlist():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout = with_pane_playlist(layout, 2, 'plB')
    closed, main_changed, promoted = close_leaf(layout, 1)
    assert main_changed and promoted == 'plB'
    assert closed.main == 2 and closed.panes == {}           # it lives in the window's assignment from now on
    layout = with_pane_playlist(split_leaf(WindowLayout(), 1, 'h')[0], 2, None)
    _, main_changed, promoted = close_leaf(layout, 1)
    assert main_changed and promoted is None


def test_closing_a_pane_forgets_its_playlist():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout = with_pane_playlist(layout, 2, 'plB')
    closed, _, _ = close_leaf(layout, 2)
    assert closed.panes == {}


def test_pane_playlists_never_cover_the_main_pane():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    with pytest.raises(LayoutError, match='main'):
        with_pane_playlist(layout, 1, 'x')
    with pytest.raises(LayoutError):
        with_pane_playlist(layout, 9, 'x')
    assert with_pane_playlist(layout, 2, 'x').panes == {2: 'x'}
    assert with_pane_playlist(with_pane_playlist(layout, 2, 'x'), 2, None).panes == {}


# ── splitter ─────────────────────────────────────────────────────────────

def test_ratio_is_clamped():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    assert set_ratio(layout, '', 0.3).tree.ratio == 0.3
    assert set_ratio(layout, '', 0).tree.ratio == 0.1
    assert set_ratio(layout, '', 5).tree.ratio == 0.9
    assert set_ratio(layout, '', float('nan')).tree.ratio == 0.5
    assert layout.tree.ratio == 0.5


def test_ratio_of_a_nested_split():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout, _ = split_leaf(layout, 2, 'v')
    moved = set_ratio(layout, 'b', 0.25)
    assert moved.tree.ratio == 0.5 and moved.tree.b.ratio == 0.25
    with pytest.raises(LayoutError):
        set_ratio(layout, 'a', 0.2)            # a leaf, not a split
    with pytest.raises(LayoutError):
        set_ratio(layout, 'x', 0.2)
    with pytest.raises(LayoutError):
        set_ratio(layout, 'aaaaa', 0.2)
    with pytest.raises(LayoutError):
        set_ratio(WindowLayout(), '', 0.2)


def test_rects_cover_the_area_exactly():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout, _ = split_leaf(layout, 2, 'v')
    layout = set_ratio(set_ratio(layout, '', 0.25), 'b', 0.4)
    r = rects(layout.tree)
    assert r[1] == (0, 0, 25, 100)
    assert r[2] == (25, 0, 75, pytest.approx(40))
    assert r[3] == (25, pytest.approx(40), 75, pytest.approx(60))
    assert sum(w * h for _, _, w, h in r.values()) == pytest.approx(100 * 100)


# ── consistency of a stored layout ───────────────────────────────────────

def test_duplicate_ids_are_refused():
    tree = Split(dir='h', a=Leaf(id=1), b=Leaf(id=1))
    with pytest.raises(ValidationError):
        WindowLayout(tree=tree)


def test_too_many_panes_are_refused():
    tree = Leaf(id=1)
    for i in range(2, MAX_LEAVES + 2):
        tree = Split(dir='h' if i % 2 else 'v', a=tree, b=Leaf(id=i))
    with pytest.raises(ValidationError):
        WindowLayout(tree=tree, next_id=50)


def test_next_id_is_kept_above_every_pane():
    layout = WindowLayout(tree=Split(dir='h', a=Leaf(id=4), b=Leaf(id=7)), next_id=1)
    assert layout.next_id == 8


def test_playlists_of_missing_panes_and_of_the_main_pane_are_dropped():
    layout = WindowLayout(tree=Split(dir='h', a=Leaf(id=1), b=Leaf(id=2)), panes={1: 'a', 2: 'b', 9: 'c'})
    assert layout.panes == {2: 'b'}


def test_set_area_keeps_the_tree():
    layout, _ = split_leaf(WindowLayout(), 1, 'v')
    moved = set_area(layout, Area(left=0, top=33.33, width=100, height=33.33, margin_color='#112233'))
    assert moved.tree == layout.tree and moved.area.top == 33.33 and not moved.area.is_full


def test_page_payload_has_no_playlists():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout = with_pane_playlist(layout, 2, 'secret')
    payload = layout.page_payload()
    assert set(payload) == {'area', 'tree'} and 'secret' not in json.dumps(payload)
    assert payload['tree']['kind'] == 'split' and payload['tree']['a'] == {'kind': 'leaf', 'id': 1}
    assert main_leaf(layout.tree) == 1


def test_a_layout_survives_json():
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    layout, _ = split_leaf(layout, 2, 'v')
    layout = with_pane_playlist(set_area(layout, Area(left=10, top=20, width=50, height=60, margin_color='#abc')), 3, 'pl')
    again = WindowLayout.model_validate(json.loads(layout.model_dump_json()))
    assert again == layout and again.panes == {3: 'pl'}


# ── the file ─────────────────────────────────────────────────────────────

KEY = 'local-webview|HDMI-A-1'


def test_store_round_trip(tmp_path):
    path = tmp_path / 'window_layouts.json'
    store = WindowLayouts.load(path)
    assert store.get(KEY).is_default and not path.exists()
    layout, _ = split_leaf(WindowLayout(), 1, 'h')
    store.put(KEY, layout)
    again = WindowLayouts.load(path)
    assert again.get(KEY) == layout
    assert json.loads(path.read_text())['windows'][KEY]['tree']['kind'] == 'split'


def test_storing_the_default_removes_the_entry(tmp_path):
    store = WindowLayouts.load(tmp_path / 'l.json')
    store.put(KEY, split_leaf(WindowLayout(), 1, 'h')[0])
    store.put(KEY, WindowLayout())
    assert KEY not in WindowLayouts.load(tmp_path / 'l.json').windows


def test_unreadable_or_invalid_files_give_defaults(tmp_path):
    path = tmp_path / 'l.json'
    path.write_text('{not json')
    assert WindowLayouts.load(path).windows == {}
    path.write_text(json.dumps({'windows': {
        'good|A': {'tree': {'kind': 'leaf', 'id': 3}},
        'bad|B': {'tree': {'kind': 'split', 'dir': 'h', 'a': {'kind': 'leaf', 'id': 1}, 'b': {'kind': 'leaf', 'id': 1}}},
        'worse|C': 'nonsense',
    }}))
    store = WindowLayouts.load(path)
    assert set(store.windows) == {'good|A'} and store.windows['good|A'].main == 3
    assert store.get('bad|B').is_default


def test_a_file_from_a_newer_version_with_extra_fields_loads(tmp_path):
    path = tmp_path / 'l.json'
    path.write_text(json.dumps({'version': 2, 'windows': {KEY: {'future': True, 'area': {'width': 50, 'extra': 1}}}}))
    assert WindowLayouts.load(path).get(KEY).area.width == 50


def test_rename_follows_the_output(tmp_path):
    store = WindowLayouts.load(tmp_path / 'l.json')
    layout = split_leaf(WindowLayout(), 1, 'h')[0]
    store.put('local-webview|HDMI-1', layout)
    store.rename('local-webview|HDMI-1', 'local-webview|HDMI-A-1')
    assert store.get('local-webview|HDMI-A-1') == layout and 'local-webview|HDMI-1' not in store.windows


def test_export_and_restore_keep_the_layouts(tmp_path):
    store = WindowLayouts.load(tmp_path / 'a.json')
    store.put(KEY, split_leaf(WindowLayout(), 1, 'v')[0])
    other = WindowLayouts.load(tmp_path / 'b.json')
    other.replace_all(WindowLayouts.parse(json.loads(json.dumps(store.export()))))
    assert other.get(KEY) == store.get(KEY) and (tmp_path / 'b.json').exists()
