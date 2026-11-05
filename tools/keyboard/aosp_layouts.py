#!/usr/bin/env python3
"""
Extracts the keyboard layouts of AOSP LatinIME (Apache-2.0) for the viewer's on-screen keyboard.

Nothing here is guessed: the layouts are read from the resource files of LatinIME, the way LatinIME builds a keyboard
(see README.md for the files and how to fetch them).

How LatinIME describes a keyboard (what this script follows):
  * `res/xml/method.xml`: the subtypes (a locale and, in `KeyboardLayoutSet=`, the layout set; for the few without one,
    `res/values/donottranslate.xml` maps `locale:extra values` to the layout set).
  * `res/xml/keyboard_layout_set_<name>.xml`: which keyboard file is the alphabet, the symbols and the shifted symbols.
  * `res/xml/kbd_*.xml`, `rows_*.xml`, `rowkeys_*.xml`, `key_*.xml`: `<Row>`, `<Key>`, `<Spacer>`, `<include>`, `<switch>`/`<case>`
    (conditions on the layout set, the shift state, the locale...), `<key-style>`. Key widths are `N%p` of the keyboard width.
    `res/xml-sw600dp/` overrides some of them for tablets (the screen of a kiosk), and is looked up first.
  * a key spec is `label|output` with `!text/<name>` references, resolved with the table of the locale (`KeyboardTextsTable.java`);
    the long-press keys are `moreKeys` plus `additionalMoreKeys` (`%` marks where the additional ones go).
  * the shift states are built as separate keyboards (`alphabet`, `alphabetManualShifted` = shift, `alphabetShiftLocked` = caps lock):
    a key is read in all three and the result keeps what differs from the capital letter of the plain one.
  * the PC-style number row (digits with the symbols shift gives) is the first row of `kbd_pcqwerty.xml`, put on top of every layout
    but the PC one.

The conditions are evaluated for a text field on a tablet without language switch key, split layout, voice or settings key.
Keys the viewer has no use for (emoji, settings, language switch) are left out; icons are replaced by the function name.

Usage: aosp_layouts.py LATINIME_DIR OUT_DIR
  LATINIME_DIR is laid out like the upstream repository: java/res/xml, java/res/xml-sw600dp, java/res/values and
  java/src/com/android/inputmethod/keyboard/internal/KeyboardTextsTable.java.
Writes OUT_DIR/index.json (languages and their layouts, for the settings page), OUT_DIR/symbols.json (the symbols pages every
language shares) and OUT_DIR/layouts/<language>.json (one file per language, loaded on demand by the keyboard), and OUT_DIR/NOTICE.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

LATIN = '{http://schemas.android.com/apk/res/com.android.inputmethod.latin}'
ANDROID = '{http://schemas.android.com/apk/res/android}'
ROW_WEIGHT = 786.0  # the weight units of webui/src/keyboard.js for 100% of the width
UPSTREAM = 'https://android.googlesource.com/platform/packages/inputmethods/LatinIME'
COMMIT = '127336e9f29d69607eab55982324b210279ae8c5'
NOTICE = f"""Keyboard layouts derived from AOSP LatinIME, Copyright (C) 2008-2025 The Android Open Source Project, Apache License 2.0
Upstream: {UPSTREAM}
Commit: {COMMIT}
License text: https://www.apache.org/licenses/LICENSE-2.0

