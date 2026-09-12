"""Physical-coordinate, full-input XY binning; no interpolation or topology edits."""
from dataclasses import dataclass
import numpy as np

XY_RASTER_THRESHOLD = 50_000
MAX_RASTER_SIDE = 1200
MAX_DETAIL_POINTS = 50_000


@dataclass(frozen=True)
class Raster:
    extent: tuple
    count: np.ndarray
    z_count: np.ndarray
    z_mean: np.ndarray
    roi_count: np.ndarray
    source_count: int
    visible_count: int
    z_limits: tuple
    detail_indices: np.ndarray | None = None


def build_xy_raster(x, y, z, extent, size, roi=None):
    """Bins include both outer edges; imshow extent denotes pixel edges, in mm."""
    x, y, z = (np.asarray(a) for a in (x, y, z))
    if x.ndim != 1 or x.shape != y.shape or x.shape != z.shape:
        raise ValueError('XYZ must be equal-length vectors')
    xmin, xmax, ymin, ymax = map(float, extent)
    if not np.isfinite(extent).all() or xmax <= xmin or ymax <= ymin:
        raise ValueError('Invalid raster extent')
    nx, ny = (max(1, min(MAX_RASTER_SIDE, int(v))) for v in size)
    visible = np.isfinite(x) & np.isfinite(y) & (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
    xv, yv, zv = x[visible], y[visible], z[visible]
    ix = np.minimum(((xv-xmin)/(xmax-xmin)*nx).astype(np.int64), nx-1)
    iy = np.minimum(((yv-ymin)/(ymax-ymin)*ny).astype(np.int64), ny-1)
    flat = iy*nx + ix
    count = np.bincount(flat, minlength=nx*ny).reshape(ny, nx)
    finite = np.isfinite(zv)
    z_count = np.bincount(flat[finite], minlength=nx*ny).reshape(ny, nx)
    sums = np.bincount(flat[finite], weights=zv[finite], minlength=nx*ny).reshape(ny, nx)
    mean = np.full((ny, nx), np.nan)
    np.divide(sums, z_count, out=mean, where=z_count > 0)
    if roi is None:
        roi_count = np.zeros_like(count)
    else:
        roi = np.asarray(roi, dtype=bool)
        if roi.shape != x.shape:
            raise ValueError('ROI must match input points')
        roi_count = np.bincount(flat[roi[visible]], minlength=nx*ny).reshape(ny, nx)
    limits = (float(zv[finite].min()), float(zv[finite].max())) if finite.any() else (0., 1.)
    visible_count = int(visible.sum())
    # Bounded, exact source indices prepared off the GUI thread. No resampling.
    detail = np.flatnonzero(visible) if visible_count <= MAX_DETAIL_POINTS else None
    return Raster((xmin, xmax, ymin, ymax), count, z_count, mean, roi_count,
                  len(x), visible_count, limits, detail)


def raster_rgba(raster, mode='height', z_limits=None):
    from matplotlib import colormaps
    from matplotlib.colors import Normalize
    if mode == 'density':
        values = np.log1p(raster.count)
        low, high = 0., max(1., float(values.max()))
        cmap = colormaps['viridis']
    else:
        values = np.nan_to_num(raster.z_mean)
        low, high = raster.z_limits if z_limits is None else z_limits
        cmap = colormaps['turbo']
    rgba = cmap(Normalize(low, high)(values))
    empty = (raster.count == 0) if mode == 'density' else (raster.z_count == 0)
    rgba[empty, 3] = 0.
    if mode == 'missing':
        rgba[empty] = [.82, .84, .86, 1.]
    return rgba
