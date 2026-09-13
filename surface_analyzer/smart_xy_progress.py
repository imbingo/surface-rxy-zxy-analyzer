"""Inline Smart ROI feedback and a blitted overlay on the existing XY canvas."""
import time
import numpy as np
from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from .import_progress import ProgressRing


class SmartXYProgress(QFrame):
    cancelRequested = pyqtSignal()

    def __init__(self, mailbox, seed, owner):
        self.canvas = owner.canvas.ax_xy.figure.canvas
        super().__init__(self.canvas)
        self.owner = owner
        self.ax = owner.canvas.ax_xy
        self.mailbox = mailbox
        self.finished_state = False
        self.cancel_pending = False
        self.progress_value = 0
        self.started = time.monotonic()
        self.last_stage_message = ''
        self.background = None
        self.commit_limits = None
        self.detached = False
        self.setObjectName('smartXYProgress')
        self.setStyleSheet('#smartXYProgress {background:#f5f9ff; border:1px solid #b9d2ed; border-radius:6px;}')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        row = QVBoxLayout()
        row.setSpacing(0)
        self.stage = QLabel('智能抓面 · 建立邻接关系')
        self.stage.setWordWrap(False)
        self.ring = ProgressRing()
        self.ring.setFixedSize(40, 40)
        row.addWidget(self.stage)
        self.detail = QLabel('灰色：已纳入区域（过程抽样）')
        self.detail.setWordWrap(False)
        row.addWidget(self.detail)
        self.cancel_button = QPushButton('取消')
        self.cancel_button.setFixedWidth(62)
        self.cancel_button.clicked.connect(self.reject)
        layout.addLayout(row, 1)
        layout.addWidget(self.ring)
        layout.addWidget(self.cancel_button)
        limits = self.ax.get_xlim(), self.ax.get_ylim()
        self.artist = self.ax.scatter([], [], s=7, color='#6b7280', alpha=.72,
                                      edgecolors='none', zorder=8, animated=True)
        self.ax.set_xlim(limits[0], emit=False)
        self.ax.set_ylim(limits[1], emit=False)
        self.draw_cid = self.canvas.mpl_connect('draw_event', self.capture_background)
        self.canvas.installEventFilter(self)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.consume)
        self.poll.start(300)

    def open(self):
        self.place()
        self.show()
        self.raise_()
        self.canvas.draw_idle()

    def place(self):
        self.setFixedWidth(max(180, min(440, self.canvas.width()-16)))
        self.adjustSize()
        self.move(max(0, self.canvas.width()-self.width()-8), 8)

    def eventFilter(self, obj, event):
        if obj is self.canvas and event.type() == QEvent.Type.Resize:
            self.background = None
            self.place()
        return super().eventFilter(obj, event)

    def capture_background(self, event):
        if self.detached:
            return
        self.background = self.canvas.copy_from_bbox(self.ax.bbox)
        # Do not request a synchronous Qt repaint from inside a full draw/resize.
        QTimer.singleShot(0, self.blit)

    def blit(self):
        if self.background is None or self.detached:
            return
        self.canvas.restore_region(self.background)
        self.ax.draw_artist(self.artist)
        self.canvas.blit(self.ax.bbox)

    def set_progress(self, value, message):
        if self.finished_state or self.cancel_pending:
            return
        self.progress_value = max(self.progress_value, min(100, int(value)))
        self.ring.set_progress(self.progress_value, True)
        stage = ('建立邻接关系' if value < 49 else '检查连通性' if value < 55
                 else '跟踪曲面' if value < 90 else '整理 ROI')
        self.last_stage_message = message
        self.stage.setText('智能抓面 · ' + stage)

    def consume(self):
        if self.finished_state or self.cancel_pending:
            return
        progress = self.mailbox.take_progress()
        if progress is not None:
            self.set_progress(*progress)
        frame = self.mailbox.take()
        if frame is not None:
            selected, _, processed, count = frame
            self.artist.set_offsets(self.mailbox.xy[selected])
            self.detail.setText(f'已纳入 {count:,} 点（灰色抽样预览）')
            self.detail.setToolTip(f'已处理 {processed:,} 点')
            self.blit()
        elapsed = f'已用时 {time.monotonic()-self.started:.1f} 秒'
        self.stage.setToolTip(f'{self.last_stage_message}；{elapsed}；灰色为已纳入区域的抽样预览')

    def detach(self):
        if self.detached:
            return
        self.detached = True
        self.canvas.mpl_disconnect(self.draw_cid)
        self.canvas.removeEventFilter(self)
        if self.artist in self.ax.collections:
            self.artist.remove()
        self.background = None
        self.canvas.draw_idle()

    def begin_commit(self):
        self.consume()
        self.commit_limits = self.ax.get_xlim(), self.ax.get_ylim()
        self.cancel_button.setEnabled(False)
        self.stage.setText('正在应用 ROI…')
        self.detach()

    def finish(self, success=True, message=''):
        self.finished_state = True
        self.poll.stop()
        self.mailbox.close()
        self.detach()
        if success and self.commit_limits is not None:
            self.ax.set_xlim(self.commit_limits[0])
            self.ax.set_ylim(self.commit_limits[1])
            self.canvas.draw_idle()
        if message:
            self.owner._show_status(message, 8000)
        self.hide()
        self.deleteLater()

    def reject(self):
        if not self.finished_state and not self.cancel_pending and self.cancel_button.isEnabled():
            self.cancel_pending = True
            self.cancel_button.setEnabled(False)
            self.stage.setText('正在取消，请等待当前步骤结束…')
            self.cancelRequested.emit()
