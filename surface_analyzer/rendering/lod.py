"""Spatial display indices only. No measurement point is removed or synthesized."""
import numpy as np


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


def spatial_lod_indices(x, y, source, limit=30000, required=()):
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
    ratio = dx/dy if dy > 0 else float(budget)
    nx = max(1, min(budget, int(np.sqrt(budget*max(ratio, 1/budget)))))
    ny = max(1, budget//nx)
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
