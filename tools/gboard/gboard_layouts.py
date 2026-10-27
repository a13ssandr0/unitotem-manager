#!/usr/bin/env python3
"""
Extracts the keyboard layouts of Gboard (Google's keyboard) from its APK, for the viewer's on-screen keyboard.

Nothing here is guessed: the layouts are read from the resources of the application, exactly as Gboard reads them
(see README.md for how to get and decode the APK).

How Gboard describes a keyboard (what was found by reading the decoded resources):
  * `res/xml/<ime>.xml`: one `<ime language="it">` per language, with its keyboards (`<keyboard_group variant=...>`, inline or
    through an `<include>` of a template that has `{lang_def}` / `{main_def}` placeholders).
  * the group's prime keyboard is `lang_def` (the language: its softkeys, i.e. letters, accents, popups) merged with
    `main_def` (the arrangement: which softkey is at which position). Both are XML with `<include>`, `<if>` / `<else_if>` /
    `<else>` (conditions on the device, the language, the variant and settings), `<softkeys>` (id -> press_data and
    long_press_data), `<key_mapping state=...>` (position -> softkey) and `<merge_key_mapping>` (adds a softkey's
    popup to the key at a position: this is how the symbols shown on the letters come in).
  * a position is named as in ISO 9995 / XKB: E00..E12 the number row, D01.. the top letter row, C01.. the middle row,
    B01.. the bottom letter row, `key_pos_*` the function keys (tab, del, caps, ime_action = enter, shift...).
  * `res/layout/<body>.xml` gives, for every row, the weight of every position: that is the real width of a key.
Gboard on a large tablet (device="tablet" device_size="large") with the number row on and "symbols on keys" on is what is extracted.

Usage: gboard_layouts.py DECODED_APK_DIR OUT_DIR
Writes OUT_DIR/index.json (the languages and their layouts, for the settings page), OUT_DIR/symbols.json (the symbols pages every
language shares) and OUT_DIR/layouts/<language>.json (one file per language, loaded on demand by the keyboard).
"""
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

ANDROID = '{http://schemas.android.com/apk/res/android}'
COND_TAGS = ('any_of', 'all_of', 'cond')
FUNCTION_KEYS = {'key_pos_tab': 'tab', 'key_pos_del': 'bksp', 'key_pos_caps': 'lock', 'key_pos_ime_action': 'enter',
                 'key_pos_shift': 'shift', 'key_pos_shift_right': 'shift'}
SYMBOLS_KEYBOARD = '0x7f170878'  # the symbols keyboard of a large tablet (the one `true="{support_drag_to_click}"` picks)
GENERIC_VARIANTS = ('qwerty', 'qwertz', 'azerty', 'dvorak', 'colemak', 'pcqwerty')  # offered for every language by a template
FULL_SET = ('en-US',)  # the languages that keep all the generic variants (the others only have their own and, failing that, qwerty)


def short(ref):
    m = re.search(r'(0x7f[0-9a-f]+)', ref or '')
    return m.group(1) if m else None


class UnresolvedWeight(Exception):
    pass


