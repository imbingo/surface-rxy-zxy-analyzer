"""Adaptive topology and continuous-surface region growing for smart ROI v2."""

from collections import deque

import numpy as np
from scipy.spatial import Delaunay, QhullError, cKDTree


SENSITIVITY = {
    'strict': {
        'edge_factor': 1.55, 'residual_factor': 0.75, 'normal_deg': 5.0,
        'fast_ratio': 0.18, 'reject_ratio': 1.00, 'refresh_hops': 6,
    },
    'standard': {
        'edge_factor': 2.20, 'residual_factor': 1.00, 'normal_deg': 12.0,
        'fast_ratio': 0.32, 'reject_ratio': 1.10, 'refresh_hops': 10,
    },
    'loose': {
        'edge_factor': 3.00, 'residual_factor': 1.50, 'normal_deg': 24.0,
        'fast_ratio': 0.45, 'reject_ratio': 1.25, 'refresh_hops': 16,
    },
}


def _edge_pairs_to_adjacency(point_count, edges):
    buckets = [[] for _ in range(int(point_count))]
    for left, right in np.asarray(edges, dtype=np.int64):
        if left == right:
            continue
        buckets[int(left)].append(int(right))
        buckets[int(right)].append(int(left))
    return [np.asarray(sorted(set(items)), dtype=np.int32) for items in buckets]


def _matrix_edges(row_values, col_values):
    cells = {(int(row), int(col)): index
             for index, (row, col) in enumerate(zip(row_values, col_values))}
    edges = []
    for (row, col), index in cells.items():
        for dr, dc in ((0, 1), (1, -1), (1, 0), (1, 1)):
            other = cells.get((row + dr, col + dc))
            if other is not None:
                edges.append((index, other))
    return np.asarray(edges, dtype=np.int64).reshape(-1, 2)


def _edge_lengths(xy, edges):
    if len(edges) == 0:
        return np.empty(0, dtype=float)
    return np.linalg.norm(xy[edges[:, 0]] - xy[edges[:, 1]], axis=1)


def _prune_edges_by_local_scale(xy, edges, edge_factor):
    if len(edges) == 0:
        return edges, 0.0
    lengths = _edge_lengths(xy, edges)
    positive = lengths[np.isfinite(lengths) & (lengths > 0)]
    if positive.size == 0:
        return np.empty((0, 2), dtype=np.int64), 0.0
    incident = [[] for _ in range(len(xy))]
    for edge_index, (left, right) in enumerate(edges):
        distance = float(lengths[edge_index])
        if np.isfinite(distance) and distance > 0:
            incident[int(left)].append(distance)
            incident[int(right)].append(distance)
    global_scale = float(np.median(positive))
    global_cap = float(np.percentile(positive, 92)) * float(edge_factor)
    local_scale = np.full(len(xy), global_scale, dtype=float)
    for index, values in enumerate(incident):
        if values:
            local_scale[index] = float(np.percentile(values, 75))
    threshold = np.maximum(local_scale[edges[:, 0]], local_scale[edges[:, 1]]) * float(edge_factor)
    keep = (lengths <= threshold) & (lengths <= max(global_cap, global_scale * edge_factor))
    return edges[keep], global_scale


def _delaunay_edges(xy, edge_factor):
    if len(xy) < 3:
        raise ValueError('点数不足3个')
    if len(np.unique(xy, axis=0)) != len(xy):
        raise ValueError('存在重复XY')
    centered = xy - np.mean(xy, axis=0)
    covariance = np.cov(centered.T)
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues[-1] <= 0 or eigenvalues[0] / eigenvalues[-1] < 1e-8:
        raise ValueError('点云近共线')
    try:
        triangles = Delaunay(xy).simplices
    except QhullError as exc:
        raise ValueError(f'Delaunay失败: {exc.__class__.__name__}') from exc
    edges = np.vstack((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [0, 2]]))
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    return _prune_edges_by_local_scale(xy, edges, edge_factor)