The files of this directory are generated from the resource files of LatinIME by tools/keyboard/aosp_layouts.py (layouts, key
widths, long-press keys and symbols pages are the ones LatinIME defines for its tablet keyboards).
"""
SHIFT_STATES = ('alphabet', 'alphabetShiftLocked', 'alphabetManualShifted')
UPPER_STATES = ('alphabetManualShifted', 'alphabetAutomaticShifted', 'alphabetShiftLocked', 'alphabetShiftLockShifted')
# subtypes of the same locale get their layout set as the name of the variant; these are the extra layouts of a language
# LatinIME offers for every language ("predefined_layouts"); only the English one offers them here
GENERIC_LAYOUTS = ('qwerty', 'qwertz', 'azerty', 'dvorak', 'colemak', 'pcqwerty')
GENERIC_FOR = ('en-US',)
# codes (`!code/<name>`) of the keys that are not text, and what the viewer calls them (None: left out)
CODE_FUNCTIONS = {'key_delete': 'bksp', 'key_enter': 'enter', 'key_tab': 'tab', 'key_space': 'space', 'key_emoji': None,
                  'key_settings': None, 'key_language_switch': None, 'key_shortcut': None, 'key_capslock': None}
MORE_KEYS_FLAGS = ('!autoColumnOrder!', '!fixedColumnOrder!', '!hasLabels!', '!needsDividers!', '!noPanelAutoMoreKey!')
# Java's Locale display names the table does not carry (no entry of the locale or the language in KeyboardTextsTable)
LANGUAGE_NAMES = {'in': 'Indonesian', 'ms': 'Malay', 'zz': 'Alphabet'}
REGION_NAMES = {'AZ': 'Azerbaijan', 'BE': 'Belgium', 'BR': 'Brazil', 'CA': 'Canada', 'CH': 'Switzerland', 'EE': 'Estonia',
                'ES': 'Spain', 'GB': 'UK', 'GE': 'Georgia', 'IN': 'India', 'MY': 'Malaysia', 'PT': 'Portugal', 'US': 'US',
                '419': 'Latin America'}
# the ids the language tag of the subtypes does not give (Java's old language codes)
LANGUAGE_TAGS = {'in': 'id', 'iw': 'he'}


class Unsupported(Exception):
    pass


# ── resources ───────────────────────────────────────────────────────────

AAPT_ESCAPES = {'n': '\n', 't': '\t', '\\': '\\', "'": "'", '"': '"', '@': '@', '?': '?', '#': '#'}


def aapt_unescape(text):
    """The backslash escapes aapt resolves in the value of an XML attribute (`\\@`, `\\\\`, `\\'`...)."""
    if '\\' not in text:
        return text
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            n = text[i + 1]
            if n in AAPT_ESCAPES:
                out.append(AAPT_ESCAPES[n])
                i += 2
                continue
            if n == 'u' and re.fullmatch(r'[0-9a-fA-F]{4}', text[i + 2:i + 6]):
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
                continue
            raise Unsupported(f'unknown escape in {text!r}')
        out.append(c)
        i += 1
    return ''.join(out)


def attrs_of(element):
    """The attributes of a tag without namespace, with the escapes of aapt resolved."""
    result = {}
    for name, value in element.attrib.items():
        if name.startswith(LATIN):
            name = name[len(LATIN):]
        elif name.startswith(ANDROID):
            name = 'android:' + name[len(ANDROID):]
        result[name] = aapt_unescape(value)
    return result


class Resources:
    def __init__(self, root):
        res = os.path.join(root, 'java', 'res')
        self.values = os.path.join(res, 'values')
        # the most specific qualifier first: a kiosk is a large tablet (sw768dp then sw600dp), the plain files are the fallback
        self.xml_dirs = [d for d in (os.path.join(res, 'xml-sw768dp'), os.path.join(res, 'xml-sw600dp'), os.path.join(res, 'xml'))
                         if os.path.isdir(d)]
        self.texts_java = os.path.join(root, 'java', 'src', 'com', 'android', 'inputmethod', 'keyboard', 'internal',
                                       'KeyboardTextsTable.java')
        self.cache = {}

    def xml(self, name):
        if name not in self.cache:
            for directory in self.xml_dirs:
                path = os.path.join(directory, name + '.xml')
                if os.path.isfile(path):
                    self.cache[name] = ET.parse(path).getroot()
                    break
            else:
                raise Unsupported(f'no resource @xml/{name}')
        return self.cache[name]

    def values_file(self, name):
        return ET.parse(os.path.join(self.values, name)).getroot()


# ── the texts table (`!text/<name>`) ────────────────────────────────────

JAVA_TOKEN = re.compile(r'"(?:[^"\\\n]|\\.)*"|//[^\n]*|/\*.*?\*/|\w+|[{},+;=\[\]]', re.S)
JAVA_ESCAPES = {'n': '\n', 't': '\t', '\\': '\\', '"': '"', "'": "'"}


def java_string(literal):
    body = literal[1:-1]
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == '\\':
            n = body[i + 1]
            if n == 'u':
                out.append(chr(int(body[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(JAVA_ESCAPES[n])
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def java_array(body):
    """The items of a `String[]` initializer: a string, or None for `null`; `EMPTY` is the empty string."""
    items = []
    for token in JAVA_TOKEN.findall(body):
        if token.startswith('//') or token.startswith('/*'):
            continue
        if token == ',':
            continue
        if token.startswith('"'):
            items.append(java_string(token))
        elif token == 'null':
            items.append(None)
        elif token == 'EMPTY':
            items.append('')
        else:
            raise Unsupported(f'unexpected token {token!r} in KeyboardTextsTable.java')
    return items


class Texts:
    PREFIX = '!text/'

    def __init__(self, java_path):
        source = open(java_path, encoding='utf-8').read()
        arrays = {m.group(1): java_array(m.group(2))
                  for m in re.finditer(r'private static final String\[\] (\w+) = \{(.*?)\n    \};', source, re.S)}
        self.names = {name: i for i, name in enumerate(arrays.pop('NAMES'))}
        self.default = arrays['TEXTS_DEFAULT']
        self.tables = {}
        self.language_names = {}
        for m in re.finditer(r'"([A-Za-z_0-9]+)"\s*,\s*TEXTS_(\w+),\s*/\*\s*\d+/\s*\d+\s+(.*?)\s*\*/', source):
            self.tables[m.group(1)] = arrays['TEXTS_' + m.group(2)]
            self.language_names[m.group(1)] = m.group(3)

    def table(self, locale):
        """`KeyboardTextsTable.getTextsTable`: the locale, then its language, then the default."""
        if locale in self.tables:
            return self.tables[locale]
        return self.tables.get(locale.split('_')[0], self.default)

    def get(self, name, table):
        index = self.names[name]
        text = table[index] if index < len(table) else None
        return text if text is not None else self.default[index]

    def resolve(self, raw, table):
        """`KeyboardTextsSet.resolveTextReference`: expands `!text/<name>` until no reference is left (backslash escapes stay)."""
        if not raw:
            return None
        text = raw
        for _ in range(10):
            if len(text) < len(self.PREFIX):
                break
            out = []
            changed = False
            pos = 0
            while pos < len(text):
                if text.startswith(self.PREFIX, pos):
                    end = pos + len(self.PREFIX)
                    while end < len(text) and (text[end] in '_' or 'a' <= text[end] <= 'z' or '0' <= text[end] <= '9'):
                        end += 1
                    out.append(self.get(text[pos + len(self.PREFIX):end], table))
                    pos = end
                    changed = True
                elif text.startswith('!string/', pos):
                    raise Unsupported(f'string resource reference in {raw!r}')
                elif text[pos] == '\\':
                    out.append(text[pos:pos + 2])
                    pos += 2
                else:
                    out.append(text[pos])
                    pos += 1
            text = ''.join(out)
            if not changed:
                break
        else:
            raise Unsupported(f'too many references in {raw!r}')
        return text or None


# ── key specs (KeySpecParser, MoreKeySpec) ──────────────────────────────

def split_key_specs(text):
    """`MoreKeySpec.splitKeySpecs`: comma separated, a backslash escapes the next character."""
    if not text:
        return None
    if len(text) == 1:
        return None if text == ',' else [text]
    parts = []
    start = 0
    pos = 0
    while pos < len(text):
        c = text[pos]
        if c == ',':
            if pos - start > 0:
                parts.append(text[start:pos])
            start = pos + 1
        elif c == '\\':
            pos += 1
        pos += 1
    if len(text) - start > 0:
        parts.append(text[start:])
    return parts or None


def insert_additional(more, additional):
    """`MoreKeySpec.insertAdditionalMoreKeys`, step for step (including how it appends the surplus)."""
    more = [m for m in (more or []) if m]
    additional = [m for m in (additional or []) if m]
    out = None
    index = 0
    for i, spec in enumerate(more):
        if spec == '%':
            if index < len(additional):
                if out is not None:
                    out.append(additional[index])
                else:
                    more[i] = additional[index]
                index += 1
            elif out is None:
                out = more[:i]
        elif out is not None:
            out.append(spec)
    if additional and index == 0:
        out = list(additional) + list(more)
    elif index < len(additional):
        out = list(more)
        for _ in range(index, len(additional)):
            out.append(additional[index])
    if out is None and more:
        return more
    if out:
        return out
    return None


def label_end(spec):
    if '\\' not in spec:
        pos = spec.find('|')
        if pos == 0:
            if len(spec) == 1:
                return -1
            raise Unsupported(f'empty label {spec!r}')
        return pos
    i = 0
    while i < len(spec):
        if spec[i] == '\\' and i + 1 < len(spec):
            i += 1
        elif spec[i] == '|':
            return i
        i += 1
    return -1


def parse_escape(text):
    if '\\' not in text:
        return text
    out = []
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            i += 1
        out.append(text[i])
        i += 1
    return ''.join(out)


class Spec:
    """A key spec: label (None for an icon), icon name, code (an int, a `key_*` name, or None for an output text), output text."""

    def __init__(self, spec):
        self.icon = None
        end = label_end(spec)
        before = spec if end < 0 else spec[:end]
        after = None if end < 0 else spec[end + 1:]
        if spec.startswith('!icon/'):
            self.icon = before[len('!icon/'):]
            self.label = None
        else:
            self.label = parse_escape(before)
            if not self.label:
                raise Unsupported(f'empty label {spec!r}')
        self.code = None
        self.output = None
        if after is not None and (after.startswith('!code/') or after.startswith('0x')):
            self.code = after[len('!code/'):] if after.startswith('!code/') else int(after[2:], 16)
            return
        text = parse_escape(after) if after else None
        if text is None and self.label is not None:
            text = self.label
        if text is None:
            raise Unsupported(f'no output in {spec!r}')
        if len(text) == 1 or len([c for c in text]) == 1:
            self.code = ord(text)
        else:
            self.output = text


def upper(text, locale):
    """`toTitleCaseOfKeyLabel`: the capital of a label, with the Turkic dotted i."""
    if locale.split('_')[0] in ('tr', 'az'):
        text = text.replace('i', 'İ')
    return text.upper()


# ── building a keyboard ─────────────────────────────────────────────────

class Style:
    def __init__(self, parent):
        self.attrs = {}
        self.parent = parent


class Cell:
    """One key of a row: where it starts (% of the width), its width, and what it is."""

    def __init__(self, x, width, kind, **fields):
        self.x = x
        self.w = width
        self.kind = kind  # key | fn | gap
        self.text = fields.get('text')  # what a key types (also its label unless `label`)
        self.label = fields.get('label')
        self.fn = fields.get('fn')
        self.popups = fields.get('popups') or []
        self.letter = fields.get('letter', False)


class Builder:
    def __init__(self, res, texts, context):
        self.res = res
        self.texts = texts
        self.ctx = context
        self.table = texts.table(context['locale'])
        self.styles = {}
        self.rows = []
        self.row = None
        self.key_width = 10.0
        self.dropped = {}
        self.note = {}

    # conditions
    def case_matches(self, a):
        c = self.ctx

        def string(name, value):
            return name not in a or value in a[name].split('|')

        def boolean(name, value):
            return name not in a or (a[name] == 'true') == value

        return (string('keyboardLayoutSet', c['layout_set']) and string('keyboardLayoutSetElement', c['element'])
                and string('keyboardTheme', c['theme']) and string('mode', c['mode'])
                and boolean('navigateNext', False) and boolean('navigatePrevious', False) and boolean('passwordInput', False)
                and boolean('clobberSettingsKey', False) and boolean('hasShortcutKey', False)
                and boolean('languageSwitchKeyEnabled', False) and boolean('isMultiLine', False)
                and string('imeAction', 'actionUnspecified') and ('isIconDefined' not in a or True)
                and string('localeCode', c['locale']) and string('languageCode', c['language'])
                and string('countryCode', c['country']) and boolean('isSplitLayout', False))

    # styles
    def style_value(self, style, name, key_attrs):
        """`KeyStyle.getString/getStringArray`: the key's own attribute, else the style's, else its parent's."""
        while True:
            if key_attrs is not None and name in key_attrs:
                raw = self.texts.resolve(key_attrs[name], self.table)
                return split_key_specs(raw) if name in ('moreKeys', 'additionalMoreKeys') else raw
            if style is None:
                return None
            if name in style.attrs:
                value = style.attrs[name]
                return list(value) if isinstance(value, list) else value
            style, key_attrs = style.parent, None

    def style_flags(self, style, key_attrs):
        flags = set(key_attrs.get('keyLabelFlags', '').split('|')) if key_attrs and key_attrs.get('keyLabelFlags') else set()
        while style is not None:
            flags |= style.attrs.get('keyLabelFlags', set())
            style = style.parent
        return flags

    def define_style(self, a):
        parent = self.styles[a['parentStyle']] if 'parentStyle' in a else None
        if 'parentStyle' in a and parent is None:
            raise Unsupported(f'unknown parentStyle {a["parentStyle"]}')
        style = Style(parent)
        for name in ('altCode', 'keySpec', 'keyHintLabel'):
            if name in a:
                style.attrs[name] = self.texts.resolve(a[name], self.table)
        for name in ('moreKeys', 'additionalMoreKeys'):
            if name in a:
                style.attrs[name] = split_key_specs(self.texts.resolve(a[name], self.table))
        if 'keyLabelFlags' in a:
            style.attrs['keyLabelFlags'] = set(a['keyLabelFlags'].split('|'))
        self.styles[a['styleName']] = style

    # layout
    def percent(self, value):
        m = re.fullmatch(r'([0-9.]+)%p', value)
        if not m:
            raise Unsupported(f'width {value!r}')
        return float(m.group(1))

    def walk(self, element, in_row):
        for child in element:
            tag = child.tag
            a = attrs_of(child)
            if tag == 'Row':
                if in_row:
                    raise Unsupported('Row in Row')
                self.start_row(a)
                self.walk(child, True)
                self.end_row()
            elif tag == 'include':
                self.include(a, in_row)
            elif tag == 'switch':
                for case in child:
                    if case.tag == 'default' or (case.tag == 'case' and self.case_matches(attrs_of(case))):
                        self.walk(case, in_row)
                        break
            elif tag == 'key-style':
                self.define_style(a)
            elif tag == 'Key':
                self.key(a, spacer=False)
            elif tag == 'Spacer':
                self.key(a, spacer=True)
            elif tag in ('GridRows',):
                raise Unsupported(f'unsupported tag {tag}')
            elif not isinstance(tag, str):
                continue
            else:
                raise Unsupported(f'unknown tag {tag}')

    def start_row(self, a):
        width = self.percent(a['keyWidth']) if 'keyWidth' in a else self.key_width
        flags = set(a['keyLabelFlags'].split('|')) if a.get('keyLabelFlags') else set()
        self.row = {'x': 0.0, 'stack': [(width, flags)], 'cells': []}

    def end_row(self):
        self.rows.append(self.row['cells'])
        self.row = None

    def include(self, a, in_row):
        name = a['keyboardLayout']
        if not name.startswith('@xml/'):
            raise Unsupported(f'include {name}')
        merge = self.res.xml(name[len('@xml/'):])
        if merge.tag != 'merge':
            raise Unsupported(f'{name} is not a merge')
        if in_row:
            row = self.row
            if 'keyXPos' in a:
                x = self.key_x(a)
                if x > row['x'] + 0.001:
                    row['cells'].append(Cell(row['x'], x - row['x'], 'gap'))
                elif x < row['x'] - 0.001:
                    raise Unsupported('included keys overlap')
                row['x'] = x
            width, flags = row['stack'][-1]
            if 'keyWidth' in a:
                width = self.percent(a['keyWidth'])
            if a.get('keyLabelFlags'):
                flags = flags | set(a['keyLabelFlags'].split('|'))
            row['stack'].append((width, flags))
        self.walk(merge, in_row)
        if in_row:
            self.row['stack'].pop()

    def key_x(self, a):
        value = a.get('keyXPos')
        if value is None:
            return self.row['x']
        x = self.percent(value.lstrip('-'))
        return x if not value.startswith('-') else max(100.0 - x, self.row['x'])

    def key(self, a, spacer):
        row = self.row
        x = self.key_x(a)
        default_width, row_flags = row['stack'][-1]
        if a.get('keyWidth') == 'fillRight':
            width = 100.0 - x
        elif 'keyWidth' in a:
            width = self.percent(a['keyWidth'])
        else:
            width = default_width
        if x > row['x'] + 0.001:
            row['cells'].append(Cell(row['x'], x - row['x'], 'gap'))
        elif x < row['x'] - 0.001:
            raise Unsupported('keys overlap')
        row['x'] = x + width
        if spacer:
            row['cells'].append(Cell(x, width, 'gap'))
            return
        style = self.styles[a['keyStyle']] if 'keyStyle' in a else None
        if 'keyStyle' in a and style is None:
            raise Unsupported(f'unknown key style {a["keyStyle"]}')
        spec_text = self.style_value(style, 'keySpec', a)
        if not spec_text:
            raise Unsupported('empty keySpec')
        spec = Spec(spec_text)
        flags = self.style_flags(style, a) | row_flags
        more = insert_additional(self.style_value(style, 'moreKeys', a), self.style_value(style, 'additionalMoreKeys', a))
        element = self.ctx['element']
        uppercase = element in UPPER_STATES and 'preserveCase' not in flags
        row['cells'].append(self.make_cell(x, width, spec, more, uppercase, a.get('keyStyle')))

    def make_cell(self, x, width, spec, more, uppercase, style_name):
        locale = self.ctx['locale']
        fn = None
        if isinstance(spec.code, str):
            name = spec.code
            if name == 'key_shift':
                fn = 'shift' if spec.icon else ('sym2' if self.ctx['element'] == 'symbols' else 'sym1')
            elif name == 'key_switch_alpha_symbol':
                fn = 'sym1' if self.ctx['element'] == 'alphabet' or self.ctx['element'] in UPPER_STATES else 'abc'
            elif name in CODE_FUNCTIONS:
                fn = CODE_FUNCTIONS[name] or 'dropped:' + name
            else:
                raise Unsupported(f'unknown code {name}')
        if fn is not None:
            label = spec.label if fn in ('sym1', 'sym2', 'abc') else None
            return Cell(x, width, 'fn', fn=fn, label=label)
        if spec.icon:
            if style_name == 'zwnjKeyStyle':
                return Cell(x, width, 'key', text='‌', label='ZWNJ', popups=self.popups(more, uppercase))
            raise Unsupported(f'icon key {spec.icon}')
        text = chr(spec.code) if spec.output is None else spec.output
        label = spec.label
        if uppercase:
            text = upper(text, locale)
            label = upper(label, locale)
        return Cell(x, width, 'key', text=text, label=label, popups=self.popups(more, uppercase),
                    letter=len(text) == 1 and text.isalpha())

    def popups(self, more, uppercase):
        result = []
        for spec_text in more or []:
            if any(spec_text.startswith(flag) for flag in MORE_KEYS_FLAGS):
                continue
            spec = Spec(spec_text)
            if spec.icon or isinstance(spec.code, str):
                self.dropped['popup ' + (spec.icon or spec.code)] = self.dropped.get('popup ' + (spec.icon or spec.code), 0) + 1
                continue
            text = chr(spec.code) if spec.output is None else spec.output
            label = spec.label
            if uppercase:
                text, label = upper(text, self.ctx['locale']), upper(label, self.ctx['locale'])
            # `label|output`: the key shows the label and types the output (mirrored brackets, Arabic ligatures)
            result.append(text if label == text else (label, text))
        return result