class Apk:
    def __init__(self, root):
        self.xml_dir = os.path.join(root, 'res', 'xml')
        self.layout_dir = os.path.join(root, 'res', 'layout')
        self.cache = {}
        self.layouts = {}
        self.unknown_conditions = {}
        self.strings = {}
        self.integers = {short(m.group(1)): m.group(2) for m in re.finditer(
            r'<integer name="(APKTOOL_DUMMYVAL_0x[0-9a-f]+)">(-?[0-9.]+)</integer>',
            open(os.path.join(root, 'res', 'values', 'integers.xml'), encoding='utf-8').read())}
        text = open(os.path.join(root, 'res', 'values', 'strings.xml'), encoding='utf-8').read()
        for m in re.finditer(r'<string name="(APKTOOL_DUMMYVAL_0x[0-9a-f]+)"[^>]*>(.*?)</string>', text, re.S):
            self.strings[short(m.group(1))] = html.unescape(m.group(2))

    def definitions(self):
        """Every softkey defined anywhere: id -> [attrs] (press_data, param_data, data or an <action> child give what it types)."""
        if getattr(self, '_definitions', None) is None:
            self._definitions = {}
            for name in os.listdir(self.xml_dir):
                try:
                    root = ET.parse(os.path.join(self.xml_dir, name)).getroot()
                except ET.ParseError:
                    continue
                for sk in root.iter('softkey'):
                    if sk.get('id') is None:
                        continue
                    attrs = dict(sk.attrib)
                    if attrs.get('press_data') is None:
                        for key in ('param_data', 'data'):
                            if attrs.get(key) is not None:
                                attrs['press_data'] = attrs[key]
                        for action in sk.findall('action'):
                            if action.get('type') == 'PRESS' and action.get('data') is not None:
                                attrs['press_data'] = action.get('data')
                    self._definitions.setdefault(short(attrs['id']), []).append(attrs)
        return self._definitions

    def fallback(self, key_id):
        """The softkey a keyboard uses when its own files do not define the id (the comma of the tablet layouts is such a case): the
        most common definition of that id in the whole application, preferring the plain one (what it types, no popup)."""
        found = [d for d in self.definitions().get(key_id, []) if d.get('press_data') is not None]
        if not found:
            return None
        counts = {}
        for d in found:
            counts[d['press_data']] = counts.get(d['press_data'], 0) + 1
        press = max(counts, key=counts.get)
        same = [d for d in found if d['press_data'] == press]
        plain = [d for d in same if d.get('long_press_data') is None]
        chosen = dict((plain or same)[0])
        chosen['_splitter'] = chosen.get('_splitter', ',')
        return chosen

    def xml(self, ref):
        key = short(ref)
        if key not in self.cache:
            self.cache[key] = ET.parse(os.path.join(self.xml_dir, 'APKTOOL_DUMMYVAL_%s.xml' % key)).getroot()
        return self.cache[key]

    def text(self, value):
        """A press_data / long_press_data item: a string resource, or text with the backslash escapes of the format."""
        if value is None:
            return None
        if value.startswith('@string/'):
            value = self.strings.get(short(value), value)
        return value.replace('\\\\', '\x00').replace('\\@', '@').replace('\\?', '?').replace('\\#', '#').replace('\\n', '\n').replace('\x00', '\\')

    def rows(self, layout_ref):
        """The rows of a body layout: [{'sum': weightSum, 'keys': [(position, weight, visible)]}], through its includes."""
        key = short(layout_ref)
        if key in self.layouts:
            return self.layouts[key]
        root = ET.parse(os.path.join(self.layout_dir, 'APKTOOL_DUMMYVAL_%s.xml' % key)).getroot()
        rows = []

        def keys_of(el):
            return [(c.get(ANDROID + 'id', '').split('/')[-1], weight_of(c.get(ANDROID + 'layout_weight', '0')),
                     c.get(ANDROID + 'visibility') != 'gone') for c in el if c.tag.endswith('SoftKeyView')]

        def weight_of(value):
            if value.startswith('@integer/') and short(value) in self.integers:
                value = self.integers[short(value)]
            try:
                return float(value)
            except ValueError:  # a theme attribute (a keyboard that is laid out by its theme, not by its own weights)
                raise UnresolvedWeight(value)

        def walk(el):
            for c in el:
                if c.tag == 'include':
                    rows.extend(self.rows(c.get('layout')))
                elif c.tag != 'ViewStub':
                    ks = keys_of(c)
                    if len(ks) >= 3:
                        rows.append({'sum': weight_of(c.get(ANDROID + 'weightSum', '0')) or None, 'keys': ks})
                    walk(c)
        walk(root)
        self.layouts[key] = rows
        return rows


class Keyboard:
    """What walking a keyboard definition collects (the state is Gboard's: SHIFT, CAPS, SUB_CATEGORY_1...)."""

    def __init__(self):
        self.softkeys = {}
        self.maps = {}    # state -> {position: [softkey ids]}
        self.merges = []  # (state, exclude_state, position, softkey id)
        self.body_layouts = []
        self.splitter = ','


def alternatives(state):
    return set(state.split(',')) if state else set()


def holds(apk, el, ctx, params):
    for k, v in el.attrib.items():
        if v.startswith('{'):
            v = params.get(v.strip('{}'), v)
        if k not in ctx:  # a setting this tool does not model: off
            apk.unknown_conditions[(k, v)] = apk.unknown_conditions.get((k, v), 0) + 1
            if v != 'false':
                return False
            continue
        if not any(ctx[k] == a or ctx[k].startswith(a + '-') for a in v.split('|')):
            return False
    for c in el:
        sub = [x for x in c if x.tag in COND_TAGS]
        if c.tag == 'any_of' and not any(holds(apk, x, ctx, params) for x in sub):
            return False
        if c.tag == 'all_of' and not all(holds(apk, x, ctx, params) for x in sub):
            return False
        if c.tag == 'cond' and not holds(apk, c, ctx, params):
            return False
    return True