def _choose_adaptive_knn_k(tree, xy):
    levels = (8, 16, 32, 64)
    max_k = min(len(xy), levels[-1] + 1)
    if max_k <= 2:
        return 1
    sample_count = min(len(xy), 4000)
    sample_index = np.linspace(0, len(xy) - 1, sample_count, dtype=int)
    _, neighbors = tree.query(xy[sample_index], k=max_k)
    if neighbors.ndim == 1:
        neighbors = neighbors[:, None]
    for level in levels:
        count = min(level + 1, neighbors.shape[1])
        covered = 0
        for sample_row, source_index in enumerate(sample_index):
            ids = np.asarray(neighbors[sample_row, 1:count], dtype=int)
            delta = xy[ids] - xy[source_index]
            delta = delta[np.linalg.norm(delta, axis=1) > 0]
            if len(delta) < 3:
                continue
            covariance = np.cov(delta.T)
            eigenvalues = np.linalg.eigvalsh(covariance)
            if eigenvalues[-1] > 0 and eigenvalues[0] / eigenvalues[-1] >= 0.015:
                covered += 1
        if covered >= max(1, int(sample_count * 0.8)):
            return min(level, len(xy) - 1)
    return min(64, len(xy) - 1)


def _adaptive_knn_edges(xy, edge_factor):
    if len(xy) < 2:
        return np.empty((0, 2), dtype=np.int64), 0.0, 0
    tree = cKDTree(xy)
    neighbor_count = _choose_adaptive_knn_k(tree, xy)
    query_k = min(len(xy), neighbor_count + 1)
    edge_chunks = []
    chunk_size = 20000
    for start in range(0, len(xy), chunk_size):
        end = min(len(xy), start + chunk_size)
        distances, indices = tree.query(xy[start:end], k=query_k)
        if indices.ndim == 1:
            indices = indices[:, None]
            distances = distances[:, None]
        selected = []
        for local_row, source_index in enumerate(range(start, end)):
            ids = np.asarray(indices[local_row, 1:], dtype=int)
            dists = np.asarray(distances[local_row, 1:], dtype=float)
            valid = (ids != source_index) & np.isfinite(dists) & (dists > 0)
            ids = ids[valid]; dists = dists[valid]
            if len(ids) == 0:
                continue
            chosen = list(ids[:min(4, len(ids))])
            delta = xy[ids] - xy[source_index]
            sectors = np.floor((np.arctan2(delta[:, 1], delta[:, 0]) + np.pi) /
                               (2.0 * np.pi / 8.0)).astype(int)
            sectors = np.clip(sectors, 0, 7)
            for sector in range(8):
                positions = np.flatnonzero(sectors == sector)
                if len(positions):
                    chosen.append(int(ids[positions[np.argmin(dists[positions])]]))
            for target in set(chosen):
                selected.append((source_index, target))
        if selected:
            edge_chunks.append(np.asarray(selected, dtype=np.int64))
    edges = (np.vstack(edge_chunks) if edge_chunks else
             np.empty((0, 2), dtype=np.int64))
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    pruned, spacing = _prune_edges_by_local_scale(xy, edges, edge_factor)
    return pruned, spacing, neighbor_count


def build_adaptive_topology(x, y, matrix_rc=None, sensitivity='standard', delaunay_limit=150000):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xy = np.column_stack([x, y])
    if not np.isfinite(xy).all():
        raise ValueError('拓扑输入包含无效XY')
    config = SENSITIVITY.get(str(sensitivity), SENSITIVITY['standard'])
    fallback_reason = ''
    if matrix_rc is not None:
        rows, cols = matrix_rc
        edges = _matrix_edges(np.asarray(rows), np.asarray(cols))
        lengths = _edge_lengths(xy, edges)
        positive = lengths[np.isfinite(lengths) & (lengths > 0)]
        spacing = float(np.median(positive)) if positive.size else 0.0
        return {
            'adjacency': _edge_pairs_to_adjacency(len(xy), edges),
            'method': 'matrix8',
            'topology': '矩阵8邻域',
            'local_spacing_mm': spacing,
            'fallback_reason': '',
        }
    if len(xy) <= int(delaunay_limit):
        try:
            edges, spacing = _delaunay_edges(xy, config['edge_factor'])
            if len(edges) == 0:
                raise ValueError('剪枝后无有效边')
            return {
                'adjacency': _edge_pairs_to_adjacency(len(xy), edges),
                'method': 'delaunay',
                'topology': 'Delaunay自适应邻接',
                'local_spacing_mm': float(spacing),
                'fallback_reason': '',
            }
        except Exception as exc:
            fallback_reason = str(exc)
    else:
        fallback_reason = f'点数 {len(xy):,} 超过 Delaunay 上限 {int(delaunay_limit):,}'
    edges, spacing, neighbor_count = _adaptive_knn_edges(xy, config['edge_factor'])
    return {
        'adjacency': _edge_pairs_to_adjacency(len(xy), edges),
        'method': 'adaptive_knn',
        'topology': f'自适应kNN(k={neighbor_count})',
        'local_spacing_mm': float(spacing),
        'fallback_reason': fallback_reason,
    }


