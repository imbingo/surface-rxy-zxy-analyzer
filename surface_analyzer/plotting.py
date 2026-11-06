"""Shared plotting geometry helpers for GUI canvases and exported reports."""

from __future__ import annotations

import numpy as np
from matplotlib.ticker import LinearLocator, MaxNLocator
from mpl_toolkits.mplot3d import proj3d


# Search ceiling for the main view, not a fixed display multiplier. The fitter
# measures the actual card and labels to choose the largest safe scene size.
MAIN_3D_SCENE_ZOOM = 3.0
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
    horizontal_padding_px: float = 36.0,
    vertical_padding_px: float = 26.0,
):
    """Maximize a 3D scene while keeping its projected box inside the canvas.

    mplot3d's ``zoom`` is independent of the FigureCanvas aspect ratio.  A
    fixed value can therefore clip a square surface in a short landscape card.
    Apply the final axes aspect before projecting the eight padded limit
    corners, then maximize their scale within the actual canvas. Rendered
    labels reserve additional space only where needed.
    """
    aspect = surface_box_aspect(x, y, z, min_z_ratio=min_z_ratio)
    xlim, ylim, zlim = ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()
    corners = np.array([
        (xx, yy, zz)
        for xx in xlim for yy in ylim for zz in zlim
    ], dtype=float)
    # Axes3D changes its active rectangle to a centred square at draw time.
    # Apply that geometry *before* measuring pixels. Measuring the undrawn wide
    # rectangle and then drawing the square used to shrink the home view twice.
    ax.apply_aspect()
    # transData and renderer extents are in physical display pixels. Qt's
    # get_width_height() defaults to logical pixels on high-DPI displays.
    canvas_width, canvas_height = ax.figure.bbox.width, ax.figure.bbox.height
    pixel_ratio = float(getattr(ax.figure.canvas, 'device_pixel_ratio', 1.0))
    horizontal_padding_px *= pixel_ratio
    vertical_padding_px *= pixel_ratio
    px = min(float(horizontal_padding_px), float(canvas_width) * .12)
    py = min(float(vertical_padding_px), float(canvas_height) * .12)
    safe_bounds = (px, float(canvas_width) - px, py, float(canvas_height) - py)
    left, right, bottom, top = safe_bounds
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.zaxis.set_major_locator(LinearLocator(3))
    ax.tick_params(axis='both', labelsize=8, pad=2)
    ax.zaxis.labelpad = 4
    # 3D collections otherwise keep the centred square axes clip box, cutting
    # material edges off when the scene uses the wider FigureCanvas.
    for artist in (*ax.collections, *ax.lines):
        artist.set_clip_box(ax.figure.bbox)

    def project(zoom):
        ax.set_box_aspect(aspect, zoom=zoom)
        projected = np.column_stack(proj3d.proj_transform(
            corners[:, 0], corners[:, 1], corners[:, 2], ax.get_proj())[:2])
        return ax.transData.transform(projected)

    def fits(screen):
        return bool(np.all(np.isfinite(screen)) and
                    np.min(screen[:, 0]) >= left and np.max(screen[:, 0]) <= right and
                    np.min(screen[:, 1]) >= bottom and np.max(screen[:, 1]) <= top)

    label_bounds = None
    for refinement in range(8):
        # Search both up and down. The actual canvas and camera, rather than a
        # fixed multiplier, determine how much space this scene can occupy.
        upper = max(.05, float(max_zoom))
        screen = project(upper)
        if fits(screen):
            zoom = upper
        else:
            lower = 0.0
            for _ in range(16):
                middle = (lower + upper) / 2
                if fits(project(middle)):
                    lower = middle
                else:
                    upper = middle
            zoom = max(lower * .995, .001)
            screen = project(zoom)

        # Measure the rendered labels once per refinement, without repainting
        # pixels. Ignore locator ticks outside the limits: Matplotlib does not
        # draw them, and their stale text positions can be far off the canvas.
        ax.figure.draw_without_rendering()
        renderer = ax.figure.canvas.get_renderer()
        boxes = []
        for axis, limits in ((ax.xaxis, xlim), (ax.yaxis, ylim), (ax.zaxis, zlim)):
            labels = [axis.label, axis.get_offset_text()]
            low, high = sorted(limits)
            labels.extend(tick.label1 for tick in axis.majorTicks
                          if low <= tick.get_loc() <= high)
            boxes.extend(label.get_window_extent(renderer) for label in labels
                         if label.get_visible() and label.get_text())
        if not boxes:
            break
        label_bounds = (min(box.x0 for box in boxes), max(box.x1 for box in boxes),
                        min(box.y0 for box in boxes), max(box.y1 for box in boxes))
        overflows = (max(0., 6. * pixel_ratio - label_bounds[0]),
                     max(0., label_bounds[1] - (canvas_width - 6. * pixel_ratio)),
                     max(0., 6. * pixel_ratio - label_bounds[2]),
                     max(0., label_bounds[3] - (canvas_height - 6. * pixel_ratio)))
        if not any(overflows) or refinement == 7:
            break
        left += overflows[0] + 1
        right -= overflows[1] + 1
        bottom += overflows[2] + 1
        top -= overflows[3] + 1
    safe_bounds = (left, right, bottom, top)
    ax._surface_home_label_bounds = label_bounds
    ax._surface_home_screen_bounds = None if screen is None else (
        float(np.min(screen[:, 0])), float(np.max(screen[:, 0])),
        float(np.min(screen[:, 1])), float(np.max(screen[:, 1])))
    ax._surface_home_safe_bounds = safe_bounds
    ax._surface_home_labels_fit = bool(label_bounds is None or (
        label_bounds[0] >= 0 and label_bounds[1] <= canvas_width and
        label_bounds[2] >= 0 and label_bounds[3] <= canvas_height))
    ax._surface_home_fits = bool(
        screen is not None and safe_bounds is not None and
        float(np.min(screen[:, 0])) >= safe_bounds[0] and
        float(np.max(screen[:, 0])) <= safe_bounds[1] and
        float(np.min(screen[:, 1])) >= safe_bounds[2] and
        float(np.max(screen[:, 1])) <= safe_bounds[3])
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