def walk(apk, el, kb, ctx, params, cur_map=None, cur_merge=None):
    taken = None  # whether a branch of the current if / else_if / else chain was taken
    for ch in list(el):
        t = ch.tag
        if t in COND_TAGS:
            continue
        if t in ('if', 'ifnot'):
            taken = holds(apk, ch, ctx, params)
            if t == 'ifnot':
                taken = not taken
            if taken:
                walk(apk, ch, kb, ctx, params, cur_map, cur_merge)
            continue
        if t == 'else_if':
            if not taken and holds(apk, ch, ctx, params):
                taken = True
                walk(apk, ch, kb, ctx, params, cur_map, cur_merge)
            continue
        if t == 'else':
            if not taken:
                walk(apk, ch, kb, ctx, params, cur_map, cur_merge)
            taken = None
            continue
        taken = None
        if t == 'include' or (t == 'softkeys' and ch.get('href')):
            ref = ch.get('href')
            if ref.startswith('{'):
                ref = params.get(ref.strip('{}'))
            if ref:
                walk(apk, apk.xml(ref), kb, ctx, {**params, **{k: v for k, v in ch.attrib.items() if k != 'href'}})
        elif t == 'merge' and ch.get('def'):
            ref = ch.get('def')
            if ref.startswith('{'):
                ref = params.get(ref.strip('{}'))
            if ref:
                walk(apk, apk.xml(ref), kb, ctx, params)
        elif t == 'merge' and cur_merge is not None:
            cur_merge.append((ch.get('view_id').split('/')[-1], short(ch.get('key_id'))))
        elif t == 'softkey_list':
            kb.splitter = ch.get('splitter', ',')
            walk(apk, ch, kb, ctx, params, cur_map, cur_merge)
        elif t == 'softkey':
            attrs = dict(ch.attrib)
            attrs['_splitter'] = kb.splitter
            kb.softkeys[short(attrs['id'])] = attrs
        elif t == 'key_mapping':
            walk(apk, ch, kb, ctx, params, kb.maps.setdefault(ch.get('state', ''), {}), None)
        elif t == 'mapping' and cur_map is not None:
            cur_map[ch.get('view_id').split('/')[-1]] = [short(ch.get('key_id'))]
        elif t == 'merge_key_mapping':
            merged = []
            walk(apk, ch, kb, ctx, params, None, merged)
            kb.merges.extend((ch.get('state'), ch.get('exclude_state'), v, k) for v, k in merged)
        elif t == 'view' and ch.get('type') == 'body' and ch.get('layout'):
            kb.body_layouts.append(short(ch.get('layout')))
            walk(apk, ch, kb, ctx, params, cur_map, cur_merge)
        else:
            walk(apk, ch, kb, ctx, params, cur_map, cur_merge)


def mapping_for(kb, states):
    """Position -> softkeys, for the default state with the given states laid over it (a merge adds to the key there)."""
    out = {}

    def merges(state):
        mine = alternatives(state)
        for only, exclude, position, key in kb.merges:
            if only is not None and not (alternatives(only) & mine):
                continue
            if exclude is not None and (alternatives(exclude) & mine):
                continue
            out[position] = out.get(position, []) + [key]
    out.update({p: list(ks) for p, ks in kb.maps.get('', {}).items()})
    merges('')
    for state in states:
        out.update({p: list(ks) for p, ks in kb.maps.get(state, {}).items()})
        merges(state)
    return out


def resolve(apk, kb, ids):
    """The key at a position: what it types, what it shows when that differs, and its long-press popup."""
    press = label = None
    popup = []
    for n, key_id in enumerate(ids):
        sk = kb.softkeys.get(key_id) or apk.fallback(key_id)
        if not sk:
            continue
        sep = sk.get('_splitter', ',')
        data = sk.get('long_press_data')
        items = [apk.text(x) for x in ([] if data is None else data.split(sep)) if x != '']
        if sk.get('press_data') is not None or n == 0:
            if sk.get('press_data') is not None:
                press = apk.text(sk.get('press_data'))
            label = apk.text(sk.get('press_label'))
            popup = items
        else:  # a merge: its popup goes in at merge_index, in front of the accents by default
            at = sk.get('merge_index', '0')
            at = int(at) if at.lstrip('-').isdigit() else 0
            popup = popup[:at] + [x for x in items if x not in popup] + popup[at:]
    return press, label, popup


