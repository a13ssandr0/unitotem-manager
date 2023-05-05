from typing import Optional


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