def build(res, texts, context, keyboard_name, allow_redundant=True):
    """The rows of one keyboard file in one state: each a list of Cell."""
    builder = Builder(res, texts, context)
    root = res.xml(keyboard_name)
    if root.tag != 'Keyboard':
        raise Unsupported(f'{keyboard_name} is not a Keyboard')
    a = attrs_of(root)
    if 'keyWidth' in a:
        builder.key_width = builder.percent(a['keyWidth'])
    builder.walk(root, False)
    if not allow_redundant:
        remove_redundant(builder.rows)
    return builder.rows, builder


def popup_text(popup):
    return popup if isinstance(popup, str) else popup[1]


def remove_redundant(rows):
    """`MoreKeySpec.removeRedundantMoreKeys`: a long-press key that is a letter of the keyboard itself is dropped."""
    letters = {c.text for row in rows for c in row if c.kind == 'key' and (c.text.isalpha() or len(c.text) > 1)}
    for row in rows:
        for cell in row:
            if cell.kind == 'key':
                cell.popups = [p for p in cell.popups
                               if not ((popup_text(p).isalpha() or len(popup_text(p)) > 1) and popup_text(p) in letters)]


# ── subtypes and layout sets ────────────────────────────────────────────

def read_subtypes(res):
    root = res.xml('method')
    layout_map = {}
    for array in res.values_file('donottranslate.xml').iter('string-array'):
        if array.get('name') == 'locale_and_extra_value_to_keyboard_layout_set_map':
            items = [i.text for i in array]
            layout_map = dict(zip(items[0::2], items[1::2]))
    strings = {s.get('name'): ''.join(s.itertext()) for s in res.values_file('strings.xml').iter('string')}
    subtypes = []
    for subtype in root.iter('subtype'):
        a = {k.replace(ANDROID, ''): v for k, v in subtype.attrib.items()}
        locale = a['imeSubtypeLocale']
        extra = a.get('imeSubtypeExtraValue', '')
        m = re.search(r'KeyboardLayoutSet=([a-z_]+)', extra)
        layout = m.group(1) if m else layout_map[f'{locale}:{extra}']
        subtypes.append({'locale': locale, 'tag': a.get('languageTag'), 'layout': layout, 'label': a.get('label', ''),
                         'strings': strings})
    return subtypes


