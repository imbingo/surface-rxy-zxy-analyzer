import sys
import os
import re
import mmap
import json
import tempfile
import time
from collections import deque
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle as MplRectangle, Circle as MplCircle
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.widgets import RectangleSelector
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSplitter, QGroupBox, QGridLayout, QMessageBox,
    QScrollArea, QComboBox, QTabWidget, QDoubleSpinBox, QSpinBox, QCheckBox,
    QDialog, QDialogButtonBox, QFrame, QSizePolicy, QGraphicsDropShadowEffect,
    QStackedWidget, QSizeGrip, QToolButton, QStyle, QStyleOption, QStyledItemDelegate, QStyleOptionViewItem,
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent, QSize, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen
from scipy.spatial import cKDTree

from .plotting import (
    DEFAULT_3D_AZIMUTH, DEFAULT_3D_ELEVATION,
    set_surface_box_aspect, set_xy_equal_aspect,
)
from .pose_icons import focus_icon



class ProjectionToolbar(QWidget):
    """Wrap the display selector below the two command groups when necessary."""
    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.groups = groups
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.wrapped = None
        self._relayout()

    def _relayout(self):
        wrapped = self.width() < sum(group.sizeHint().width() for group in self.groups) + 24
        if wrapped == self.wrapped:
            return
        self.wrapped = wrapped
        for group in self.groups:
            self.grid.removeWidget(group)
        self.grid.addWidget(self.groups[0], 0, 0)
        self.grid.addWidget(self.groups[1], 0, 1)
        self.grid.addWidget(self.groups[2], 1 if wrapped else 0, 0 if wrapped else 2,
                            1, 2 if wrapped else 1)
        self.grid.setColumnStretch(1, 1 if wrapped else 0)
        self.grid.setColumnStretch(2, 0 if wrapped else 1)
        self.grid.setColumnStretch(3, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()


class ROIRegionDelegate(QStyledItemDelegate):
    """Paint enabled state as a switch while preserving Qt's check-state model."""
    @staticmethod
    def switch_rect(rect):
        return QRectF(rect.right() - 43, rect.center().y() - 9, 32, 18)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(44)
        return hint

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ''
        styled.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = styled.widget.style() if styled.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_rect = option.rect.adjusted(12, 0, -54, 0)
        painter.setFont(styled.font)
        painter.setPen(QColor('#243b50' if styled.state & QStyle.StateFlag.State_Selected else '#343d48'))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         styled.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()))
        enabled = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        switch = self.switch_rect(option.rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#4779a6' if enabled else '#c8ced4'))
        painter.drawRoundedRect(switch, 9, 9)
        painter.setBrush(QColor('#ffffff'))
        knob_x = switch.right() - 16 if enabled else switch.left() + 2
        painter.drawEllipse(QRectF(knob_x, switch.top() + 2, 14, 14))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return False
        toggle = False
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            if event.button() != Qt.MouseButton.LeftButton or not self.switch_rect(option.rect).contains(event.position()):
                return False
            if event.type() == QEvent.Type.MouseButtonPress:
                return True
            toggle = True
        elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            toggle = True
        if toggle:
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
            return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                                 Qt.ItemDataRole.CheckStateRole)
        return False


class WorkspaceTabs(QTabWidget):
    """Keep the three workspace choices evenly distributed over the inspector."""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.tabBar().setExpanding(True)
        self.tabBar().setFixedWidth(self.width())


class SourceLabel(QLabel):
    """Keep the full source identity while painting a single elided line."""
    textChanged = pyqtSignal(str)

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setWordWrap(False)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.textChanged.emit(text)

    def sizeHint(self):
        return QSize(0, self.fontMetrics().height() + 18)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        rect = self.contentsRect().adjusted(9, 0, -9, 0)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle,
                                           max(0, rect.width()))
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()


class NoWheelSpinBox(QSpinBox):
    """SpinBox should not steal scroll-wheel gestures from the left control pane."""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """DoubleSpinBox should not change values on accidental wheel scroll."""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(QComboBox):
    """ComboBox should not change selection on accidental wheel scroll."""
    def wheelEvent(self, event):
        event.ignore()


