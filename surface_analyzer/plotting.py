"""Shared plotting geometry helpers for GUI canvases and exported reports."""

from __future__ import annotations

import numpy as np
from matplotlib.ticker import LinearLocator, MaxNLocator
from mpl_toolkits.mplot3d import proj3d


# The main 3D view uses a dedicated full-card canvas.  A larger scene zoom is
# needed because mplot3d otherwise leaves substantially more internal whitespace
# than the neighbouring 2D projections.  This changes presentation only.
# The 3D axis now receives explicit data-limit padding, so the scene can use
# the full-card scale without clipping the material's boundary markers.
MAIN_3D_SCENE_ZOOM = 1.40
DEFAULT_3D_ELEVATION = 30.0
DEFAULT_3D_AZIMUTH = -135.0


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
    practical, with a display-only lower bound so nearly flat optical surfaces
    remain readable instead of collapsing to a line. This does not scale the
    data or change any measurement result.
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
    min_z_ratio: float = 0.18,
):
    """Apply the shared 3D aspect policy and tolerate older Matplotlib APIs."""
    aspect = surface_box_aspect(x, y, z, min_z_ratio=min_z_ratio)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.tick_params(axis='both', labelsize=8, pad=2)
    ax.zaxis.labelpad = 12
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


def fit_surface_box_to_canvas(
    ax,
    x,
    y,
    z,
    *,
    max_zoom: float = MAIN_3D_SCENE_ZOOM,
    min_z_ratio: float = 0.28,
    horizontal_padding_px: float = 54.0,
    vertical_padding_px: float = 42.0,
):
    """Maximize a 3D scene while keeping its projected box inside the canvas.

    mplot3d's ``zoom`` is independent of the FigureCanvas aspect ratio.  A
    fixed value can therefore clip a square surface in a short landscape card.
    Projecting the eight padded limit corners at zoom 1 gives a stable scale
    estimate for the current camera and actual canvas dimensions.
    """
    aspect = surface_box_aspect(x, y, z, min_z_ratio=min_z_ratio)
    xlim, ylim, zlim = ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()
    corners = np.array([
        (xx, yy, zz)
        for xx in xlim for yy in ylim for zz in zlim
    ], dtype=float)
    zoom = float(max_zoom)
    safe_bounds = None
    screen = None
    # Validate the final projected pixels rather than assuming zoom is exactly
    # linear. This makes the home view deterministic across Matplotlib builds,
    # DPI settings and landscape card sizes.
    for _ in range(6):
        ax.set_box_aspect(aspect, zoom=zoom)
        projected = np.column_stack(proj3d.proj_transform(
            corners[:, 0], corners[:, 1], corners[:, 2], ax.get_proj())[:2])
        screen = ax.transData.transform(projected)
        canvas_width, canvas_height = ax.figure.canvas.get_width_height()
        # Axes3D forces its active axes bbox to a centred square even when the
        # FigureCanvas is a wide landscape card.  3D artists can render across
        # the full canvas, so fitting against ax.bbox would waste both side
        # regions and shrink the surface unnecessarily.
        safe_bounds = (
            horizontal_padding_px,
            float(canvas_width) - horizontal_padding_px,
            vertical_padding_px,
            float(canvas_height) - vertical_padding_px,
        )
        left, right, bottom, top = safe_bounds
        projected_width = max(float(np.ptp(screen[:, 0])), 1.0)
        projected_height = max(float(np.ptp(screen[:, 1])), 1.0)
        usable_width = max(right - left, 1.0)
        usable_height = max(top - bottom, 1.0)
        inside = (float(np.min(screen[:, 0])) >= left and
                  float(np.max(screen[:, 0])) <= right and
                  float(np.min(screen[:, 1])) >= bottom and
                  float(np.max(screen[:, 1])) <= top)
        if inside:
            break
        correction = min(usable_width / projected_width,
                         usable_height / projected_height)
        zoom = max(0.55, zoom * min(0.96, correction * 0.94))
    ax._surface_home_screen_bounds = None if screen is None else (
        float(np.min(screen[:, 0])), float(np.max(screen[:, 0])),
        float(np.min(screen[:, 1])), float(np.max(screen[:, 1])))
    ax._surface_home_safe_bounds = safe_bounds
    ax._surface_home_fits = bool(
        screen is not None and safe_bounds is not None and
        float(np.min(screen[:, 0])) >= safe_bounds[0] and
        float(np.max(screen[:, 0])) <= safe_bounds[1] and
        float(np.min(screen[:, 1])) >= safe_bounds[2] and
        float(np.max(screen[:, 1])) <= safe_bounds[3])
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.zaxis.set_major_locator(LinearLocator(3))
    ax.tick_params(axis='both', labelsize=8, pad=2)
    ax.zaxis.labelpad = 4
    return aspect, zoom


def pad_surface_limits(ax, x, y, z, horizontal_fraction=0.055,
                       vertical_fraction=0.08):
    """Add stable display-only breathing room around a 3D surface."""
    for values, setter, fraction in (
            (x, ax.set_xlim3d, horizontal_fraction),
            (y, ax.set_ylim3d, horizontal_fraction),
            (z, ax.set_zlim3d, vertical_fraction)):
        array = np.asarray(values, dtype=float)
        finite = array[np.isfinite(array)]
        if finite.size == 0:
            continue
        low, high = float(np.min(finite)), float(np.max(finite))
        span = high - low
        if not np.isfinite(span) or span <= 0.0:
            span = max(abs(low), 1.0) * 1e-6
        padding = span * float(fraction)
        setter(low - padding, high + padding)


def set_xy_equal_aspect(ax):
    """Preserve physical X/Y geometry without stretching the point cloud."""
    ax.set_aspect("equal", adjustable="box")