def read_layout_set(res, name):
    """{element name: (keyboard file, allowRedundantMoreKeys)} of `keyboard_layout_set_<name>.xml`."""
    elements = {}
    for element in res.xml('keyboard_layout_set_' + name).iter('Element'):
        a = attrs_of(element)
        elements[a['elementName']] = (a['elementKeyboard'][len('@xml/'):], a.get('allowRedundantMoreKeys', 'true') == 'true')
    return elements


def language_id(subtype):
    if subtype['tag']:
        return subtype['tag']
    language, _, country = subtype['locale'].partition('_')
    language = LANGUAGE_TAGS.get(language, language)
    return f'{language}-{country}' if country else language


def language_name(subtype, texts):
    strings = subtype['strings']
    locale = subtype['locale']
    if 'subtype_' + locale in strings:
        name = strings['subtype_' + locale]
    elif locale == 'zz':
        name = 'Alphabet'
    else:
        language, _, country = locale.partition('_')
        base = texts.language_names.get(locale)
        if base is None:
            base = texts.language_names.get(language) or LANGUAGE_NAMES[language]
            if country:
                base += f' ({REGION_NAMES[country]})'
        name = base
    label = subtype['label']
    if label.endswith('subtype_generic_compact'):
        name += ' (Compact)'
    elif label.endswith('subtype_generic_traditional'):
        name += ' (Traditional)'
    elif label.endswith('subtype_bulgarian_bds'):
        name += ' (BDS)'
    elif label.endswith('subtype_no_language_qwerty'):
        name = strings['subtype_no_language_qwerty']
    return name