def _graph_neighborhood(adjacency, center, target=24, max_depth=3):
    visited = {int(center)}
    frontier = [int(center)]
    for _ in range(int(max_depth)):
        new_frontier = []
        for item in frontier:
            for neighbor in adjacency[item]:
                neighbor = int(neighbor)
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_frontier.append(neighbor)
        frontier = new_frontier
        if len(visited) >= int(target) or not frontier:
            break
    return np.asarray(sorted(visited), dtype=int)


def _robust_local_plane(x, y, z, indices):
    indices = np.asarray(indices, dtype=int)
    if len(indices) < 3:
        return None, None
    xx = x[indices]; yy = y[indices]; zz = z[indices]
    x0 = float(np.mean(xx)); y0 = float(np.mean(yy))
    design = np.column_stack([xx - x0, yy - y0, np.ones(len(indices))])
    coeffs, *_ = np.linalg.lstsq(design, zz, rcond=None)
    residual = zz - design @ coeffs
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    if mad > 1e-12 and len(indices) >= 8:
        keep = np.abs(residual - median) <= 3.5 * 1.4826 * mad
        if int(np.sum(keep)) >= 3:
            coeffs, *_ = np.linalg.lstsq(design[keep], zz[keep], rcond=None)
    a, b, c0 = [float(value) for value in coeffs]
    c = c0 - a * x0 - b * y0
    normal = np.array([-a, -b, 1.0], dtype=float)
    normal /= max(float(np.linalg.norm(normal)), 1e-12)
    return np.array([a, b, c], dtype=float), normal


