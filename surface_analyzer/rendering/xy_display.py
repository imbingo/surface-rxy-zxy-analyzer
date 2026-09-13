"""Two XY presentation modes. Source coordinates and measurement data stay intact."""
from dataclasses import replace
import numpy as np
from scipy.spatial import cKDTree
from .raster import build_xy_raster, MAX_DETAIL_POINTS, MAX_RASTER_SIDE
from .lod import spatial_lod_indices, critical_indices


def estimate_scan_grid(x, y):
    """Estimate independent directional pitches, once per display source.

    Query at most 4096 source points against the full XY tree; never infer pitch
    from global occupancy, which includes real holes. Directional neighbours
    tolerate modest axis jitter. Unknown/line layouts fall back to local spacing.
    This is a display estimate, not scanner calibration or a hole classifier.
    """
    xy = np.column_stack((x, y))
    xy = xy[np.isfinite(xy).all(axis=1)]
    if not len(xy):
        return (1., 1., 0., 0.)
    origin = xy.min(axis=0)
    if len(xy) < 2:
        return (1., 1., *origin)
    tree = cKDTree(xy)
    query = xy[np.linspace(0, len(xy)-1, min(4096, len(xy)), dtype=int)]
    distances, neighbours = tree.query(query, k=min(32, len(xy)))
    delta = np.abs(xy[neighbours] - query[:, None, :])
    eps = max(np.spacing(np.abs(xy).max()) * 8, 1e-12)
    nearest = np.min(np.where(distances > eps, distances, np.inf), axis=1)
    usable = nearest[np.isfinite(nearest)]
    fallback = float(np.median(usable)) if len(usable) else 1.
    pitch = []
    for axis in (0, 1):
        primary, cross = delta[..., axis], delta[..., 1-axis]
        candidate = np.min(np.where((primary > eps) & (cross < primary*.25),
                                    primary, np.inf), axis=1)
        candidate = candidate[np.isfinite(candidate)]
        pitch.append(float(np.median(candidate)) if len(candidate) else fallback)
    # Slightly wider than typical spacing avoids jitter-induced sub-pixel holes.
    # No empty cell is filled; features below this footprint are unresolved.
    return (pitch[0]*1.25, pitch[1]*1.25, *origin)


def aligned_grid(extent, size, grid):
    """Source-anchored physical bins, coarsened only to respect pixel budget."""
    output, counts = [], []
    for low, high, pixels, pitch, origin in zip(
            extent[::2], extent[1::2], size, grid[:2], grid[2:]):
        pixels = max(3, min(MAX_RASTER_SIDE, int(pixels)))
        step = pitch * max(1, int(np.ceil((high-low)/((pixels-2)*pitch))))
        anchor = origin - step/2
        first = np.floor((low-anchor)/step)
        last = np.ceil((high-anchor)/step)
        count = max(1, int(last-first))
        output.extend((anchor+first*step, anchor+(first+count)*step))
        counts.append(count)
    return tuple(output), tuple(counts)


def build_xy_display(x, y, z, extent, size, roi=None, mode='height', grid=None):
    if mode not in ('height', 'points'):
        raise ValueError('Unknown XY display mode')
    x, y, z = map(np.asarray, (x, y, z))
    if mode == 'points':
        result = build_xy_raster(x, y, z, extent, (1, 1), roi)
        visible = (np.isfinite(x) & np.isfinite(y) & np.isfinite(z) &
                   (x >= extent[0]) & (x <= extent[1]) &
                   (y >= extent[2]) & (y <= extent[3]))
        indices = np.flatnonzero(visible)
        indices = spatial_lod_indices(x, y, indices, MAX_DETAIL_POINTS,
                                      critical_indices(x, y, z, indices))
        return replace(result, detail_indices=indices, scan_grid=grid)
    grid = estimate_scan_grid(x, y) if grid is None else grid
    bounds, bins = aligned_grid(extent, size, grid)
    result = build_xy_raster(x, y, z, bounds, bins, roi)
    visible = (np.isfinite(x) & np.isfinite(y) & (x >= extent[0]) &
               (x <= extent[1]) & (y >= extent[2]) & (y <= extent[3]))
    return replace(result, visible_count=int(visible.sum()), scan_grid=grid)