# ── one layout ──────────────────────────────────────────────────────────

class Report:
    def __init__(self):
        self.counts = {}
        self.examples = {}

    def add(self, what, where=''):
        self.counts[what] = self.counts.get(what, 0) + 1
        self.examples.setdefault(what, where)


def context(locale, layout_set, element):
    language, _, country = locale.partition('_')
    return {'locale': locale, 'language': language, 'country': country, 'layout_set': layout_set, 'element': element,
            'theme': 'LXXLight', 'mode': 'text'}


def states_rows(res, texts, locale, layout_set, elements, names, report, tag):
    """The rows of the keyboard in each wanted state; a state without its own element uses the alphabet one."""
    result = {}
    for state in names:
        keyboard, redundant = elements.get(state) or elements['alphabet']
        rows, builder = build(res, texts, context(locale, layout_set, state), keyboard, redundant)
        for what, count in builder.dropped.items():
            report.add(what, tag)
        result[state] = rows
    return result


def trim(cells):
    """Drops the left-out keys: at the end of a row they leave nothing, elsewhere a gap."""
    for cell in cells:
        if cell.kind == 'fn' and cell.fn.startswith('dropped:'):
            cell.kind = 'gap'
    while cells and cells[-1].kind == 'gap':
        cells.pop()
    return cells


