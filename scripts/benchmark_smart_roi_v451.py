"""Reproducible V4.5.0-reference versus V4.5.1 Smart ROI benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import surface_analyzer.smart_roi as smart


def grow_v450_reference(x, y, z, seed_x, seed_y, tolerance_mm, topology,
                        sensitivity='standard'):
    adjacency = topology['adjacency']
    seed = int(np.argmin((x - seed_x) ** 2 + (y - seed_y) ** 2))
    config = smart.SENSITIVITY[sensitivity]
    tolerance = max(float(tolerance_mm), 1e-12) * float(config['residual_factor'])
    seed_local = smart._graph_neighborhood(adjacency, seed, target=36, max_depth=5)
    seed_plane, seed_normal = smart._robust_local_plane(x, y, z, seed_local)
    fits = 1
    cache = {seed: (seed_plane, seed_normal)}

    def local_plane(index):
        nonlocal fits
        value = cache.get(index)
        if value is None:
            neighborhood = smart._graph_neighborhood(adjacency, index, target=24, max_depth=4)
            value = smart._robust_local_plane(x, y, z, neighborhood)
            cache[index] = value
            fits += 1
        return value

    keep = np.zeros(len(x), dtype=bool); keep[seed] = True
    queue = deque([seed])
    normal_limit = np.deg2rad(float(config['normal_deg']))
    while queue:
        current = queue.popleft()
        plane, normal = local_plane(current)
        if plane is None:
            continue
        for neighbor in adjacency[current]:
            neighbor = int(neighbor)
            if keep[neighbor]:
                continue
            predicted = plane[0] * x[neighbor] + plane[1] * y[neighbor] + plane[2]
            if abs(float(z[neighbor] - predicted)) > tolerance:
                continue
            next_plane, next_normal = local_plane(neighbor)
            if next_plane is None:
                continue
            cosine = float(np.clip(np.dot(normal, next_normal), -1.0, 1.0))
            if np.arccos(cosine) <= normal_limit:
                keep[neighbor] = True
                queue.append(neighbor)
    return keep, fits


def grid_for_size(target):
    rows = max(3, int(np.sqrt(target * 0.75)))
    cols = max(3, int(np.ceil(target / rows)))
    return rows, cols


def run_case(target):
    rows, cols = grid_for_size(target)
    yy, xx = np.mgrid[0:rows, 0:cols]
    x = xx.ravel() * 0.035
    y = yy.ravel() * 0.045
    z = 1.0 + 0.00035 * (x - np.mean(x)) ** 2 + 0.00028 * (y - np.mean(y)) ** 2
    seed = (rows // 2) * cols + cols // 2

    started = time.perf_counter()
    topology = smart.build_adaptive_topology(
        x, y, matrix_rc=(yy.ravel(), xx.ravel()), sensitivity='standard')
    topology_seconds = time.perf_counter() - started

    started = time.perf_counter()
    old_mask, old_fits = grow_v450_reference(
        x, y, z, x[seed], y[seed], 0.005, topology)
    old_seconds = time.perf_counter() - started

    stats = {}
    started = time.perf_counter()
    new_mask = smart.grow_surface_roi(
        x, y, z, x[seed], y[seed], 0.005, topology,
        mode='surface_following', sensitivity='standard', stats=stats)
    new_seconds = time.perf_counter() - started
    intersection = int(np.sum(old_mask & new_mask))
    union = int(np.sum(old_mask | new_mask))
    return {
        'requested_points': int(target), 'points': int(len(x)),
        'rows': int(rows), 'cols': int(cols),
        'topology_seconds': topology_seconds,
        'v450_grow_seconds': old_seconds,
        'v451_grow_seconds': new_seconds,
        'speedup': old_seconds / max(new_seconds, 1e-12),
        'v450_selected': int(old_mask.sum()), 'v451_selected': int(new_mask.sum()),
        'mask_iou': intersection / max(union, 1),
        'v450_local_plane_fits': int(old_fits),
        **stats,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', nargs='+', type=int, default=[10000, 50000, 120000])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = {'cases': [run_case(size) for size in args.sizes]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