class MultiViewCanvas(QWidget):
    """四视图改为 4 张独立卡片（2×2 网格），每张白底圆角 + 投影 + 顶部「● 标题」，
    模块感更强；标题用 Qt 渲染，蓝点与文字天然对齐。"""
    focusChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # YaHei 同时含中文与 µ(U+00B5)，去倾斜显示的 µm 轴标签也不再出现缺字方块
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        plt.rcParams.update({
            'figure.facecolor': '#ffffff', 'axes.facecolor': '#ffffff',
            'axes.edgecolor': '#d8dee4', 'axes.linewidth': 0.8,
            'axes.labelcolor': '#5b6672', 'axes.labelsize': 10,
            'grid.color': '#edf0f3', 'grid.linewidth': 0.7,
            'xtick.color': '#9aa4ae', 'ytick.color': '#9aa4ae',
            'xtick.labelsize': 9, 'ytick.labelsize': 9,
        })
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.ax3d, c3, card3, self.title_3d, b3 = self._make_card("3D 原始高度", '3d', '3D')
        self.ax_xy, cxy, cardxy, self.title_xy, bxy = self._make_card("XY 俯视分布", None, 'XY')
        self.xy_mode = NoWheelComboBox(self)
        self.xy_mode.addItem('面型图', 'height')
        self.xy_mode.addItem('原始点图', 'points')
        self.xy_mode.setFixedWidth(104)
        self.xy_mode.setFixedHeight(24)
        self.xy_mode.setAccessibleName('XY显示模式')
        self.xy_mode.setToolTip('面型图：步距自适应栅格，不插值补洞。\n原始点图：真实XY位置；大点云仅显示抽样，放大后显示局部全部点。')
        header = cardxy.layout().itemAt(0).layout()
        header.insertWidget(header.count()-1, self.xy_mode)
        self.xy_resolution = NoWheelComboBox(self)
        self.xy_resolution.addItem('自动', 1200)
        # Retain settings objects only as compatibility shims for Recipe schema 8.
        self.xy_resolution.hide()
        self.ax_xz, cxz, cardxz, self.title_xz, bxz = self._make_card("X-Z 投影", None, 'XZ')
        self.ax_yz, cyz, cardyz, self.title_yz, byz = self._make_card("Y-Z 投影", None, 'YZ')
        self._canvases = [c3, cxy, cxz, cyz]
        self._grid = grid
        self._cards = [card3,cardxy,cardxz,cardyz]
        self._views = ('3D', 'XY', 'XZ', 'YZ')
        self._card_by_view = dict(zip(self._views, self._cards))
        self._canvas_by_view = dict(zip(self._views, self._canvases))
        self.focus_buttons = dict(zip(self._views, (b3,bxy,bxz,byz)))
        self.focused_view = None
        self._layout_columns = 2
        self._layout_cards()

    def _layout_cards(self):
        for card in self._cards:
            self._grid.removeWidget(card)
        if self.focused_view is not None:
            self.setMinimumHeight(0)
            self._grid.addWidget(self._card_by_view[self.focused_view], 0, 0, 1, 2)
            return
        columns = 1 if self.width() < 650 else 2
        self._layout_columns = columns
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // columns, i % columns)
        self._grid.setColumnStretch(1, 1 if columns == 2 else 0)
        self.setMinimumHeight(1040 if columns == 1 else 460)

    def set_focused_view(self, view=None):
        if view is not None and view not in self._card_by_view:
            raise ValueError(f'Unknown plot view: {view}')
        if view == self.focused_view:
            return
        self.focused_view = view
        for key, card in self._card_by_view.items():
            card.setVisible(view is None or key == view)
        for key, button in self.focus_buttons.items():
            restore = view == key
            button.setIcon(focus_icon(restore))
            button.setToolTip('还原四视图（Esc）' if restore else f'放大 {key} 视图')
            button.setAccessibleName('还原四视图' if restore else f'放大 {key} 视图')
        self._layout_cards()
        self.updateGeometry()
        if view is None:
            for canvas in self._canvases:
                canvas.draw_idle()
        else:
            self._canvas_by_view[view].draw_idle()
        self.focusChanged.emit(view)

    def toggle_focused_view(self, view):
        self.set_focused_view(None if self.focused_view == view else view)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self,'_cards'):
            return
        if self.focused_view is not None:
            return
        columns = 1 if self.width() < 650 else 2
        if columns != self._layout_columns:
            self._layout_cards()

    def _make_card(self, title, projection, view):
        card = QFrame(); card.setObjectName("plotCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 10, 8); v.setSpacing(5)
        head = QHBoxLayout(); head.setSpacing(7)
        dot = QLabel(); dot.setObjectName("plotDot"); dot.setFixedSize(8, 8)
        tlabel = QLabel(title); tlabel.setObjectName("plotTitle")
        focus = QToolButton()
        focus.setObjectName('plotFocusButton')
        focus.setAutoRaise(True)
        focus.setFixedSize(26, 24)
        focus.setIconSize(QSize(18, 18))
        focus.setIcon(focus_icon(False))
        focus.setToolTip(f'放大 {view} 视图')
        focus.setAccessibleName(f'放大 {view} 视图')
        focus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        focus.clicked.connect(lambda checked=False, key=view: self.toggle_focused_view(key))
        head.addWidget(dot); head.addWidget(tlabel); head.addStretch(); head.addWidget(focus)
        v.addLayout(head)
        if projection == '3d':
            # A manually positioned orthographic axis uses the wide card much
            # better than constrained_layout, which tends to shrink 3D axes to
            # a small square when Z is much smaller than X/Y.
            fig = Figure(constrained_layout=False)
            # Use the complete canvas. Data-limit padding in draw_plots keeps
            # boundary points inside the 3D clip box; shrinking this axes rect
            # only wastes card area and makes the material harder to inspect.
            ax = fig.add_axes([0.0, 0.0, 1.0, 1.0], projection=projection)
            ax.set_proj_type('ortho')
            ax.view_init(elev=DEFAULT_3D_ELEVATION, azim=DEFAULT_3D_AZIMUTH)
        else:
            fig = Figure(constrained_layout=True)
            ax = fig.add_subplot(111, projection=projection)
        canvas = FigureCanvas(fig)
        if view == 'XY':
            content = QHBoxLayout()
            content.setContentsMargins(0, 0, 0, 0)
            content.setSpacing(0)
            content.addWidget(canvas, 1)
            self.colorbar_figure = Figure(figsize=(1.04, 3))
            self.colorbar_axis = self.colorbar_figure.add_axes([.08, .14, .22, .72])
            self.colorbar_axis.set_visible(False)
            self.colorbar_canvas = FigureCanvas(self.colorbar_figure)
            self.colorbar_canvas.setFixedWidth(104)
            self.colorbar_canvas.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)
            content.addWidget(self.colorbar_canvas)
            v.addLayout(content, 1)
        else:
            v.addWidget(canvas, 1)
        eff = QGraphicsDropShadowEffect(card)
        eff.setBlurRadius(20); eff.setXOffset(0); eff.setYOffset(3)
        eff.setColor(QColor(18, 28, 40, 30))
        card.setGraphicsEffect(eff)
        return ax, canvas, card, tlabel, focus

    def set_color_scale(self, limits, label, flat_value=None):
        from matplotlib.colorbar import ColorbarBase
        from matplotlib.colors import Normalize
        from matplotlib.ticker import ScalarFormatter, MaxNLocator, FuncFormatter
        self.colorbar_axis.clear()
        self.colorbar_axis.set_visible(limits is not None)
        self.shared_colorbar = None
        if limits is not None:
            bar = ColorbarBase(self.colorbar_axis, cmap='turbo', norm=Normalize(*limits))
            bar.set_label(label, fontsize=9, labelpad=6)
            if flat_value is not None:
                bar.set_ticks([flat_value])
            else:
                bar.locator = MaxNLocator(nbins=5)
            if flat_value is not None:
                bar.formatter = FuncFormatter(lambda value, position: f'{value:.9g}')
            else:
                formatter = ScalarFormatter(useOffset=False)
                formatter.set_powerlimits((-3, 3))
                bar.formatter = formatter
            bar.update_ticks()
            bar.ax.tick_params(labelsize=8)
            self.shared_colorbar = bar
        self.colorbar_canvas.draw_idle()

    def set_titles(self, mode='raw'):
        if mode != 'raw':
            try:
                order = int(str(mode).rsplit('_', 1)[1])
            except (ValueError, IndexError):
                order = 1
            self.title_3d.setText(f"3D {order}阶去除后残差")
            self.title_xy.setText(f"XY {order}阶残差色图")
            self.title_xz.setText(f"X-{order}阶残差投影")
            self.title_yz.setText(f"Y-{order}阶残差投影")
        else:
            self.title_3d.setText("3D 原始高度"); self.title_xy.setText("XY 俯视分布")
            self.title_xz.setText("X-Z 投影"); self.title_yz.setText("Y-Z 投影")

    def draw(self):
        # 同步 draw()（非 draw_idle）：确保滤波/去倾斜等改动后四张图立即重绘，
        # 否则带框选(useblit)的 XZ/YZ 投影会滞留旧画面，需点一下才刷新。
        for c in self._canvases:
            c.draw()