def capital(text):
    """webui/src/keyboard.js `capital`: the capital of a key (a sharp s stays one character)."""
    if not isinstance(text, str):
        return tuple(capital(t) for t in text)
    up = text.upper()
    return up if len(up) == len(text) else text


def find(cells, x):
    for cell in cells:
        if abs(cell.x - x) < 0.01:
            return cell
    return None


def merge_states(rows_by_state, report, tag):
    """One row list from the three states of the alphabet: what the plain state says, and what the other two change."""
    plain = rows_by_state['alphabet']
    caps = rows_by_state['alphabetShiftLocked']
    shift = rows_by_state['alphabetManualShifted']
    if not (len(plain) == len(caps) == len(shift)):
        raise Unsupported(f'{tag}: the states have a different number of rows')
    rows = []
    for r, base in enumerate(plain):
        out = []
        for cell in base:
            if cell.kind == 'gap':
                out.append({'w': cell.w})
                continue
            if cell.kind == 'fn':
                key = {'f': cell.fn, 'w': cell.w}
                if cell.label:
                    key['t'] = cell.label
                out.append(key)
                continue
            key = {'k': cell.text, 'w': cell.w}
            if cell.label and cell.label != cell.text:
                key['t'] = cell.label
                report.add('key whose label differs from its text', f'{tag} {cell.text!r} / {cell.label!r}')
            if cell.popups:
                key['l'] = cell.popups
            plain_caps = capital(cell.text)
            plain_caps_popups = [capital(p) for p in cell.popups]
            caps_cell = find(caps[r], cell.x)
            shift_cell = find(shift[r], cell.x)
            if caps_cell is None or caps_cell.kind != 'key' or abs(caps_cell.w - cell.w) > 0.01:
                report.add('key missing in caps lock state', f'{tag} {cell.text!r}')
                caps_cell = cell
            caps_text, caps_popups = (caps_cell.text, caps_cell.popups) if caps_cell is not cell else (plain_caps, plain_caps_popups)
            if caps_text != plain_caps:
                key['u'] = caps_text
            if caps_popups != plain_caps_popups:
                key['L'] = caps_popups
            if shift_cell is None or shift_cell.kind != 'key':
                report.add('key missing in shift state (kept as in caps lock)', f'{tag} {cell.text!r}')
                shift_text, shift_popups = caps_text, caps_popups
            else:
                shift_text, shift_popups = shift_cell.text, shift_cell.popups
            if shift_text != key.get('u', plain_caps):
                key['s'] = shift_text
            if shift_popups != key.get('L', plain_caps_popups):
                key['S'] = shift_popups
            out.append(key)
        rows.append(out)
    return rows


