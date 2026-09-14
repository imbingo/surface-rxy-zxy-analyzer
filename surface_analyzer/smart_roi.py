"""Adaptive topology and continuous-surface region growing for smart ROI v2."""

from collections import deque
from contextvars import ContextVar
import time

import numpy as np
from scipy import ndimage
from scipy.spatial import Delaunay, QhullError, cKDTree

from .config import PERFORMANCE_POLICY, perf_event


class Matrix8Adjacency:
    """Implicit 8-neighbour lattice without one Python adjacency object per point."""
    __slots__ = ('grid', 'point_rows', 'point_cols', 'health')

    def __init__(self, grid, point_rows, point_cols, health):
        self.grid = grid
        self.point_rows = point_rows
        self.point_cols = point_cols
        self.health = health

    def __len__(self):
        return len(self.point_rows)

    def __getitem__(self, index):
        row = int(self.point_rows[index]); col = int(self.point_cols[index])
        values = []
        for dr in (-1, 0, 1):
            rr = row + dr
            if rr < 0 or rr >= self.grid.shape[0]:
                continue
            for dc in (-1, 0, 1):
                cc = col + dc
                if (dr == 0 and dc == 0) or cc < 0 or cc >= self.grid.shape[1]:
                    continue
                value = int(self.grid[rr, cc])
                if value >= 0:
                    values.append(value)
        return tuple(sorted(values))


