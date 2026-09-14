"""Spatial display indices only. No measurement point is removed or synthesized."""
import numpy as np


class LODCache:
    """Small revision-keyed cache; values are immutable index arrays."""
    def __init__(self, capacity=8):
        self.capacity = max(1, int(capacity))
        self._values = {}
        self._order = []

    def get(self, key):
        value = self._values.get(key)
        if value is not None:
            self._order.remove(key); self._order.append(key)
        return value

    def put(self, key, value):
        if key in self._values:
            self._order.remove(key)
        self._values[key] = value
        self._order.append(key)
        while len(self._order) > self.capacity:
            self._values.pop(self._order.pop(0), None)

    def clear(self):
        self._values.clear(); self._order.clear()


def critical_indices(x, y, z, source, coeffs=None):
    source = np.asarray(source, dtype=np.int64)
    if not len(source):
        return source.copy()
    values = np.asarray(z)[source]
    finite = np.isfinite(values) & np.isfinite(np.asarray(x)[source]) & np.isfinite(np.asarray(y)[source])
    source, values = source[finite], values[finite]
    if not len(source):
        return source
    required = [source[np.argmin(values)], source[np.argmax(values)]]
    if coeffs is not None:
        a, b, c = coeffs
        residual = values - (a*np.asarray(x)[source] + b*np.asarray(y)[source] + c)
        required += [source[np.argmin(residual)], source[np.argmax(residual)]]
    return np.unique(required)


def _lod_grid(dx, dy, budget):
    ratio = dx/dy if dy > 0 else float(budget)
    nx = max(1, min(budget, int(np.sqrt(budget*max(ratio, 1/budget)))))
    return nx, max(1, budget//nx)


def _spatial_lod_indices_reference(x, y, source, limit=30000, required=()):
    """One nearest-to-cell-center source point per occupied physical XY cell.

    Ties use physical coordinates then source index. Required indices consume
    the budget first. Even for thin layouts nx*ny never exceeds the budget.
    """
    source = np.asarray(source, dtype=np.int64)
    limit = max(1, int(limit))
    required = np.intersect1d(source, np.asarray(required, dtype=np.int64))
    if len(required) > limit:
        raise ValueError('Display limit smaller than required extrema count')
    if len(source) <= limit:
        return source.copy()
    budget = limit-len(required)
    if not budget:
        return required
    xx, yy = np.asarray(x)[source], np.asarray(y)[source]
    finite = np.isfinite(xx) & np.isfinite(yy)
    source, xx, yy = source[finite], xx[finite], yy[finite]
    if not len(source):
        return required
    dx, dy = np.ptp(xx), np.ptp(yy)
    nx, ny = _lod_grid(dx, dy, budget)
    ux = (xx-xx.min())/dx*nx if dx > 0 else np.zeros(len(xx))
    uy = (yy-yy.min())/dy*ny if dy > 0 else np.zeros(len(yy))
    ix, iy = np.minimum(ux.astype(np.int64), nx-1), np.minimum(uy.astype(np.int64), ny-1)
    cell = iy*nx+ix
    distance = (ux-ix-.5)**2+(uy-iy-.5)**2
    nearest = np.full(nx*ny,np.inf)
    np.minimum.at(nearest,cell,distance)
    candidates = np.flatnonzero(distance == nearest[cell])
    # Sort only cell winners/ties, not the whole million-point cloud.
    order = candidates[np.lexsort((source[candidates],yy[candidates],xx[candidates],cell[candidates]))]
    first = np.r_[True, np.diff(cell[order]) != 0]
    return np.union1d(source[order[first]], required)


def spatial_lod_indices(x, y, source, limit=30000, required=()):
    """Deterministic spatial LOD with bounded temporary memory for large data."""
    source = np.asarray(source, dtype=np.int64)
    limit = max(1, int(limit))
    required = np.intersect1d(source, np.asarray(required, dtype=np.int64))
    if len(required) > limit:
        raise ValueError('Display limit smaller than required extrema count')
    if len(source) <= limit or len(source) <= 250_000:
        return _spatial_lod_indices_reference(x, y, source, limit, required)
    budget = limit-len(required)
    if not budget:
        return required
    x_values, y_values = np.asarray(x), np.asarray(y)
    # One million points bounds the main float/int workspace to roughly
    # 70-100 MiB while avoiding excessive per-chunk setup on 5M/8M clouds.
    chunk_size = 1_000_000
    xmin = ymin = np.inf
    xmax = ymax = -np.inf
    for start in range(0, len(source), chunk_size):
        ids = source[start:start+chunk_size]
        xx, yy = x_values[ids], y_values[ids]
        finite = np.isfinite(xx) & np.isfinite(yy)
        if np.any(finite):
            xmin = min(xmin, float(np.min(xx[finite])))
            xmax = max(xmax, float(np.max(xx[finite])))
            ymin = min(ymin, float(np.min(yy[finite])))
            ymax = max(ymax, float(np.max(yy[finite])))
    if not np.isfinite(xmin):
        return required
    dx, dy = xmax-xmin, ymax-ymin
    nx, ny = _lod_grid(dx, dy, budget)
    cell_count = nx*ny
    best_distance = np.full(cell_count, np.inf)
    best_x = np.full(cell_count, np.inf)
    best_y = np.full(cell_count, np.inf)
    best_source = np.full(cell_count, -1, dtype=np.int64)
    for start in range(0, len(source), chunk_size):
        ids = source[start:start+chunk_size]
        xx, yy = x_values[ids], y_values[ids]
        finite = np.isfinite(xx) & np.isfinite(yy)
        ids, xx, yy = ids[finite], xx[finite], yy[finite]
        if not len(ids):
            continue
        ux = (xx-xmin)/dx*nx if dx > 0 else np.zeros(len(xx))
        uy = (yy-ymin)/dy*ny if dy > 0 else np.zeros(len(yy))
        ix = np.minimum(ux.astype(np.int64), nx-1)
        iy = np.minimum(uy.astype(np.int64), ny-1)
        cells = iy*nx+ix
        distance = (ux-ix-.5)**2+(uy-iy-.5)**2
        local_min = np.full(cell_count, np.inf)
        np.minimum.at(local_min, cells, distance)
        candidates = np.flatnonzero(distance == local_min[cells])
        order = candidates[np.lexsort((ids[candidates], yy[candidates],
                                       xx[candidates], cells[candidates]))]
        first = np.r_[True, np.diff(cells[order]) != 0]
        winners = order[first]
        wc, wd = cells[winners], distance[winners]
        wx, wy, ws = xx[winners], yy[winners], ids[winners]
        better = ((wd < best_distance[wc]) |
                  ((wd == best_distance[wc]) &
                   ((wx < best_x[wc]) |
                    ((wx == best_x[wc]) &
                     ((wy < best_y[wc]) |
                      ((wy == best_y[wc]) & (ws < best_source[wc])))))))
        wc, wd, wx, wy, ws = (value[better] for value in (wc, wd, wx, wy, ws))
        best_distance[wc] = wd
        best_x[wc] = wx
        best_y[wc] = wy
        best_source[wc] = ws
    return np.union1d(best_source[best_source >= 0], required)