def number_row(res, texts, locale, report):
    """The first row of the PC keyboard (digits and what shift gives), merged over the three states."""
    elements = read_layout_set(res, 'pcqwerty')
    states = states_rows(res, texts, locale, 'pcqwerty', elements, SHIFT_STATES, report, locale + ' number row')
    first = {state: [trim(list(rows[0]))] for state, rows in states.items()}
    return merge_states(first, report, locale + ' number row')[0]


def scaled(rows):
    for row in rows:
        for key in row:
            key['w'] = round(key['w'] * ROW_WEIGHT / 100.0, 2)
    return rows


def alphabet_rows(res, texts, locale, layout_set, report):
    elements = read_layout_set(res, layout_set)
    tag = f'{locale}/{layout_set}'
    states = states_rows(res, texts, locale, layout_set, elements, SHIFT_STATES, report, tag)
    states = {state: [trim(list(row)) for row in rows] for state, rows in states.items()}
    rows = merge_states(states, report, tag)
    if layout_set != 'pcqwerty':
        # the number row has the backspace: the one at the end of the first letter row is not repeated
        first = rows[0]
        if first and first[-1].get('f') == 'bksp':
            first.pop()
        rows.insert(0, number_row(res, texts, locale, report))
    return scaled(rows)


def symbols_pages(res, texts, locale, layout_set, report):
    elements = read_layout_set(res, layout_set)
    pages = []
    for state in ('symbols', 'symbolsShifted'):
        keyboard, redundant = elements[state]
        rows, builder = build(res, texts, context(locale, layout_set, state), keyboard, redundant)
        for what in builder.dropped:
            report.add(what, f'{locale} symbols')
        page = []
        for row in rows:
            out = []
            for cell in trim(list(row)):
                if cell.kind == 'gap':
                    out.append({'w': cell.w})
                elif cell.kind == 'fn':
                    key = {'f': cell.fn, 'w': cell.w}
                    if cell.label:
                        key['t'] = cell.label
                    out.append(key)
                else:
                    key = {'k': cell.text, 'w': cell.w}
                    if cell.label != cell.text:
                        key['t'] = cell.label
                    if cell.popups:
                        key['l'] = cell.popups
                    out.append(key)
            page.append(out)
        pages.append(page)
    return scaled_pages(pages)


