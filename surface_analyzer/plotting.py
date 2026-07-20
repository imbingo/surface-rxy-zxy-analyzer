"""Shared plotting geometry helpers for GUI canvases and exported reports."""

from __future__ import annotations

import numpy as np
from matplotlib.ticker import LinearLocator


def _finite_extent(values) -> float:
    array = np.asarray(values, dtype=float).ravel()
    finite = array[np.isfinite(array)]
    if finite.size < 2:
        return 0.0
    extent = float(np.ptp(finite))
    return extent if np.isfinite(extent) and extent > 0.0 else 0.0


def surface_box_aspect(
    x,
    y,
    z,
    *,
    min_horizontal_ratio: float = 0.06,
    min_z_ratio: float = 0.18,
    max_z_ratio: float = 0.60,
) -> tuple[float, float, float]:
    """Return a 3D box aspect that preserves XY geometry and keeps Z readable.

    X and Y always use their real range ratio. Z follows its real range when
    practical, with a small lower bound so nearly flat optical surfaces remain
    visible instead of collapsing to a line.
    """
    x_extent = _finite_extent(x)
    y_extent = _finite_extent(y)
    z_extent = _finite_extent(z)
    horizontal_extent = max(x_extent, y_extent)

    if horizontal_extent <= 0.0:
        return 1.0, 1.0, min_z_ratio

    x_ratio = max(x_extent / horizontal_extent, min_horizontal_ratio)
    y_ratio = max(y_extent / horizontal_extent, min_horizontal_ratio)
    z_ratio = float(np.clip(z_extent / horizontal_extent, min_z_ratio, max_z_ratio))
    return x_ratio, y_ratio, z_ratio


def set_surface_box_aspect(
    ax,
    x,
    y,
    z,
    *,
    zoom: float | None = None,
    z_tick_count: int | None = None,
):
    """Apply the shared 3D aspect policy and tolerate older Matplotlib APIs."""
    aspect = surface_box_aspect(x, y, z)
    if zoom is not None:
        try:
            ax.set_box_aspect(aspect, zoom=zoom)
            if z_tick_count is not None:
                ax.zaxis.set_major_locator(LinearLocator(z_tick_count))
            return aspect
        except TypeError:
            pass
    ax.set_box_aspect(aspect)
    if z_tick_count is not None:
        ax.zaxis.set_major_locator(LinearLocator(z_tick_count))
    return aspect


def set_xy_equal_aspect(ax):
    """Preserve physical X/Y geometry without stretching the point cloud."""
    ax.set_aspect("equal", adjustable="box")
