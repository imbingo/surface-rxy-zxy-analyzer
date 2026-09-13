"""Window-modal import feedback; percentages describe stages, not time remaining."""
import time
from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ProgressRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0
        self.active = False
        self.setFixedSize(58, 58)

    def set_progress(self, value, active=False):
        self.value = max(0, min(100, int(value)))
        self.active = active
        self.setAccessibleName(f'进度 {self.value}%')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(5, 5, 48, 48)
        painter.setPen(QPen(QColor('#e6ebf1'), 4))
        painter.drawEllipse(rect)
        color = QColor('#25855a' if self.value == 100 else '#2f6db0')
        painter.setPen(QPen(color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, -round(self.value * 3.6 * 16))
        painter.setPen(color if self.active or self.value else QColor('#8693a3'))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f'{self.value}%')


class ImportProgressDialog(QDialog):
    cancelRequested = pyqtSignal()
    stages = (('识别文件与编码', 0, 12), ('读取与解析数据', 12, 88),
              ('坐标与列映射', 88, 94), ('首次分析与绘图准备', 94, 100))

    def __init__(self, filename, parent=None):
        super().__init__(parent)
        self.setWindowTitle('导入测量数据')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(480)
        self.resize(540, 440)
        self.finished_state = False
        self.cancel_pending = False
        self.progress_value = 0
        self.started = time.monotonic()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        heading = QLabel(filename)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.rings, self.states = [], []
        for title, _, _ in self.stages:
            row = QHBoxLayout()
            row.addWidget(QLabel(title), 1)
            state = QLabel('等待')
            state.setMinimumWidth(64)
            state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ring = ProgressRing()
            row.addWidget(state)
            row.addWidget(ring)
            layout.addLayout(row)
            self.states.append(state)
            self.rings.append(ring)
        self.detail = QLabel('正在准备导入…')
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.elapsed = QLabel()
        layout.addWidget(self.elapsed)
        note = QLabel('百分比表示各阶段进度，不代表剩余时间；部分文件无法提前获知总记录数。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.cancel_button = QPushButton('取消导入')
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.elapsed.setText(f'已用时 {time.monotonic()-self.started:.1f} 秒'))
        self.timer.start(250)

    def set_progress(self, value, message):
        value = max(self.progress_value, min(100, int(value)))
        self.progress_value = value
        for i, (_, start, end) in enumerate(self.stages):
            p = max(0, min(100, int(100 * (value-start)/(end-start))))
            active = start <= value < end
            self.rings[i].set_progress(p, active)
            self.states[i].setText('完成' if p == 100 else ('进行中' if active else '等待'))
        if not self.cancel_pending:
            self.detail.setText(message)

    def begin_commit(self):
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText('正在应用数据…')

    def finish(self, success=True, message=''):
        self.finished_state = True
        self.timer.stop()
        if success:
            self.set_progress(100, '导入完成')
            self.accept()
        else:
            self.detail.setText(message or '导入未完成')
            self.cancel_button.setText('关闭')
            self.cancel_button.setEnabled(True)

    def reject(self):
        if self.finished_state:
            super().reject()
        elif self.cancel_button.isEnabled() and not self.cancel_pending:
            self.cancel_pending = True
            self.cancel_button.setEnabled(False)
            self.detail.setText('正在取消，请等待当前读取批次结束…')
            self.cancelRequested.emit()

    def closeEvent(self, event):
        if self.finished_state:
            super().closeEvent(event)
        else:
            event.ignore()
            self.reject()
