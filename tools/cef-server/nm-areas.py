"""Attributes the size of the defined symbols of a binary to areas, by the leading namespace of the demangled name."""
import collections, re, subprocess, sys
nm, lib = sys.argv[1:3]
out = subprocess.run([nm, '-S', '-C', '--defined-only', lib], capture_output=True, text=True, errors='replace').stdout
by = collections.Counter(); total = 0; count = collections.Counter()
ns = re.compile(r'^(?:(?:non-virtual thunk to|virtual thunk to|vtable for|typeinfo for|typeinfo name for|guard variable for|construction vtable for)\s+)?([A-Za-z_][A-Za-z0-9_]*)::')
for line in out.splitlines():
    parts = line.split(None, 3)
    if len(parts) < 4:
        continue
    try:
        size = int(parts[1], 16)
    except ValueError:
        continue
    name = parts[3]
    m = ns.match(name)
    key = m.group(1) if m else ('(c-style) ' + name[:14] if False else '(no namespace)')
    by[key] += size; count[key] += 1; total += size
print(f'total symbol bytes: {total/1e6:.1f} MB in {sum(count.values())} symbols')
for key, size in by.most_common(45):
    print(f'{size/1e6:8.1f} MB  {100*size/total:5.1f}%  {key}  ({count[key]})')
