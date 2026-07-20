import sys
import os
import re
import mmap
import json
import tempfile
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
    QStackedWidget, QSizeGrip,
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen
from scipy.spatial import cKDTree

from .plotting import set_surface_box_aspect, set_xy_equal_aspect



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
    def __init__(self, parent=None):
        super().__init__(parent)
        # YaHei 同时含中文与 µ(U+00B5)，去倾斜显示的 µm 轴标签也不再出现缺字方块
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        plt.rcParams.update({
            'figure.facecolor': '#ffffff', 'axes.facecolor': '#ffffff',
            'axes.edgecolor': '#d8dee4', 'axes.linewidth': 0.8,
            'axes.labelcolor': '#5b6672', 'axes.labelsize': 9,
            'grid.color': '#edf0f3', 'grid.linewidth': 0.7,
            'xtick.color': '#9aa4ae', 'ytick.color': '#9aa4ae',
            'xtick.labelsize': 8, 'ytick.labelsize': 8,
        })
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.ax3d, c3, card3, self.title_3d = self._make_card("3D 原始高度", '3d')
        self.ax_xy, cxy, cardxy, self.title_xy = self._make_card("XY 俯视分布", None)
        self.ax_xz, cxz, cardxz, self.title_xz = self._make_card("X-Z 剖面", None)
        self.ax_yz, cyz, cardyz, self.title_yz = self._make_card("Y-Z 剖面", None)
        self._canvases = [c3, cxy, cxz, cyz]
        grid.addWidget(card3, 0, 0); grid.addWidget(cardxy, 0, 1)
        grid.addWidget(cardxz, 1, 0); grid.addWidget(cardyz, 1, 1)

    def _make_card(self, title, projection):
        card = QFrame(); card.setObjectName("plotCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 10, 8); v.setSpacing(5)
        head = QHBoxLayout(); head.setSpacing(7)
        dot = QLabel(); dot.setObjectName("plotDot"); dot.setFixedSize(8, 8)
        tlabel = QLabel(title); tlabel.setObjectName("plotTitle")
        head.addWidget(dot); head.addWidget(tlabel); head.addStretch()
        v.addLayout(head)
        fig = Figure(constrained_layout=True)
        ax = fig.add_subplot(111, projection=projection)
        canvas = FigureCanvas(fig)
        v.addWidget(canvas, 1)
        eff = QGraphicsDropShadowEffect(card)
        eff.setBlurRadius(20); eff.setXOffset(0); eff.setYOffset(3)
        eff.setColor(QColor(18, 28, 40, 30))
        card.setGraphicsEffect(eff)
        return ax, canvas, card, tlabel

    def set_titles(self, detrended):
        if detrended:
            self.title_3d.setText("3D 去倾斜残差面型"); self.title_xy.setText("XY 俯视分布")
            self.title_xz.setText("X-残差剖面"); self.title_yz.setText("Y-残差剖面")
        else:
            self.title_3d.setText("3D 原始高度"); self.title_xy.setText("XY 俯视分布")
            self.title_xz.setText("X-Z 剖面"); self.title_yz.setText("Y-Z 剖面")

    def draw(self):
        # 同步 draw()（非 draw_idle）：确保滤波/去倾斜等改动后四张图立即重绘，
        # 否则带框选(useblit)的 XZ/YZ 剖面会滞留旧画面，需点一下才刷新。
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
        self.setMinimumHeight(360)
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
    """多层胶厚扣减匹配诊断：用堆叠层 XY 点显示哪些点没有匹配到单片层。"""
    def __init__(self, parent=None):
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        self.fig = Figure(constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setMinimumHeight(460)
        self.plot_diagnostic(None)

    def plot_diagnostic(self, diag):
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        ax = self.ax
        ax.clear()
        if not diag:
            ax.set_title("多层扣减匹配诊断")
            ax.text(0.5, 0.5, "计算胶厚后显示匹配/未匹配点分布",
                    transform=ax.transAxes, ha='center', va='center', color='#8a94a3')
            ax.set_axis_off()
            self.draw()
            return

        x = diag['x']
        y = diag['y']
        valid1 = diag['valid1']
        valid2 = diag.get('valid2')
        final_valid = diag['final_valid']
        finite = np.isfinite(x) & np.isfinite(y)
        x = x[finite]
        y = y[finite]
        valid1 = valid1[finite]
        final_valid = final_valid[finite]
        if valid2 is not None:
            valid2 = valid2[finite]

        total_full = len(x)
        matched_full = int(final_valid.sum())
        display_note = ""
        if total_full > 100000:
            unmatched_idx = np.flatnonzero(~final_valid)
            matched_idx = np.flatnonzero(final_valid)
            keep_parts = []
            if len(unmatched_idx) > 0:
                max_unmatched = min(len(unmatched_idx), 60000)
                if len(unmatched_idx) > max_unmatched:
                    unmatched_idx = unmatched_idx[np.linspace(0, len(unmatched_idx) - 1, max_unmatched, dtype=int)]
                keep_parts.append(unmatched_idx)
            remaining = max(10000, 100000 - sum(len(k) for k in keep_parts))
            if len(matched_idx) > remaining:
                matched_idx = matched_idx[np.linspace(0, len(matched_idx) - 1, remaining, dtype=int)]
            keep_parts.append(matched_idx)
            keep = np.sort(np.concatenate(keep_parts)) if keep_parts else np.array([], dtype=int)
            x, y = x[keep], y[keep]
            valid1, final_valid = valid1[keep], final_valid[keep]
            if valid2 is not None:
                valid2 = valid2[keep]
            display_note = f" | 显示 {len(keep):,}/{total_full:,} 点"

        ax.set_title(f"多层扣减匹配诊断 | 容差 {diag['tolerance']:.3f} mm", fontsize=11)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.grid(True, linestyle='-', linewidth=0.6, color='#edf0f3')
        set_xy_equal_aspect(ax)

        def scatter_mask(mask, label, color, marker='o', size=16, alpha=0.90, zorder=2):
            if np.any(mask):
                kwargs = {
                    's': size, 'c': color, 'marker': marker, 'alpha': alpha,
                    'label': f"{label} ({int(mask.sum()):,})",
                    'rasterized': True, 'zorder': zorder
                }
                if marker not in ('x', '+', '1', '2', '3', '4'):
                    kwargs['edgecolors'] = 'none'
                ax.scatter(x[mask], y[mask], **kwargs)

        scatter_mask(final_valid, "成功匹配", '#2f6db0', 'o', 12, 0.72, 1)
        if valid2 is None:
            scatter_mask(~valid1, "未匹配单片1", '#dc2626', 'x', 24, 0.95, 4)
        else:
            miss_both = (~valid1) & (~valid2)
            miss_b1 = (~valid1) & valid2
            miss_b2 = valid1 & (~valid2)
            scatter_mask(miss_b1, "未匹配单片1", '#dc2626', 'x', 28, 0.95, 4)
            scatter_mask(miss_b2, "未匹配单片2", '#f59e0b', '^', 26, 0.95, 4)
            scatter_mask(miss_both, "两层都未匹配", '#7c3aed', 'x', 32, 0.95, 5)

        ax.text(0.01, 0.99,
                f"堆叠点 {total_full:,} | 成功 {matched_full:,} | 未参与扣减 {total_full - matched_full:,}{display_note}",
                transform=ax.transAxes, ha='left', va='top', fontsize=9,
                color='#374151', bbox=dict(boxstyle='round,pad=0.28', fc='white', ec='#e5e7eb', alpha=0.88))
        ax.legend(loc='lower right', frameon=True, fontsize=8)
        self.draw()