class ParallelismCanvas(FigureCanvas):
    """平行度分析专用静态预览：基准面和测量面分成两个 3D 图。"""
    def __init__(self, parent=None):
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        self.fig = Figure(constrained_layout=True)
        self.ax_base = self.fig.add_subplot(121, projection='3d')
        self.ax_measure = self.fig.add_subplot(122, projection='3d')
        super().__init__(self.fig)
        self.setParent(parent)
        self.setMinimumHeight(260)
        self.plot_records(None, None)

    def _empty_axis(self, ax, title):
        ax.clear()
        ax.set_title(title)
        ax.text2D(0.5, 0.5, "等待写入数据", transform=ax.transAxes,
                  ha='center', va='center', color='#8a94a3')
        ax.set_axis_off()

    def _draw_record(self, ax, rec, title, plane_color):
        if rec is None:
            self._empty_axis(ax, title)
            return
        x, y, z = rec['x'], rec['y'], rec['z']
        m = rec['metrics']
        ax.clear()
        ax.set_axis_on()
        ax.set_title(f"{title}: {rec['name']}", fontsize=10)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_zlabel("Z (mm)")
        ax.grid(True, linestyle='-', linewidth=0.6, color='#edf0f3')

        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        x, y, z = x[finite], y[finite], z[finite]
        n = len(z)
        if n == 0:
            self._empty_axis(ax, title)
            return
        if n > 25000:
            idx = np.linspace(0, n - 1, 25000, dtype=int)
            sx, sy, sz = x[idx], y[idx], z[idx]
        else:
            sx, sy, sz = x, y, z

        xmin, xmax = float(np.min(x)), float(np.max(x))
        ymin, ymax = float(np.min(y)), float(np.max(y))
        if np.isclose(xmin, xmax):
            xmin -= 0.5; xmax += 0.5
        if np.isclose(ymin, ymax):
            ymin -= 0.5; ymax += 0.5
        xx, yy = np.meshgrid(np.linspace(xmin, xmax, 22), np.linspace(ymin, ymax, 22))
        zz = m['a'] * xx + m['b'] * yy + m['c']
        ax.plot_surface(xx, yy, zz, color=plane_color, alpha=0.28, edgecolor='none', shade=False)

        size = 8 if len(sx) <= 12000 else 5
        ax.scatter(sx, sy, sz, c=sz, s=size, cmap='turbo', alpha=0.78,
                   edgecolors='none', depthshade=False, rasterized=True)
        ax.text2D(0.01, 0.98,
                  f"点数 {n:,} | Rx {m['rx']:.2f} µrad | Ry {m['ry']:.2f} µrad",
                  transform=ax.transAxes, ha='left', va='top', fontsize=8,
                  color='#4b5563', bbox=dict(boxstyle='round,pad=0.25', fc='white', ec='#e5e7eb', alpha=0.86))
        ax.view_init(elev=24, azim=-52)
        set_surface_box_aspect(ax, x, y, z, zoom=1.08, z_tick_count=3)
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_facecolor('#fbfcfd')
            pane.set_edgecolor('#e6eaee')
            pane.set_alpha(1.0)

    def plot_records(self, base_rec, measure_rec):
        self.fig.clear()
        self.ax_base = self.fig.add_subplot(121, projection='3d')
        self.ax_measure = self.fig.add_subplot(122, projection='3d')
        self._draw_record(self.ax_base, base_rec, "基准面 3D 拟合预览", '#2f6db0')
        self._draw_record(self.ax_measure, measure_rec, "测量面 3D 拟合预览", '#f59e0b')
        self.draw()