def _implicit_matrix_topology(x, y, rows, cols):
    _topology_progress(5, '正在验证矩阵坐标')
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    if len(rows) != len(x) or len(cols) != len(x) or not len(rows):
        raise ValueError('矩阵拓扑坐标长度不一致')
    row0, col0 = int(rows.min()), int(cols.min())
    rr, cc = rows-row0, cols-col0
    height, width = int(rr.max())+1, int(cc.max())+1
    cell_count = height*width
    # Avoid allocating a huge mostly-empty bounding box for irregular inputs.
    if cell_count > max(len(rows)*4, len(rows)+1_000_000):
        raise ValueError('矩阵坐标过于稀疏，不能使用隐式规则拓扑')
    flat = rr*width+cc
    if len(np.unique(flat)) != len(flat):
        raise ValueError('矩阵拓扑存在重复坐标')
    grid = np.full(cell_count, -1, dtype=np.int32)
    grid[flat] = np.arange(len(rows), dtype=np.int32)
    grid = grid.reshape(height, width)
    _topology_progress(25, '正在建立隐式矩阵索引')
    occupancy = grid >= 0
    labels, component_count = ndimage.label(occupancy, structure=np.ones((3, 3), dtype=np.uint8))
    _topology_progress(45, '正在检查矩阵连通域')
    sizes = np.bincount(labels.ravel())[1:] if component_count else np.empty(0, dtype=int)
    degrees = np.zeros(len(rows), dtype=np.uint8)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == dc == 0:
                continue
            target_r, target_c = rr+dr, cc+dc
            valid = ((target_r >= 0) & (target_r < height) &
                     (target_c >= 0) & (target_c < width))
            hit = np.zeros(len(rows), dtype=bool)
            hit[valid] = grid[target_r[valid], target_c[valid]] >= 0
            degrees += hit
    _topology_progress(70, '正在统计矩阵邻接关系')
    health = {
        'point_count': int(len(rows)),
        'edge_count': int(degrees.sum()//2),
        'isolated_ratio': float(np.mean(degrees == 0)),
        'median_degree': float(np.median(degrees)),
        'largest_component_ratio': float(sizes.max()/len(rows)) if len(sizes) else 0.0,
    }
    adjacency = Matrix8Adjacency(grid, rr.astype(np.int32), cc.astype(np.int32), health)
    # Match the historical matrix-edge median exactly, including diagonals.
    distances = []
    for dr, dc in ((0, 1), (1, -1), (1, 0), (1, 1)):
        tr, tc = rr+dr, cc+dc
        valid = (tr < height) & (tc < width)
        source = np.flatnonzero(valid)
        target = grid[tr[valid], tc[valid]]
        hit = target >= 0
        if np.any(hit):
            source, target = source[hit], target[hit]
            distances.append(np.hypot(np.asarray(x)[source]-np.asarray(x)[target],
                                      np.asarray(y)[source]-np.asarray(y)[target]))
    positive = np.concatenate(distances) if distances else np.empty(0)
    positive = positive[np.isfinite(positive) & (positive > 0)]
    spacing = float(np.median(positive)) if len(positive) else 0.0
    _topology_progress(90, '隐式矩阵拓扑已生成')
    return adjacency, spacing, health


# Scoped to one build/thread, including matrix fallback paths and helper calls.
_topology_reporter = ContextVar('topology_reporter', default=None)


def _topology_progress(value=0, message='', done=None, total=None):
    reporter = _topology_reporter.get()
    if reporter is not None:
        reporter(value, message, done, total)


def build_adaptive_topology(x, y, matrix_rc=None, sensitivity='standard',
                            delaunay_limit=150000, progress=None, cancel_event=None):
    started = time.perf_counter()
    highest = 0

    def report(value, message, done, total):
        nonlocal highest
        if cancel_event is not None and cancel_event.is_set():
            from .workers import TaskCancelled
            raise TaskCancelled()
        if progress is not None and message:
            highest = max(highest, min(100, int(value)))
            counts = f' {done:,} / {total:,}' if done is not None else ''
            progress(highest, f'{message}{counts} | 已用时 {time.perf_counter()-started:.1f}s')

    token = _topology_reporter.set(report)
    try:
        _topology_progress(0, '正在准备智能抓面邻接关系')
        result = _build_adaptive_topology(x, y, matrix_rc, sensitivity, delaunay_limit)
        perf_event('Topology', time.perf_counter()-started,
                   method=result.get('method'), points=len(x))
        _topology_progress(100, '智能抓面邻接关系已就绪')
        return result
    finally:
        _topology_reporter.reset(token)


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
    _topology_progress(82, '正在生成点邻接表')
    buckets = [[] for _ in range(int(point_count))]
    for index, (left, right) in enumerate(np.asarray(edges, dtype=np.int64)):
        if index % 20000 == 0:
            _topology_progress(82 + 5 * index / max(len(edges), 1), '正在生成点邻接表', index, len(edges))
        if left == right:
            continue
        buckets[int(left)].append(int(right))
        buckets[int(right)].append(int(left))
    adjacency = []
    for index, items in enumerate(buckets):
        if index % 20000 == 0:
            _topology_progress(87 + 3 * index / max(point_count, 1), '正在整理点邻接表', index, point_count)
        adjacency.append(np.asarray(sorted(set(items)), dtype=np.int32))
    return adjacency


def _matrix_edges(row_values, col_values):
    _topology_progress(2, '正在建立矩阵索引')
    cells = {(int(row), int(col)): index
             for index, (row, col) in enumerate(zip(row_values, col_values))}
    edges = []
    for count, ((row, col), index) in enumerate(cells.items()):
        if count % 20000 == 0:
            _topology_progress(5 + 70 * count / max(len(cells), 1), '正在连接矩阵邻点', count, len(cells))
        for dr, dc in ((0, 1), (1, -1), (1, 0), (1, 1)):
            other = cells.get((row + dr, col + dc))
            if other is not None:
                edges.append((index, other))
    return np.asarray(edges, dtype=np.int64).reshape(-1, 2)


def _edge_lengths(xy, edges):
    if len(edges) == 0:
        return np.empty(0, dtype=float)
    return np.linalg.norm(xy[edges[:, 0]] - xy[edges[:, 1]], axis=1)


def _topology_health(adjacency):
    _topology_progress(90, '正在检查邻接关系连通性')
    if isinstance(adjacency, Matrix8Adjacency):
        return dict(adjacency.health)
    degrees = np.asarray([len(items) for items in adjacency], dtype=int)
    point_count = int(len(degrees))
    edge_count = int(degrees.sum() // 2)
    isolated_ratio = float(np.mean(degrees == 0)) if point_count else 1.0
    median_degree = float(np.median(degrees)) if point_count else 0.0
    largest = 0
    visited = np.zeros(point_count, dtype=bool)
    processed = 0
    for start in range(point_count):
        if visited[start]:
            continue
        size = 0
        queue = deque([start]); visited[start] = True
        while queue:
            current = queue.popleft(); size += 1
            processed += 1
            if processed % 20000 == 0:
                _topology_progress(90 + 9 * processed / max(point_count, 1), '正在检查邻接关系连通性', processed, point_count)
            for neighbor in adjacency[current]:
                neighbor = int(neighbor)
                if not visited[neighbor]:
                    visited[neighbor] = True; queue.append(neighbor)
        largest = max(largest, size)
    return {
        'point_count': point_count,
        'edge_count': edge_count,
        'isolated_ratio': isolated_ratio,
        'median_degree': median_degree,
        'largest_component_ratio': float(largest / point_count) if point_count else 0.0,
    }


def _health_is_usable(health):
    return bool(health['edge_count'] > 0 and health['isolated_ratio'] <= 0.25
                and health['median_degree'] >= 1.0
                and health['largest_component_ratio'] >= 0.50)


def _constrain_edges_to_raster(edges, rows, cols):
    if len(edges) == 0:
        return edges
    row_delta = np.abs(np.asarray(rows)[edges[:, 0]] - np.asarray(rows)[edges[:, 1]])
    col_delta = np.abs(np.asarray(cols)[edges[:, 0]] - np.asarray(cols)[edges[:, 1]])
    return edges[(row_delta <= 1) & (col_delta <= 1)]


def _prune_edges_by_local_scale(xy, edges, edge_factor):
    _topology_progress(60, '正在计算邻接距离')
    if len(edges) == 0:
        return edges, 0.0
    lengths = _edge_lengths(xy, edges)
    positive = lengths[np.isfinite(lengths) & (lengths > 0)]
    if positive.size == 0:
        return np.empty((0, 2), dtype=np.int64), 0.0
    incident = [[] for _ in range(len(xy))]
    for edge_index, (left, right) in enumerate(edges):
        if edge_index % 20000 == 0:
            _topology_progress(62 + 5 * edge_index / max(len(edges), 1), '正在汇总邻接距离', edge_index, len(edges))
        distance = float(lengths[edge_index])
        if np.isfinite(distance) and distance > 0:
            incident[int(left)].append(distance)
            incident[int(right)].append(distance)
    global_scale = float(np.median(positive))
    global_cap = float(np.percentile(positive, 92)) * float(edge_factor)
    local_scale = np.full(len(xy), global_scale, dtype=float)
    for index, values in enumerate(incident):
        if index % 2048 == 0:
            _topology_progress(67 + 13 * index / max(len(xy), 1), '正在计算局部点距并筛选连边', index, len(xy))
        if values:
            local_scale[index] = float(np.percentile(values, 75))
    threshold = np.maximum(local_scale[edges[:, 0]], local_scale[edges[:, 1]]) * float(edge_factor)
    keep = (lengths <= threshold) & (lengths <= max(global_cap, global_scale * edge_factor))
    return edges[keep], global_scale


def _delaunay_edges(xy, edge_factor):
    _topology_progress(5, '正在检查平面点云')
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
        _topology_progress(10, '正在建立三角邻接关系')
        triangles = Delaunay(xy).simplices
    except QhullError as exc:
        raise ValueError(f'Delaunay失败: {exc.__class__.__name__}') from exc
    edges = np.vstack((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [0, 2]]))
    _topology_progress(55, '正在合并重复连边')
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
            if sample_row % 1024 == 0:
                _topology_progress(5, '正在估计邻域大小', sample_row, sample_count)
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
    _topology_progress(2, '正在建立空间索引')
    tree = cKDTree(xy)
    neighbor_count = _choose_adaptive_knn_k(tree, xy)
    query_k = min(len(xy), neighbor_count + 1)
    edge_chunks = []
    chunk_size = 20000
    for start in range(0, len(xy), chunk_size):
        _topology_progress(8 + 45 * start / max(len(xy), 1), '正在搜索并连接邻点', start, len(xy))
        end = min(len(xy), start + chunk_size)
        distances, indices = tree.query(xy[start:end], k=query_k)
        if indices.ndim == 1:
            indices = indices[:, None]
            distances = distances[:, None]
        selected = []
        for local_row, source_index in enumerate(range(start, end)):
            if local_row % 2048 == 0:
                _topology_progress(8 + 45 * source_index / max(len(xy), 1), '正在搜索并连接邻点', source_index, len(xy))
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
    _topology_progress(55, '正在合并重复连边')
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    pruned, spacing = _prune_edges_by_local_scale(xy, edges, edge_factor)
    return pruned, spacing, neighbor_count


def _build_adaptive_topology(x, y, matrix_rc=None, sensitivity='standard', delaunay_limit=150000):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xy = np.column_stack([x, y])
    if not np.isfinite(xy).all():
        raise ValueError('拓扑输入包含无效XY')
    config = SENSITIVITY.get(str(sensitivity), SENSITIVITY['standard'])
    fallback_reason = ''
    if matrix_rc is not None:
        rows, cols = matrix_rc
        if PERFORMANCE_POLICY.matrix_fast_component_enabled:
            try:
                adjacency, spacing, health = _implicit_matrix_topology(
                    x, y, np.asarray(rows), np.asarray(cols))
            except ValueError:
                adjacency = None
                health = {'point_count': len(xy), 'edge_count': 0, 'isolated_ratio': 1.0,
                          'median_degree': 0.0, 'largest_component_ratio': 0.0}
        else:
            matrix_edges = _matrix_edges(np.asarray(rows), np.asarray(cols))
            adjacency = _edge_pairs_to_adjacency(len(xy), matrix_edges)
            matrix_lengths = _edge_lengths(xy, matrix_edges)
            positive_lengths = matrix_lengths[matrix_lengths > 0]
            spacing = (float(np.median(positive_lengths))
                       if len(positive_lengths) else 0.0)
            health = _topology_health(adjacency)
        if _health_is_usable(health):
            return {
                'adjacency': adjacency,
                'method': 'matrix8',
                'topology': ('矩阵8邻域（隐式拓扑）'
                             if isinstance(adjacency, Matrix8Adjacency)
                             else '矩阵8邻域'),
                'local_spacing_mm': spacing,
                'fallback_reason': '',
                'health': health,
                **({'matrix_grid': adjacency.grid,
                    'matrix_point_rows': adjacency.point_rows,
                    'matrix_point_cols': adjacency.point_cols}
                   if isinstance(adjacency, Matrix8Adjacency) else {}),
            }
        fallback_reason = (
            '矩阵拓扑不健康: '
            f"edges={health['edge_count']}, isolated={health['isolated_ratio']:.1%}, "
            f"median_degree={health['median_degree']:.1f}, "
            f"largest={health['largest_component_ratio']:.1%}")
        def _delaunay_candidate():
            edges, spacing = _delaunay_edges(xy, config['edge_factor'])
            return edges, spacing, 'delaunay', 'Delaunay自适应邻接'

        def _knn_candidate():
            edges, spacing, neighbor_count = _adaptive_knn_edges(
                xy, config['edge_factor'])
            return edges, spacing, 'adaptive_knn', f'自适应kNN(k={neighbor_count})'

        fallback_candidates = []
        if len(xy) <= int(delaunay_limit):
            fallback_candidates.append(_delaunay_candidate)
        fallback_candidates.append(_knn_candidate)

        constrained_errors = []
        for candidate in fallback_candidates:
            try:
                fallback_edges, spacing, method, label = candidate()
                fallback_edges = _constrain_edges_to_raster(
                    fallback_edges, np.asarray(rows), np.asarray(cols))
                adjacency = _edge_pairs_to_adjacency(len(xy), fallback_edges)
                constrained_health = _topology_health(adjacency)
                if _health_is_usable(constrained_health):
                    return {
                        'adjacency': adjacency,
                        'method': method,
                        'topology': f'受像素缺口约束的{label}',
                        'local_spacing_mm': float(spacing),
                        'fallback_reason': fallback_reason,
                        'health': constrained_health,
                    }
            except Exception as exc:
                _topology_progress()  # Cancellation must never become a fallback.
                constrained_errors.append(str(exc))

        unconstrained_errors = []
        for candidate in fallback_candidates:
            try:
                fallback_edges, spacing, method, label = candidate()
                adjacency = _edge_pairs_to_adjacency(len(xy), fallback_edges)
                health = _topology_health(adjacency)
                if _health_is_usable(health):
                    fallback_reason = (
                        f'{fallback_reason}；受像素缺口约束回退不健康，'
                        '已使用未受约束拓扑（可能跨越缺口）')
                    return {
                        'adjacency': adjacency,
                        'method': method,
                        'topology': f'{label}（缺口约束失效）',
                        'local_spacing_mm': float(spacing),
                        'fallback_reason': fallback_reason,
                        'health': health,
                    }
            except Exception as exc:
                _topology_progress()
                unconstrained_errors.append(str(exc))

        detail = '；'.join(constrained_errors or ['受约束回退后拓扑仍不连通'])
        if unconstrained_errors:
            detail += f'；未受约束回退: {"; ".join(unconstrained_errors)}'
        raise ValueError(f'{fallback_reason}；{detail}') from None
    if len(xy) <= int(delaunay_limit):
        try:
            edges, spacing = _delaunay_edges(xy, config['edge_factor'])
            if len(edges) == 0:
                raise ValueError('剪枝后无有效边')
            adjacency = _edge_pairs_to_adjacency(len(xy), edges)
            health = _topology_health(adjacency)
            if not _health_is_usable(health):
                raise ValueError(
                    f"Delaunay拓扑不健康: edges={health['edge_count']}, "
                    f"isolated={health['isolated_ratio']:.1%}, "
                    f"largest={health['largest_component_ratio']:.1%}")
            return {
                'adjacency': adjacency,
                'method': 'delaunay',
                'topology': 'Delaunay自适应邻接',
                'local_spacing_mm': float(spacing),
                'fallback_reason': '',
                'health': health,
            }
        except Exception as exc:
            _topology_progress()
            fallback_reason = str(exc)
    else:
        fallback_reason = f'点数 {len(xy):,} 超过 Delaunay 上限 {int(delaunay_limit):,}'
    edges, spacing, neighbor_count = _adaptive_knn_edges(xy, config['edge_factor'])
    adjacency = _edge_pairs_to_adjacency(len(xy), edges)
    health = _topology_health(adjacency)
    if not _health_is_usable(health):
        raise ValueError(
            f"自适应kNN拓扑不健康: edges={health['edge_count']}, "
            f"isolated={health['isolated_ratio']:.1%}, "
            f"largest={health['largest_component_ratio']:.1%}")
    return {
        'adjacency': adjacency,
        'method': 'adaptive_knn',
        'topology': f'自适应kNN(k={neighbor_count})',
        'local_spacing_mm': float(spacing),
        'fallback_reason': fallback_reason,
        'health': health,
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


def grow_surface_roi_v2(x, y, z, seed_x, seed_y, tolerance_mm, topology,
                        mode='surface_following', sensitivity='standard'):
    """Exact V4.5.0 Smart ROI V2 implementation for historical Recipe replay."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    adjacency = topology['adjacency']
    if len(x) == 0:
        return np.zeros(0, dtype=bool)
    seed = int(np.argmin((x - float(seed_x)) ** 2 + (y - float(seed_y)) ** 2))
    config = SENSITIVITY.get(str(sensitivity), SENSITIVITY['standard'])
    tolerance = max(float(tolerance_mm), 1e-12) * float(config['residual_factor'])
    seed_neighborhood = _graph_neighborhood(adjacency, seed, target=36, max_depth=5)
    seed_plane, seed_normal = _robust_local_plane(x, y, z, seed_neighborhood)
    if seed_plane is None:
        result = np.zeros(len(x), dtype=bool); result[seed] = True
        return result
    if str(mode) == 'plane_residual':
        residual = np.abs(z - (seed_plane[0] * x + seed_plane[1] * y + seed_plane[2]))
        candidate = np.isfinite(residual) & (residual <= tolerance)
        visited = np.zeros(len(x), dtype=bool); visited[seed] = True
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                neighbor = int(neighbor)
                if not visited[neighbor] and candidate[neighbor]:
                    visited[neighbor] = True; queue.append(neighbor)
        return visited
    normal_limit = np.deg2rad(float(config['normal_deg']))
    plane_cache = {seed: (seed_plane, seed_normal)}

    def local_plane(index):
        value = plane_cache.get(index)
        if value is None:
            neighborhood = _graph_neighborhood(adjacency, index, target=24, max_depth=4)
            value = _robust_local_plane(x, y, z, neighborhood)
            plane_cache[index] = value
        return value

    visited = np.zeros(len(x), dtype=bool); visited[seed] = True
    queue = deque([seed])
    while queue:
        current = queue.popleft()
        current_plane, current_normal = local_plane(current)
        if current_plane is None:
            continue
        for neighbor in adjacency[current]:
            neighbor = int(neighbor)
            if visited[neighbor]:
                continue
            predicted = (current_plane[0] * x[neighbor] + current_plane[1] * y[neighbor]
                         + current_plane[2])
            if abs(float(z[neighbor] - predicted)) > tolerance:
                continue
            neighbor_plane, neighbor_normal = local_plane(neighbor)
            if neighbor_plane is None:
                continue
            cosine = float(np.clip(np.dot(current_normal, neighbor_normal), -1.0, 1.0))
            if np.arccos(cosine) > normal_limit:
                continue
            visited[neighbor] = True; queue.append(neighbor)
    return visited


def grow_surface_roi(x, y, z, seed_x, seed_y, tolerance_mm, topology,
                     mode='surface_following', sensitivity='standard',
                     candidate_mask=None, progress=None, cancel_event=None, stats=None,
                     seed_index=None, preview=None):
    """Grow one connected surface with a fast interior path and precise boundary path.

    ``candidate_mask`` is a hard gate only; it never changes topology. ``stats`` is
    populated in place so callers can profile behavior without changing the mask API.
    """
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    adjacency = topology['adjacency']
    metrics = stats if stats is not None else {}
    last_preview = 0.0

    def emit_preview(mask, frontier, force=False):
        nonlocal last_preview
        if preview is None:
            return
        now = time.monotonic()
        if force or now-last_preview >= .3:
            preview(mask, frontier, int(metrics.get('processed', 0)))
            last_preview = now
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
    if seed_index is None:
        seed = int(np.argmin((x - float(seed_x)) ** 2 + (y - float(seed_y)) ** 2))
    else:
        seed = int(seed_index)
        if seed < 0 or seed >= len(x):
            raise ValueError('seed_index超出点云范围')
    if len(adjacency[seed]) == 0:
        raise ValueError('Smart ROI seed 在当前拓扑中没有邻接点')
    metrics['seed_degree'] = int(len(adjacency[seed]))
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
        matrix_grid = topology.get('matrix_grid')
        if (PERFORMANCE_POLICY.matrix_fast_component_enabled and
                matrix_grid is not None):
            if cancel_event is not None and cancel_event.is_set():
                from .workers import TaskCancelled
                raise TaskCancelled()
            candidate_grid = np.zeros(matrix_grid.shape, dtype=bool)
            point_rows = topology['matrix_point_rows']
            point_cols = topology['matrix_point_cols']
            candidate_grid[point_rows, point_cols] = candidate
            labels, _ = ndimage.label(
                candidate_grid, structure=np.ones((3, 3), dtype=np.uint8))
            if cancel_event is not None and cancel_event.is_set():
                from .workers import TaskCancelled
                raise TaskCancelled()
            seed_label = int(labels[int(point_rows[seed]), int(point_cols[seed])])
            if seed_label:
                visited = labels[point_rows, point_cols] == seed_label
            metrics['processed'] = int(visited.sum())
            metrics['slow_path'] = int(visited.sum())
            metrics['algorithm'] = 'matrix8_component'
            if progress is not None:
                progress(100, int(metrics['processed']), int(len(x)))
            emit_preview(visited, deque(), True)
            return visited
        visited[seed] = True
        queue = deque([seed])
        emit_preview(visited, queue, True)
        while queue:
            current = queue.popleft()
            metrics['processed'] += 1
            if metrics['processed'] % 1024 == 0:
                emit_preview(visited, queue)
                if progress is not None:
                    progress(min(99, int(100 * metrics['processed']/max(len(x), 1))),
                             int(metrics['processed']), int(len(x)))
            if cancel_event is not None and metrics['processed'] % 1024 == 0 and cancel_event.is_set():
                from .workers import TaskCancelled
                raise TaskCancelled()
            for neighbor in adjacency[current]:
                neighbor = int(neighbor)
                if not visited[neighbor] and candidate[neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)
        metrics['slow_path'] = int(visited.sum())
        emit_preview(visited, queue, True)
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
    emit_preview(accepted, queue, True)
    while queue:
        current, current_plane, current_normal, plane_age = queue.popleft()
        metrics['processed'] += 1
        if metrics['processed'] % 1024 == 0:
            emit_preview(accepted, queue)
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
    emit_preview(accepted, queue, True)
    metrics['selected'] = int(accepted.sum())
    metrics['fast_accept_ratio'] = float(metrics['fast_accept'] / max(metrics['selected'] - 1, 1))
    return accepted
