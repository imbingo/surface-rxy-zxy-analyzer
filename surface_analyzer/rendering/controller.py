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
        self.image = self.overlay = None
        for cid in self.callbacks:
            self.ax.callbacks.disconnect(cid)
        self.callbacks = []

    def bind(self, x, y, z, roi, mode):
        # Advanced-indexed arrays passed by caller are owned display snapshots.
        self.source = (x, y, z, roi)
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
        side = int(self.owner.canvas.xy_resolution.currentData())
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
        rgba = raster_rgba(result, self.mode)
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
        self.ax.set_xlim(limits[0], emit=False)
        self.ax.set_ylim(limits[1], emit=False)
        ny, nx = result.count.shape
        suffix = '点密度' if self.mode == 'density' else '高度均值'
        self.owner.canvas.title_xy.setText(f'XY Raster {nx}×{ny} · {suffix}')
        self.owner.canvas.title_xy.setToolTip(
            f'高度通道：{self.z_label}。点密度颜色 = log(1+每格点数)，不参与量测。'
            '高度使用可视源点范围；空格不插值。')
        self.owner._xy_raster_status = f'Raster {nx}×{ny} / 全量显示源 {result.source_count:,} / 窗内 {result.visible_count:,}'
        self.owner._update_import_status_label()
        self.ax.figure.canvas.draw_idle()
        self.applying = False

    def shutdown(self):
        self.reset()
        self.poll.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
