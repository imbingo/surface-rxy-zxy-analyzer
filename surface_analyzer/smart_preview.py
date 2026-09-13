"""Bounded latest-frame mailbox and Qt-only Smart ROI growth preview."""
from itertools import islice
from threading import Lock
import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QGridLayout, QWidget
from .import_progress import ImportProgressDialog


class PreviewMailbox:
    def __init__(self, x, y, limit=12000):
        self.indices = np.sort(np.random.default_rng(467).choice(len(x), min(len(x), limit), replace=False))
        self.xy = np.column_stack((x[self.indices], y[self.indices]))
        self.x, self.y = x, y
        self.lock = Lock()
        self.latest = None
        self.progress = None
        self.closed = False

    def publish(self, accepted, queue, processed):
        ids = np.fromiter((int(item[0]) if isinstance(item, tuple) else int(item)
                           for item in islice(queue, 1200)), dtype=int)
        frame = (accepted[self.indices].copy(),
                 np.column_stack((self.x[ids], self.y[ids])),
                 int(processed), int(np.count_nonzero(accepted)))
        with self.lock:
            if not self.closed:
                self.latest = frame

    def take(self):
        with self.lock:
            frame, self.latest = self.latest, None
            return frame

    def report_progress(self, value, message):
        with self.lock:
            if not self.closed:
                self.progress = (value, message)

    def take_progress(self):
        with self.lock:
            value, self.progress = self.progress, None
            return value

    def close(self):
        with self.lock:
            self.closed = True
            self.latest = None
            self.progress = None


class GrowthCanvas(QWidget):
    def __init__(self, xy, seed, parent=None):
        super().__init__(parent)
        self.xy = xy
        self.seed = np.asarray(seed)
        self.selected = np.zeros(len(xy), dtype=bool)
        self.frontier = np.empty((0, 2))
        self.low = np.min(xy, axis=0)
        self.span = np.maximum(np.ptp(xy, axis=0), 1e-9)
        self.setMinimumHeight(210)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('white'))
        scale = min((self.width()-60)/self.span[0], (self.height()-45)/self.span[1])
        origin = np.array([(self.width()-self.span[0]*scale)/2, self.height()-28])
        def points(xy):
            coords = (xy-self.low) * np.array([scale, -scale]) + origin
            return QPolygonF([QPointF(float(x), float(y)) for x,y in coords])
        for xy, color, width in ((self.xy, '#d9dfe7', 2),
                                 (self.xy[self.selected], '#2f78cd', 2.5),
                                 (self.frontier, '#ef962b', 3)):
            painter.setPen(QPen(QColor(color), width))
            painter.drawPoints(points(xy))
        center = points(self.seed.reshape(1,2))[0]
        painter.setPen(QPen(QColor('#bf2538'), 2))
        painter.drawEllipse(center, 5, 5)
        painter.drawLine(center-QPointF(8,0), center+QPointF(8,0))
        painter.drawLine(center-QPointF(0,8), center+QPointF(0,8))
        painter.setPen(QColor('#526575'))
        painter.drawText(QRectF(0, self.height()-24, self.width(), 22), Qt.AlignmentFlag.AlignCenter,
                         f'X: {self.low[0]:.2f} ～ {self.low[0]+self.span[0]:.2f} mm  |  Y: {self.low[1]:.2f} ～ {self.low[1]+self.span[1]:.2f} mm')


class SmartProgressDialog(ImportProgressDialog):
    stages = (('建立邻接关系', 0, 49), ('检查连通性', 49, 55),
              ('跟踪连续曲面', 55, 90), ('整理并应用 ROI', 90, 100))

    def __init__(self, mailbox, seed, parent=None):
        super().__init__('智能抓面 · XY 生长预览', parent)
        self.mailbox = mailbox
        self.setWindowTitle('智能抓面进度与过程预览')
        self.finished.connect(self.deleteLater)
        layout = self.layout()
        grid = QGridLayout()
        for i in range(4):
            row = layout.takeAt(1).layout()
            row.itemAt(0).widget().setMinimumWidth(110)
            grid.addLayout(row, i//2, i%2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.insertLayout(1, grid)
        self.preview = GrowthCanvas(mailbox.xy, seed)
        layout.insertWidget(2, self.preview, 1)
        self.detail.setText('正在准备邻接关系；进入曲面跟踪阶段后显示生长过程。')
        # Replace the generic import explanation with the actual preview contract.
        layout.itemAt(layout.count()-2).widget().setText(
            '灰：点云背景  蓝：已纳入  橙：待扩展前沿  红：种子点\n'
            '预览抽样、约 300 毫秒更新；最终 ROI 使用完整分析点集。进度不是剩余时间。')
        self.cancel_button.setText('取消抓面')
        self.resize(720, 600)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.consume)
        self.poll.start(300)

    def consume(self):
        if self.finished_state or self.cancel_pending:
            return
        progress = self.mailbox.take_progress()
        if progress is not None:
            self.set_progress(*progress)
        frame = self.mailbox.take()
        if frame is not None:
            selected, frontier, processed, count = frame
            if self.progress_value < 55:
                self.set_progress(55, '正在跟踪连续曲面')
            self.preview.selected = selected
            self.preview.frontier = frontier
            self.preview.update()
            self.detail.setText(f'已处理 {processed:,} / {len(self.mailbox.x):,} 点；已纳入 {count:,} 点')

    def finish(self, success=True, message=''):
        self.consume()
        self.poll.stop()
        self.mailbox.close()
        super().finish(success, message)

    def reject(self):
        super().reject()
        if self.cancel_pending and not self.finished_state:
            self.detail.setText('正在取消抓面，请等待当前计算步骤结束…')

    def begin_commit(self):
        super().begin_commit()
        self.cancel_button.setText('正在应用 ROI…')