def grow_surface_roi(x, y, z, seed_x, seed_y, tolerance_mm, topology,
                     mode='surface_following', sensitivity='standard',
                     candidate_mask=None, progress=None, cancel_event=None, stats=None):
    """Grow one connected surface with a fast interior path and precise boundary path.

    ``candidate_mask`` is a hard gate only; it never changes topology. ``stats`` is
    populated in place so callers can profile behavior without changing the mask API.
    """
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    adjacency = topology['adjacency']
    metrics = stats if stats is not None else {}
    metrics.clear()
    metrics.update({
        'points': int(len(x)), 'fast_accept': 0, 'fast_reject': 0,
        'slow_path': 0, 'local_plane_fits': 0, 'processed': 0,
        'mode': str(mode), 'sensitivity': str(sensitivity),
    })
    if len(x) == 0:
        return np.zeros(0, dtype=bool)
    candidate = (np.ones(len(x), dtype=bool) if candidate_mask is None else
                 np.asarray(candidate_mask, dtype=bool).copy())
    if len(candidate) != len(x):
        raise ValueError('candidate_mask长度与点数不一致')
    candidate &= np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    seed = int(np.argmin((x - float(seed_x)) ** 2 + (y - float(seed_y)) ** 2))
    if not candidate[seed]:
        return np.zeros(len(x), dtype=bool)
    config = SENSITIVITY.get(str(sensitivity), SENSITIVITY['standard'])
    tolerance = max(float(tolerance_mm), 1e-12) * float(config['residual_factor'])
    strict = str(mode) == 'plane_residual'
    seed_neighborhood = _graph_neighborhood(adjacency, seed, target=36, max_depth=5)
    seed_neighborhood = seed_neighborhood[candidate[seed_neighborhood]]
    seed_plane, seed_normal = _robust_local_plane(x, y, z, seed_neighborhood)
    metrics['local_plane_fits'] += 1
    if seed_plane is None:
        result = np.zeros(len(x), dtype=bool); result[seed] = True
        return result

    if strict:
        residual = np.abs(z - (seed_plane[0] * x + seed_plane[1] * y + seed_plane[2]))
        candidate &= np.isfinite(residual) & (residual <= tolerance)
        visited = np.zeros(len(x), dtype=bool)
        if not candidate[seed]:
            candidate[seed] = True
        visited[seed] = True
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            metrics['processed'] += 1
            if cancel_event is not None and metrics['processed'] % 1024 == 0 and cancel_event.is_set():
                from .workers import TaskCancelled
                raise TaskCancelled()
            for neighbor in adjacency[current]:
                neighbor = int(neighbor)
                if not visited[neighbor] and candidate[neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)
        metrics['slow_path'] = int(visited.sum())
        if progress is not None:
            progress(100, int(metrics['processed']), int(len(x)))
        return visited

    normal_limit = np.deg2rad(float(config['normal_deg']))
    fast_limit = tolerance * float(config['fast_ratio'])
    reject_limit = tolerance * float(config['reject_ratio'])
    refresh_hops = max(1, int(config['refresh_hops']))
    metrics.update({
        'effective_tolerance_mm': float(tolerance),
        'fast_threshold_mm': float(fast_limit),
        'reject_threshold_mm': float(reject_limit),
        'normal_limit_deg': float(config['normal_deg']),
        'refresh_hops': int(refresh_hops),
        'edge_factor': float(config['edge_factor']),
    })
    plane_cache = {seed: (seed_plane, seed_normal)}

    def local_plane(index):
        value = plane_cache.get(index)
        if value is None:
            neighborhood = _graph_neighborhood(adjacency, index, target=24, max_depth=4)
            neighborhood = neighborhood[candidate[neighborhood]]
            value = _robust_local_plane(x, y, z, neighborhood)
            plane_cache[index] = value
            metrics['local_plane_fits'] += 1
        return value

    accepted = np.zeros(len(x), dtype=bool)
    blocked = ~candidate
    accepted[seed] = True
    # queue items carry the reusable local trend and its propagation age.
    queue = deque([(seed, seed_plane, seed_normal, 0)])
    while queue:
        current, current_plane, current_normal, plane_age = queue.popleft()
        metrics['processed'] += 1
        if cancel_event is not None and metrics['processed'] % 1024 == 0 and cancel_event.is_set():
            from .workers import TaskCancelled
            raise TaskCancelled()
        if progress is not None and metrics['processed'] % 2048 == 0:
            progress(min(99, int(100 * metrics['processed'] / max(len(x), 1))),
                     int(metrics['processed']), int(len(x)))
        if plane_age >= refresh_hops:
            refreshed_plane, refreshed_normal = local_plane(current)
            if refreshed_plane is not None:
                current_plane, current_normal = refreshed_plane, refreshed_normal
                plane_age = 0
        if current_plane is None:
            continue
        for neighbor in adjacency[current]:
            neighbor = int(neighbor)
            if accepted[neighbor] or blocked[neighbor]:
                continue
            predicted = (float(z[current]) + current_plane[0] * float(x[neighbor] - x[current])
                         + current_plane[1] * float(y[neighbor] - y[current]))
            residual = abs(float(z[neighbor] - predicted))
            if residual < fast_limit:
                accepted[neighbor] = True
                metrics['fast_accept'] += 1
                queue.append((neighbor, current_plane, current_normal, plane_age + 1))
                continue
            if residual > reject_limit:
                blocked[neighbor] = True
                metrics['fast_reject'] += 1
                continue
            metrics['slow_path'] += 1
            neighbor_plane, neighbor_normal = local_plane(neighbor)
            if neighbor_plane is None:
                blocked[neighbor] = True
                continue
            cosine = float(np.clip(np.dot(current_normal, neighbor_normal), -1.0, 1.0))
            if residual > tolerance or np.arccos(cosine) > normal_limit:
                blocked[neighbor] = True
                continue
            accepted[neighbor] = True
            queue.append((neighbor, neighbor_plane, neighbor_normal, 0))
    if progress is not None:
        progress(100, int(metrics['processed']), int(len(x)))
    metrics['selected'] = int(accepted.sum())
    metrics['fast_accept_ratio'] = float(metrics['fast_accept'] / max(metrics['selected'] - 1, 1))
    return accepted
