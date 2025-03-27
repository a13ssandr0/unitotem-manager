__all__ = [
    "do_dictsort",
    "find_by_attribute",
    "flatten",
]



from typing import Optional, Mapping, List, Tuple, Any

from jinja2 import Undefined
from jinja2.filters import K, V


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


def do_dictsort(
    value: Mapping[K, V],
    case_sensitive: bool = False,
    reverse: bool = False,
) -> List[Tuple[K, V]]:

    def sort_func(item: Tuple[Any, Any]) -> Any:
        value = item[0]

        if not case_sensitive and isinstance(value, str):
            value = value.lower()

        return value

    return sorted(value.items(), key=sort_func, reverse=reverse)