def keys_of_rows(apk, kb, geometry, lower, upper, shifted=None, functions=None):
    """The rows of a page: one entry per position with its weight (out of 786); the function keys are named, a position with
    nothing at it is a gap. `upper` is the page with caps lock, `shifted` the one with shift pressed (on a tablet shift also changes
    the digits and the punctuation, as on a PC keyboard)."""
    rows = []
    for row in geometry:
        positions = [(i, w) for i, w, visible in row['keys'] if visible]
        if not any(i[:1] in 'FEDCB' and i[1:].isdigit() for i, _ in positions):
            continue
        total = row['sum'] or sum(w for _, w in positions)  # a row without weightSum is as wide as its keys together
        scale = 786.0 / total
        out = []
        for position, weight in positions:
            weight = round(weight * scale, 2)
            if position in (functions or FUNCTION_KEYS):
                out.append({'f': (functions or FUNCTION_KEYS)[position], 'w': weight})
                continue
            if position == 'key_pos_empty':
                out.append({'w': weight})
                continue
            if not (position[:1] in 'FEDCB' and position[1:].isdigit()):
                continue
            press, label, popup = resolve(apk, kb, lower.get(position, []))
            if press is None:
                out.append({'w': weight})
                continue
            key = {'k': press, 'w': weight}
            up_press, _, up_popup = resolve(apk, kb, upper.get(position, []))
            up_press = up_press if up_press is not None else press
            capital = press.upper() if len(press.upper()) == len(press) else press  # a sharp s does not become SS
            if up_press == press:
                up_press = capital
            if up_press != capital:
                key['u'] = up_press
            if label:
                key['t'] = label
            if popup:
                key['l'] = popup
            if up_popup and up_popup != [x.upper() if len(x.upper()) == len(x) else x for x in popup]:
                key['L'] = up_popup
            if shifted is not None:
                sh_press, _, sh_popup = resolve(apk, kb, shifted.get(position, []))
                caps_press = key.get('u', capital)
                if sh_press is not None and sh_press != caps_press:
                    key['s'] = sh_press
                caps_popup = key.get('L', [x.upper() if len(x.upper()) == len(x) else x for x in popup])
                if sh_popup and sh_popup != caps_popup:
                    key['S'] = sh_popup
            out.append(key)
        rows.append(out)
    return rows


def keyboard_groups(apk, el, ctx):
    """The keyboard groups of an ime that apply (the conditions are evaluated: a group for the physical-keyboard simulator is not one)."""
    found = []
    taken = None
    for ch in list(el):
        if ch.tag in ('if', 'ifnot'):
            taken = holds(apk, ch, ctx, {})
            if ch.tag == 'ifnot':
                taken = not taken
            if taken:
                found += keyboard_groups(apk, ch, ctx)
        elif ch.tag == 'else_if':
            if not taken and holds(apk, ch, ctx, {}):
                taken = True
                found += keyboard_groups(apk, ch, ctx)
        elif ch.tag == 'else':
            if not taken:
                found += keyboard_groups(apk, ch, ctx)
            taken = None
        elif ch.tag == 'keyboard_group':
            taken = None
            found.append((ch, {}, {}))  # written in the ime itself: the language's own keyboard
        elif ch.tag == 'include' and ch.get('href'):
            taken = None
            try:
                template = apk.xml(ch.get('href'))
            except OSError:
                continue
            group = template.find('keyboard_group')
            if group is not None:
                found.append((group, dict(template.attrib), dict(ch.attrib)))
        else:
            taken = None
    return found


def ime_keyboards(apk):
    """The keyboards of every language, from its main ime (the one with most keyboards: the others are Morse, handwriting, a
    transliteration...): [{'lang', 'name', 'variant', 'main', 'langdef', 'primary'}] in the order of the file."""
    best = {}
    for name in sorted(os.listdir(apk.xml_dir)):
        path = os.path.join(apk.xml_dir, name)
        if '<ime ' not in open(path, encoding='utf-8').read(900):
            continue
        for ime in ET.parse(path).getroot().iter('ime'):
            lang = ime.get('language')
            groups = keyboard_groups(apk, ime, context(lang, ''))
            if lang not in best or len(groups) > len(best[lang][1]):
                best[lang] = (ime, groups)
    out = []
    for lang, (ime, groups) in best.items():
        seen = set()
        for group, defaults, given in groups:
            prime = None
            for kb in group.iter('keyboard'):
                if kb.get('type') == 'prime' and kb.find('merge') is not None:
                    prime = kb  # the last: the first is the accessibility simulator's
            if prime is None:
                continue

            def param(value):
                return given.get(value.strip('{}'), defaults.get(value.strip('{}'))) if value and value.startswith('{') else value
            variant = param(group.get('variant'))
            main, lang_def = param(prime.find('merge').get('def')), param(prime.get('def'))
            key = (variant, main, lang_def)
            if key in seen:
                continue
            seen.add(key)
            out.append({'lang': lang, 'name': ime.get('string_id'), 'variant': variant, 'main': main, 'langdef': lang_def,
                        'primary': given == {} or 'main_def' in given})
    return out


