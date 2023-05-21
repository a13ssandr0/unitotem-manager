from typing import Optional
from jinja2 import Undefined


# https://github.com/ansible/ansible/blob/0830b6905996fb02eefcba79a9b055961e251078/lib/ansible/plugins/filter/core.py#L476
def flatten(mylist, levels=None, skip_nulls=True):

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

    return ret


def find_by_attribute(l:list, key, value, default:Optional[int]=None):
    for index, elem in enumerate(l):
        if key in elem and elem[key] == value:
            return index
    else:
        if default == None:
            raise ValueError(f'No element with {repr(key)}: {repr(value)} in list')
        else:
            return default

class objdict(dict):
    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError("No such attribute: " + name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        if name in self:
            del self[name]
        else:
            raise AttributeError("No such attribute: " + name)