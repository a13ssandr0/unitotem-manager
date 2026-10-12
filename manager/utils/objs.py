__all__ = [
    "dict_sort",
    "parse_datetime",
    "unflatten",
    "find_by_attribute",
    "flatten",
]

from collections import OrderedDict
from datetime import datetime
from typing import Optional, Tuple, Any

from jinja2 import Undefined


# https://github.com/ansible/ansible/blob/0830b6905996fb02eefcba79a9b055961e251078/lib/ansible/plugins/filter/core.py#L476
def flatten(mylist, levels=None, skip_nulls=True, remove_duplicates=True):

    ret = []
    for element in mylist:
        if skip_nulls and (element in (None, 'None', 'null') or isinstance(element, Undefined)):
            # ignore null items
            continue
        elif isinstance(element, (list, tuple)):
            if levels is None:
                ret.extend(flatten(element, skip_nulls=skip_nulls))
            elif levels >= 1:
                # decrement as we go down the stack
                ret.extend(flatten(element, levels=(int(levels) - 1), skip_nulls=skip_nulls))
            else:
                ret.append(element)
        else:
            ret.append(element)

    if remove_duplicates:
        ret = list(set(ret))

    return ret


def find_by_attribute(l:list, key, value, default:Optional[int]=None):
    for index, elem in enumerate(l):
        if key in elem and elem[key] == value:
            return index
    else:
        if default is None:
            raise ValueError(f'No element with {repr(key)}: {repr(value)} in list')
        else:
            return default


def dict_sort(value):
    def sorter(item: Tuple[str, Any]) -> str:
        if item[0][0].islower():
            return '.' + item[0]
        return item[0]

    result = OrderedDict()

    for k, v in sorted(value.items(), key=sorter):
        if isinstance(v, dict):
            result.update([(k, dict_sort(v))])
        else:
            result[k] = v
    return result


def unflatten(flat: dict, separator: str = '/') -> dict:
    """{'a/b': 1, 'a/c': 2} -> {'a': {'b': 1, 'c': 2}}."""
    result: dict = {}
    for key, value in flat.items():
        *parents, leaf = key.split(separator)
        node = result
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return result


def parse_datetime(value) -> Optional[datetime]:
    """A datetime from what the web UI or a saved file holds: a datetime, an ISO 8601 string, a Unix timestamp
    (number or numeric string), or anything python-dateutil understands. None and '' give None."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    text = str(value).strip()
    try:
        return datetime.fromtimestamp(float(text))
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        from dateutil import parser
        return parser.parse(text)