def context(lang, variant):
    return {'device': 'tablet', 'device_size': 'large', 'keyboard_mode': 'normal', 'enable_number_row': 'true',
            'enable_secondary_symbols': 'true', 'show_comma_key': 'true', 'show_period_key': 'true', 'language': lang, 'variant': variant or ''}


def letters(apk, item):
    kb = Keyboard()
    ctx = context(item['lang'], item['variant'])
    for ref in (item['main'], item['langdef']):
        if ref:
            walk(apk, apk.xml(ref), kb, ctx, {})
    if not kb.body_layouts:
        return None
    try:
        geometry = apk.rows(kb.body_layouts[-1])  # the last body layout is the one with the number row
    except UnresolvedWeight:
        return None
    rows = keys_of_rows(apk, kb, geometry, mapping_for(kb, []), mapping_for(kb, ['SHIFT,CAPS']), mapping_for(kb, ['SHIFT,CAPS', 'SHIFT']))
    return rows if len(rows) in (4, 5) else None


def symbols(apk, lang):
    kb = Keyboard()
    walk(apk, apk.xml(SYMBOLS_KEYBOARD), kb, context(lang, 'qwerty'), {})
    geometry = apk.rows(kb.body_layouts[-1])
    pages = []
    for states, toggle in (([], 'sym2'), (['SUB_CATEGORY_1'], 'sym1')):
        page = mapping_for(kb, states)
        # on these pages the keys at the places of caps lock and shift are the switch to the other page (descriptions "More symbols" / "Symbols")
        # (descriptions "More symbols" / "Symbols"): the one at caps lock's place has an icon, the one at shift's place the text =\\< / ?123
        functions = dict(FUNCTION_KEYS, key_pos_caps='icon-' + toggle, key_pos_shift=toggle, key_pos_shift_right='none')
        pages.append(keys_of_rows(apk, kb, geometry, page, page, functions=functions))
    return pages


def main(decoded, out_dir):
    apk = Apk(decoded)
    os.makedirs(os.path.join(out_dir, 'layouts'), exist_ok=True)
    by_lang = {}
    for item in ime_keyboards(apk):
        by_lang.setdefault(item['lang'], []).append(item)
    reference = symbols(apk, 'en-US')  # the symbols pages: a language has its own only when they differ from these
    languages = []
    for lang in sorted(by_lang):
        items = by_lang[lang]
        own = [i for i in items if i['primary']]
        wanted = items if lang in FULL_SET else own or [i for i in items if i['variant'] == 'qwerty'][:1]
        variants = {}
        for item in wanted:
            rows = letters(apk, item)
            if rows and (item['variant'] or 'default') not in variants:
                variants[item['variant'] or 'default'] = rows
        if not variants:
            continue
        default = next((i['variant'] for i in items if i['primary'] and i['variant'] in variants), None) or next(iter(variants))
        ordered = {default: variants[default], **{k: v for k, v in variants.items() if k != default}}
        record = {'lang': lang, 'variants': ordered}
        pages = symbols(apk, lang)
        if pages != reference:
            record['symbols'] = pages
        with open(os.path.join(out_dir, 'layouts', lang + '.json'), 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, separators=(',', ':'))
        languages.append({'id': lang, 'name': re.sub(r'^ime_', '', items[0]['name'] or lang).replace('_', ' '), 'variants': list(ordered)})
    with open(os.path.join(out_dir, 'symbols.json'), 'w', encoding='utf-8') as f:
        json.dump(reference, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(languages, f, ensure_ascii=False, separators=(',', ':'))
    print('%d languages, %d layouts' % (len(languages), sum(len(i['variants']) for i in languages)))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
