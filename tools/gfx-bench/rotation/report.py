#!/usr/bin/env python3
"""
Turns the rows of rotation-bench.py into one table per board and metric.

    report.py DIR
"""
import collections
import glob
import json
import os
import statistics
import sys

METRICS = [('fps', 'frames drawn per second'), ('slow_per_s', 'frames slower than 25 ms per second'),
           ('dropped_pct', 'dropped video frames %'), ('viewer_cpu', 'viewer CPU % of one core'),
           ('compositor_cpu', 'compositor CPU % of one core'), ('gpu', 'busiest GPU engine %')]


def value(row, key):
    if key == 'gpu':
        return max(row['gpu_busy'].values()) if row.get('gpu_busy') else None
    return row.get(key)


for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.jsonl'))):
    rows = [json.loads(l) for l in open(path)]
    configs = list(dict.fromkeys(r['config'] for r in rows))
    workloads = list(dict.fromkeys(r['workload'] for r in rows))
    cell = collections.defaultdict(list)
    for r in rows:
        cell[(r['workload'], r['config'])].append(r)
    print(f'\n## {os.path.basename(path)[:-6]}  ({rows[0].get("size", "")} first row)')
    for key, title in METRICS:
        print(f'\n{title}\n')
        print('| workload | ' + ' | '.join(configs) + ' |')
        print('|---|' + '---|' * len(configs))
        for w in workloads:
            cells = []
            for c in configs:
                vals = [value(r, key) for r in cell[(w, c)] if value(r, key) is not None]
                cells.append(f'{statistics.mean(vals):.1f}' if vals else '-')
            print(f'| {w} | ' + ' | '.join(cells) + ' |')