def scaled_pages(pages):
    for page in pages:
        scaled(page)
    return pages


# ── output ──────────────────────────────────────────────────────────────

def dump(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    root, out = argv[1], argv[2]
    res = Resources(root)
    texts = Texts(res.texts_java)
    subtypes = read_subtypes(res)
    report = Report()
    os.makedirs(os.path.join(out, 'layouts'), exist_ok=True)
    languages = {}  # id -> {'name', 'locale', 'layouts': [layout set]}
    for subtype in subtypes:
        lang = languages.setdefault(language_id(subtype), {'name': language_name(subtype, texts), 'locale': subtype['locale'], 'layouts': []})
        if lang['locale'] != subtype['locale']:
            raise Unsupported(f'two locales for {language_id(subtype)}')
        if subtype['layout'] not in lang['layouts']:
            lang['layouts'].append(subtype['layout'])
    for lang_id in GENERIC_FOR:
        layouts = languages[lang_id]['layouts']
        layouts.extend(l for l in GENERIC_LAYOUTS if l not in layouts)
    reference = None
    index = []
    for lang_id, lang in languages.items():
        locale = lang['locale']
        variants = {layout: alphabet_rows(res, texts, locale, layout, report) for layout in lang['layouts']}
        symbols = symbols_pages(res, texts, locale, lang['layouts'][0], report)
        record = {'lang': lang_id, 'variants': variants}
        if lang_id == 'en-US':
            reference = symbols
            dump(os.path.join(out, 'symbols.json'), symbols)
        index.append((lang_id, lang['name'], list(variants), symbols, record))
    for lang_id, name, variants, symbols, record in index:
        if symbols != reference:
            record['symbols'] = symbols
            report.add('language with its own symbols pages', lang_id)
        dump(os.path.join(out, 'layouts', lang_id + '.json'), record)
    dump(os.path.join(out, 'index.json'), [{'id': i, 'name': n, 'variants': v} for i, n, v, _, _ in index])
    with open(os.path.join(out, 'NOTICE'), 'w', encoding='utf-8') as f:
        f.write(NOTICE)
    print(f'{len(index)} languages, {sum(len(v) for _, _, v, _, _ in index)} layouts')
    for what, count in sorted(report.counts.items()):
        print(f'  {count:4d}  {what}  (e.g. {report.examples[what]})')


if __name__ == '__main__':
    main(sys.argv)
