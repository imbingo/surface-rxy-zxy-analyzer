"""Snapshot-only result views shared by thickness and optional modules.

No fitting, filtering, coordinate conversion or metrology is performed here.
Callers supply the plotted Z and its unit explicitly; all four views share
the full finite input color range, before display-only downsampling.
"""
import textwrap

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

from .plotting import display_color_limits, set_surface_box_aspect
from .rendering.lod import spatial_lod_indices


def draw_four_views(figure, x, y, z, *, spec=None, quantity='Z', unit='mm'):
    x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[valid], y[valid], z[valid]
    grid = (figure.add_gridspec(2, 2, wspace=.25, hspace=.18) if spec is None
            else spec.subgridspec(2, 2, wspace=.25, hspace=.18))
    axes = [figure.add_subplot(grid[0, 0], projection='3d'),
            figure.add_subplot(grid[0, 1]),
            figure.add_subplot(grid[1, 0]), figure.add_subplot(grid[1, 1])]
    titles = (f'3D {quantity}', f'XY {quantity}', f'XZ {quantity}', f'YZ {quantity}')
    label = f'{quantity} ({unit})'
    for ax, title in zip(axes, titles):
        ax.set_title(title, parse_math=False)
        ax.set_gid('result-' + title[:2].strip().lower())
    if not len(z):
        for ax in axes:
            ax.set_xlabel('X (mm)' if ax is not axes[-1] else 'Y (mm)')
        return axes
    limits = display_color_limits(z)
    indices = np.arange(len(z))
    critical = np.unique([int(np.argmin(z)), int(np.argmax(z))])
    pick = spatial_lod_indices(x, y, indices, 35000, critical)
    px, py, pz = x[pick], y[pick], z[pick]
    style = dict(c=pz, cmap='turbo', vmin=limits[0], vmax=limits[1],
                 s=9, edgecolors='none', alpha=1., rasterized=True)
    axes[0].scatter(px, py, pz, depthshade=False, **style)
    axes[0].set(xlabel='X (mm)', ylabel='Y (mm)', zlabel=label, proj_type='ortho')
    set_surface_box_aspect(axes[0], x, y, z * (.001 if unit == 'µm' else 1.))
    from matplotlib.ticker import MaxNLocator
    axes[0].zaxis.set_major_locator(MaxNLocator(nbins=3))
    axes[1].scatter(px, py, **style)
    axes[1].set(xlabel='X (mm)', ylabel='Y (mm)', aspect='equal')
    axes[2].scatter(px, pz, **style)
    axes[2].set(xlabel='X (mm)', ylabel=label)
    axes[3].scatter(py, pz, **style)
    axes[3].set(xlabel='Y (mm)', ylabel=label)
    for ax in axes[1:]:
        ax.grid(True, alpha=.2)
    figure.colorbar(ScalarMappable(norm=Normalize(*limits), cmap=colormaps['turbo']),
                    ax=axes, shrink=.8, pad=.025, label=label)
    return axes


def snapshot_report(figure, title, left_lines, right_lines, *, fifth=False):
    """Build a measured-height header and return the result/match plot cells."""
    left = '\n'.join(textwrap.fill(str(line), 57, break_long_words=True,
                                  replace_whitespace=False) for line in left_lines)
    right = '\n'.join(textwrap.fill(str(line), 57, break_long_words=True,
                                   replace_whitespace=False) for line in right_lines)
    count = max(len(left.splitlines()), len(right.splitlines()))
    header_inches = max(1.8, .20 * count + .65)
    figure.set_size_inches(21 if fifth else 17, header_inches + 8)
    grid = figure.add_gridspec(2, 1, height_ratios=[header_inches, 8])
    head = figure.add_subplot(grid[0]); head.axis('off')
    head.set_title(title, loc='left', fontsize=16, fontweight='bold', parse_math=False)
    for xpos, value in ((0., left), (.51, right)):
        head.text(xpos, .92, value, va='top', fontsize=9, linespacing=1.4,
                  transform=head.transAxes, parse_math=False)
    if fifth:
        plots = grid[1].subgridspec(1, 2, width_ratios=[2, 1])
        return plots[0], plots[1]
    return grid[1], None