class GapMatchCanvas(FigureCanvas):
    """多层胶厚 XY 配准画布；堆叠固定，单片层只允许平移。"""

    layer_moved = pyqtSignal(str, float, float, bool)

    def __init__(self, parent=None):
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        self.fig = Figure(constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setMinimumHeight(360)
        self._records = {'stack': None, 'base1': None, 'base2': None}
        self._active_layer = None
        self._drag_state = None
        self._last_drag_emit = 0.0
        self._home_limits = None
        self._record_key = None
        self.mpl_connect('button_press_event', self._on_press)
        self.mpl_connect('motion_notify_event', self._on_motion)
        self.mpl_connect('button_release_event', self._on_release)
        self.mpl_connect('scroll_event', self._on_scroll)
        self.plot_registration(None, None, None, 0.05, None, None)

    @staticmethod
    def _display_xy(rec, limit=50000):
        if rec is None:
            return np.array([]), np.array([])
        x = np.asarray(rec['x'], dtype=float) + float(rec.get('offset_x', 0.0))
        y = np.asarray(rec['y'], dtype=float) + float(rec.get('offset_y', 0.0))
        finite = np.isfinite(x) & np.isfinite(y)
        x, y = x[finite], y[finite]
        if len(x) > limit:
            pick = np.linspace(0, len(x) - 1, limit, dtype=int)
            x, y = x[pick], y[pick]
        return x, y

    def _set_drag_cursor(self, closed=False):
        if self._active_layer in ('base1', 'base2') and self._records.get(self._active_layer) is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor if closed else Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _on_press(self, event):
        if (event.button == 1 and bool(getattr(event, 'dblclick', False))
                and event.inaxes is self.ax):
            self._restore_home_view()
            return
        if (event.button != 1 or event.inaxes is not self.ax or event.xdata is None or event.ydata is None
                or self._active_layer not in ('base1', 'base2')):
            return
        rec = self._records.get(self._active_layer)
        if rec is None:
            return
        self._drag_state = (
            float(event.xdata), float(event.ydata),
            float(rec.get('offset_x', 0.0)), float(rec.get('offset_y', 0.0)),
        )
        self._set_drag_cursor(closed=True)

    def _on_motion(self, event):
        if self._drag_state is None or event.inaxes is not self.ax or event.xdata is None or event.ydata is None:
            return
        now = time.monotonic()
        if now - self._last_drag_emit < 0.035:
            return
        self._last_drag_emit = now
        start_x, start_y, offset_x, offset_y = self._drag_state
        self.layer_moved.emit(
            self._active_layer,
            offset_x + float(event.xdata) - start_x,
            offset_y + float(event.ydata) - start_y,
            False,
        )

    def _on_release(self, event):
        if self._drag_state is None:
            return
        layer = self._active_layer
        start_x, start_y, offset_x, offset_y = self._drag_state
        self._drag_state = None
        self._set_drag_cursor(closed=False)
        if event.inaxes is self.ax and event.xdata is not None and event.ydata is not None:
            final_x = offset_x + float(event.xdata) - start_x
            final_y = offset_y + float(event.ydata) - start_y
        else:
            rec = self._records.get(layer)
            final_x = float(rec.get('offset_x', offset_x)) if rec is not None else offset_x
            final_y = float(rec.get('offset_y', offset_y)) if rec is not None else offset_y
        self.layer_moved.emit(layer, final_x, final_y, True)

    def _on_scroll(self, event):
        """Zoom around the cursor without changing any registration data."""
        if event.inaxes is not self.ax or event.xdata is None or event.ydata is None:
            return
        step = float(getattr(event, 'step', 0.0) or 0.0)
        if event.button == 'up' or step > 0:
            scale = 0.85
        elif event.button == 'down' or step < 0:
            scale = 1.0 / 0.85
        else:
            return
        if not np.isfinite(event.xdata) or not np.isfinite(event.ydata):
            return

        def scaled_limits(limits, center):
            low, high = map(float, limits)
            return (center + (low - center) * scale,
                    center + (high - center) * scale)

        self.ax.set_xlim(scaled_limits(self.ax.get_xlim(), float(event.xdata)))
        self.ax.set_ylim(scaled_limits(self.ax.get_ylim(), float(event.ydata)))
        self.draw_idle()

    def _restore_home_view(self):
        if self._home_limits is None:
            return
        self.ax.set_xlim(self._home_limits[0])
        self.ax.set_ylim(self._home_limits[1])
        self.draw_idle()

    def plot_registration(self, stack, base1, base2, tolerance, active_layer, diag,
                          preserve_view=False):
        record_key = tuple(
            None if rec is None else (id(rec.get('x')), id(rec.get('y')), len(rec.get('x', [])))
            for rec in (stack, base1, base2))
        data_changed = record_key != self._record_key
        previous_limits = None
        if preserve_view and not data_changed and self.ax.has_data():
            previous_limits = (self.ax.get_xlim(), self.ax.get_ylim())
        self._records = {'stack': stack, 'base1': base1, 'base2': base2}
        self._record_key = record_key
        self._active_layer = active_layer if active_layer in ('base1', 'base2') else None
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        ax = self.ax
        ax.clear()
        if stack is None and base1 is None and base2 is None:
            ax.set_title("多层胶厚 XY 配准")
            ax.text(0.5, 0.5, "依次写入堆叠总成、单片 1 / 单片 2 后显示 XY 点云",
                    transform=ax.transAxes, ha='center', va='center', color='#8a94a3')
            ax.set_axis_off()
            self._home_limits = None
            self.draw()
            return

        ax.set_axis_on()
        ax.set_title(f"多层胶厚 XY 配准 | 对齐判定: |ΔXY| ≤ {float(tolerance):.3f} mm", fontsize=11)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.grid(True, linestyle='-', linewidth=0.6, color='#edf0f3')

        layers = (
            ('stack', stack, '堆叠总成（固定基准）', '#7b8490', 10, 0.52, 1),
            ('base1', base1, '单片 1', '#2563eb', 12, 0.68, 2),
            ('base2', base2, '单片 2', '#7c3aed', 12, 0.68, 3),
        )
        for key, rec, label, color, size, alpha, zorder in layers:
            x, y = self._display_xy(rec)
            if len(x):
                suffix = " [当前可拖动]" if key == self._active_layer else ""
                ax.scatter(x, y, s=size, c=color, alpha=alpha, edgecolors='none',
                           label=f"{label}{suffix} ({int(rec['n']):,})",
                           rasterized=True, zorder=zorder)

        highlight_key = self._active_layer
        if highlight_key is None and diag is not None:
            highlight_key = 'final'
        if stack is not None and diag is not None:
            if highlight_key == 'final':
                valid = np.asarray(diag.get('final_valid', []), dtype=bool)
                highlight_label = "全部参与扣减点"
            else:
                layer_diag = (diag.get('layers') or {}).get(highlight_key, {})
                valid = np.asarray(layer_diag.get('valid', []), dtype=bool)
                highlight_label = "当前层已对齐点"
            sx = np.asarray(stack['x'], dtype=float)
            sy = np.asarray(stack['y'], dtype=float)
            finite = np.isfinite(sx) & np.isfinite(sy)
            if len(valid) == len(sx):
                valid = valid & finite
                indices = np.flatnonzero(valid)
                full_count = len(indices)
                if len(indices) > 40000:
                    indices = indices[np.linspace(0, len(indices) - 1, 40000, dtype=int)]
                if len(indices):
                    ax.scatter(sx[indices], sy[indices], s=30, facecolors='none',
                               edgecolors='#ef4444', linewidths=0.9, alpha=0.95,
                               label=f"{highlight_label} ({full_count:,})", rasterized=True, zorder=6)

        # Recreating the axes resets aspect to 'auto'. Limits alone do not
        # preserve the physical XY scale on a rectangular canvas.
        set_xy_equal_aspect(ax)
        if previous_limits is not None:
            ax.set_xlim(previous_limits[0])
            ax.set_ylim(previous_limits[1])
        else:
            self._home_limits = (ax.get_xlim(), ax.get_ylim())
        active_text = {
            'base1': '当前：单片 1，可左键拖动；滚轮缩放，双击复位',
            'base2': '当前：单片 2，可左键拖动；滚轮缩放，双击复位',
        }.get(self._active_layer, '请选择单片层进行粗对齐；滚轮缩放，双击复位')
        ax.text(0.01, 0.99, active_text, transform=ax.transAxes, ha='left', va='top',
                fontsize=9, color='#374151',
                bbox=dict(boxstyle='round,pad=0.28', fc='white', ec='#e5e7eb', alpha=0.90))
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc='lower right', frameon=True, fontsize=8)
        self._set_drag_cursor()
        self.draw()

    def plot_diagnostic(self, diag):
        """兼容旧调用；新界面统一走 plot_registration。"""
        self.plot_registration(
            self._records.get('stack'), self._records.get('base1'), self._records.get('base2'),
            float(diag.get('tolerance', 0.05)) if diag else 0.05,
            self._active_layer, diag,
        )
