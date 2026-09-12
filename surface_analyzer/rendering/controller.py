"""GUI-owned viewport controller; workers only operate on NumPy snapshots."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PyQt6.QtCore import QObject, QTimer
from .raster import build_xy_raster, raster_rgba


class XYRasterController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.ax = owner.canvas.ax_xy
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='xy-raster')
        self.generation = 0
        self.source_version = 0
        self.source = None
        self.future = None
        self.cache = OrderedDict()
        self.image = self.overlay = None
        self.scatter = self.scatter_roi = None
        self.z_limits = (0., 1.)
        self.detail_mode = False
        self.result = None
        self.mode = 'height'
        self.z_label = 'Z (mm)'
        self.applying = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.start)
        self.poll = QTimer(self)
        self.poll.setInterval(25)
        self.poll.timeout.connect(self.finish)
        self.ax.figure.canvas.mpl_connect('resize_event', self.request)
        self.callbacks = []

    def reset(self):
        self.generation += 1
        self.source_version += 1
        self.source = None
        self.result = None
        self.cache.clear()
        self.timer.stop()
        for artist in (self.image, self.overlay, self.scatter, self.scatter_roi):
            if artist is not None and artist.axes is not None:
                artist.remove()
        self.image = self.overlay = self.scatter = self.scatter_roi = None
        self.detail_mode = False
        for cid in self.callbacks:
            self.ax.callbacks.disconnect(cid)
        self.callbacks = []

    def bind(self, x, y, z, roi, mode):
        # Advanced-indexed arrays passed by caller are owned display snapshots.
        self.source = (x, y, z, roi)
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        self.z_limits = (float(z[finite].min()), float(z[finite].max())) if finite.any() else (0., 1.)
        self.mode = mode
        self.callbacks = [self.ax.callbacks.connect(name, self.request)
                          for name in ('xlim_changed', 'ylim_changed')]
        self.request()

    def request(self, *args):
        if self.source is None or self.applying:
            return
        self.generation += 1
        self.timer.start()

    def key(self):
        xs, ys = sorted(self.ax.get_xlim()), sorted(self.ax.get_ylim())
        side = 1200
        size = (max(1, min(side, round(self.ax.bbox.width))),
                max(1, min(side, round(self.ax.bbox.height))))
        return (self.source_version, self.mode, tuple(xs+ys), size)

    def start(self):
        if self.source is None:
            return
        if self.future is not None:
            # One running job plus one debounced latest request; no unbounded queue.
            self.timer.start()
            return
        key = self.key()
        if key in self.cache:
            self.apply(self.cache[key])
            return
        x, y, z, roi = self.source
        self.job_generation = self.generation
        self.job_key = key
        self.future = self.executor.submit(build_xy_raster, x, y, z, key[2], key[3], roi)
        self.poll.start()

    def finish(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        self.poll.stop()
        try:
            result = future.result()
        except Exception as exc:
            if self.job_generation == self.generation:
                self.owner._show_status(f'XY显示失败：{exc}', 5000)
            return
        if self.source is None or self.job_generation != self.generation or self.job_key != self.key():
            return
        self.cache[self.job_key] = result
        while len(self.cache) > 2:
            self.cache.popitem(last=False)
        self.apply(result)

    def apply(self, result):
        self.applying = True
        self.result = result
        limits = self.ax.get_xlim(), self.ax.get_ylim()
        rgba = raster_rgba(result, self.mode, self.z_limits)
        overlay = np.zeros((*result.count.shape, 4))
        overlay[result.roi_count > 0] = [.42, .45, .50, .72]
        if self.image is None:
            self.image = self.ax.imshow(rgba, extent=result.extent, origin='lower', interpolation='nearest', zorder=2)
            self.overlay = self.ax.imshow(overlay, extent=result.extent, origin='lower', interpolation='nearest', zorder=4)
        else:
            self.image.set_data(rgba)
            self.image.set_extent(result.extent)
            self.overlay.set_data(overlay)
            self.overlay.set_extent(result.extent)
        # Switch before isolated one-pixel bins wash out against the background.
        # Use measured occupancy, not bounding-box density (holes are empty too).
        occupied = np.count_nonzero(result.count)
        points_per_cell = result.visible_count / max(1, occupied)
        self.detail_mode = (self.mode == 'height' and result.detail_indices is not None
                            and points_per_cell <= (1.8 if self.detail_mode else 1.4))
        self.image.set_visible(not self.detail_mode)
        self.overlay.set_visible(not self.detail_mode)
        if self.detail_mode:
            x, y, z, roi = self.source
            idx = result.detail_indices
            idx = idx[np.isfinite(z[idx])]
            offsets = np.column_stack((x[idx], y[idx]))
            selected = np.zeros(len(idx), dtype=bool) if roi is None else np.asarray(roi)[idx]
            if self.scatter is None:
                self.scatter = self.ax.scatter([], [], c=[], s=9, cmap='turbo',
                                               vmin=self.z_limits[0], vmax=self.z_limits[1],
                                               edgecolors='none', alpha=1., zorder=2)
                self.scatter_roi = self.ax.scatter([], [], s=9, c='#6b7280',
                                                   edgecolors='none', alpha=.72, zorder=4)
            self.scatter.set_offsets(offsets)
            self.scatter.set_array(z[idx])
            self.scatter.set_clim(*self.z_limits)
            self.scatter_roi.set_offsets(offsets[selected])
        for artist in (self.scatter, self.scatter_roi):
            if artist is not None:
                artist.set_visible(self.detail_mode)
        self.ax.set_xlim(limits[0], emit=False)
        self.ax.set_ylim(limits[1], emit=False)
        ny, nx = result.count.shape
        suffix = '点密度' if self.mode == 'density' else '高度均值'
        display = f'原始散点 {result.visible_count:,} 点' if self.detail_mode else f'Raster {nx}×{ny} · {suffix}'
        self.owner.canvas.title_xy.setText(f'XY {display}')
        self.owner.canvas.title_xy.setToolTip(
            f'高度通道：{self.z_label}。无有效数据区域透明，不代表零高度或已确认孔洞。'
            '缩放保持全显示源色标；放大自动显示原始散点，点大小固定，不插值。')
        self.owner._xy_raster_status = f'{display} / 全量显示源 {result.source_count:,} / 窗内 {result.visible_count:,}'
        self.owner._update_import_status_label()
        self.ax.figure.canvas.draw_idle()
        self.applying = False

    def shutdown(self):
        self.reset()
        self.poll.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
