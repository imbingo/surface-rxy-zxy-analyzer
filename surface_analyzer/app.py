"""Qt application shell for Surface Analyzer V4.0."""

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


from .config import (
    ACCENT, APP_VERSION, BIGFILE_MODE_PRESETS, DISPLAY_POINT_LIMIT,
    LARGE_TEXT_FILE_BYTES, LARGE_TEXT_IMPORT_LIMIT, MISSING_TEXT_TOKENS,
)
from .widgets import (
    NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox,
    MultiViewCanvas, ParallelismCanvas, GapMatchCanvas,
)
from .mixins.analysis import AnalysisMixin
from .mixins.data_io import DataIOMixin
from .mixins.gap import GapAnalysisMixin
from .mixins.parallelism import ParallelismMixin
from .mixins.recipe import RecipeMixin
from .mixins.roi import ROIMixin
from .mixins.reporting import ReportingMixin



class SurfaceAnalyzerPro(AnalysisMixin, DataIOMixin, GapAnalysisMixin, ParallelismMixin, RecipeMixin, ROIMixin, ReportingMixin, QMainWindow):
    APP_VERSION = APP_VERSION
    DISPLAY_POINT_LIMIT = DISPLAY_POINT_LIMIT
    LARGE_TEXT_FILE_BYTES = LARGE_TEXT_FILE_BYTES
    LARGE_TEXT_IMPORT_LIMIT = LARGE_TEXT_IMPORT_LIMIT
    BIGFILE_MODE_PRESETS = BIGFILE_MODE_PRESETS
    MISSING_TEXT_TOKENS = MISSING_TEXT_TOKENS
    ACCENT = ACCENT

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"面型及Rxy分析ZXY版 {self.APP_VERSION}")
        self.resize(1860, 980)

        # 数据流
        self.absolute_raw_df = None
        self.df_raw = None
        self.manual_mask = None
        self.temp_selected_mask = None
        self.active_idx = None
        self.transform_pipeline = []
        self.current_coeffs = None
        self.current_source_name = "--"   # 当前视图数据来源（文件名 / GAP结果）
        self.n_filtered = 0               # 最近一次滤波剔除点数
        self.last_metrics = None          # 最近一次分析指标（导出元数据用）
        self.display_detrended = False    # 去倾斜显示：只影响绘图/框选，不改变数据与指标
        self.last_import_note = ""        # 最近一次导入说明
        self.last_displayed_points = 0     # 最近一次绘图实际显示点数
        self.large_file_mode = 'standard'  # 大文件策略模式：fast / standard / precise / custom
        self.large_file_sample_method = 'file_position'  # V3.9.2 默认：文件位置均匀抽样，优先流畅
        self.large_text_grid_count = 0     # 0=自动；>0 为用户指定的 X/Y 单边网格数
        self.large_text_stride_n = 10      # 倍率降采样：每 N 行/像素取 1 个
        self.height_matrix_pitch_x_um = 47.242
        self.height_matrix_pitch_y_um = 47.242
        self.height_matrix_z_unit = "µm"
        self.roi_enabled = False           # V3.8.4+: XY ROI 保留区域开关
        self.roi_shapes = []               # list[dict]，当前物料坐标系 X/Y(mm)
        self.roi_next_id = 1
        self.selection_mode = 'delete'     # delete / roi_rect / roi_circle / roi_smart
        self.last_roi_keep_count = None
        self.import_info = {               # 导入状态：用于UI与导出元数据
            'file_size_bytes': 0,
            'file_size_mb': 0.0,
            'strategy': '--',
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'import_rows': 0,
            'display_limit': self.DISPLAY_POINT_LIMIT,
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label(),
            'grid_count': self.large_text_grid_count,
            'stride_n': self.large_text_stride_n,
            'height_matrix': False,
            'notes': ''
        }
        # V3.5.1: 大文件策略不再占用左侧UI，改为右侧工具条按钮弹窗设置
        self.auto_sample_large_text = True
        self.large_text_threshold_mb = self.LARGE_TEXT_FILE_BYTES // (1024 * 1024)
        self.large_text_import_limit = self.LARGE_TEXT_IMPORT_LIMIT
        self.display_point_limit = self.DISPLAY_POINT_LIMIT
        # Recipe 可在未载入数据前导入；载入文件并完成列填充后自动应用
        self.pending_recipe = None

        # 变换缓存：避免每次框选都全量重算
        self._df_version = 0
        self._trans_cache_key = None
        self._trans_cache_data = None

        # 多层计算寄存器：dict {'x','y','z','name','n'} 或 None
        self.data_base1 = None
        self.data_base2 = None
        self.data_stack = None

        # V3.8.0: 平行度分析寄存器。保存主页面当前已变换、滤波、删点后的参与拟合点。
        self.parallel_base = None
        self.parallel_measure = None
        self.parallel_result = None

        self.selectors = []
        self.init_ui()

    def _apply_theme(self):
        """全局 QSS：只管视觉，不影响任何逻辑。objectName 驱动强调/危险/卡片样式。"""
        self.setStyleSheet("""
            QMainWindow { background: #eceff3; }
            #centralRoot { background: #eceff3; }
            #appBar { background: #ffffff; border-bottom: 1px solid #e7ebef; }
            #brandDot { background: #2f6db0; border-radius: 5px; }
            #appTitle { color: #1f2933; font-size: 15px; font-weight: bold; }
            #verPill { color: #2f6db0; background: #eaf1f9; border: 1px solid #d6e5f4;
                       border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: bold; }
            QTabWidget::pane { border: 1px solid #e7ebef; background: #ffffff; border-radius: 8px; top: -1px; }
            QTabBar::tab { background: #eef1f4; color: #7a858f; padding: 8px 18px;
                           border: 1px solid #e3e7eb; border-bottom: none;
                           border-top-left-radius: 7px; border-top-right-radius: 7px; font-weight: bold; }
            QTabBar::tab:selected { background: #ffffff; color: #1f2933; }
            QScrollArea { border: none; background: #ffffff; }
            QGroupBox { border: 1px solid #eef1f4; border-radius: 8px; margin-top: 8px;
                        background: #ffffff; font-weight: bold; color: #46505a; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLabel { color: #2b333c; }
            QPushButton { background: #ffffff; border: 1px solid #dde2e7; border-radius: 7px;
                          padding: 7px 12px; color: #46505a; }
            QPushButton:hover { border-color: #2f6db0; color: #2f6db0; }
            QPushButton:disabled { color: #b6bdc4; border-color: #e9edf1; }
            QComboBox, QSpinBox, QDoubleSpinBox { border: 1px solid #d9dee3; border-radius: 6px;
                          padding: 4px 8px; background: #ffffff; color: #2b333c; min-height: 22px; }
            QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #b6bdc4; background: #f6f8fa; }
            #accentBtn { background: #2f6db0; border: none; color: #ffffff; font-weight: bold; }
            #accentBtn:hover { background: #285f9a; color: #ffffff; }
            #accentBtn:disabled { background: #aebfd4; }
            #accentSoftBtn { background: #eaf1f9; border: 1px solid #d2e2f3; color: #2f6db0; font-weight: bold; }
            #accentSoftBtn:hover { background: #dceaf7; color: #2f6db0; }
            #dangerBtn { background: #ffffff; border: 1px solid #ecc9c6; color: #b4453a; font-weight: bold; }
            #dangerBtn:hover { background: #fbf0ef; color: #b4453a; }
            #stepBadge { background: #eef2f6; color: #5b6672; border-radius: 5px; font-weight: bold; font-size: 11px; }
            #stepBadgeActive { background: #2f6db0; color: #ffffff; border-radius: 5px; font-weight: bold; font-size: 11px; }
            #stepTitle { color: #2b333c; font-weight: bold; font-size: 12px; }
            #stepLine { background: #eef1f4; }
            #resultsStrip { background: transparent; border: none; }
            #stripCaption { color: #5b6672; font-weight: bold; font-size: 11px; letter-spacing: 1px; }
            #stripEqn { color: #9aa4ae; font-family: 'Consolas','Microsoft YaHei'; font-size: 11px; }
            #metricCard { background: #f7f9fc; border: 1px solid #e6ebf1; border-radius: 12px; }
            #metricCardAccent { background: #eaf3fd; border: 1px solid #cbe0f4; border-radius: 12px; }
            #metricTitle { color: #7e8893; font-size: 10px; font-weight: bold; }
            #metricTitleAccent { color: #2f6db0; font-size: 10px; font-weight: bold; }
            #metricValue { color: #14202c; font-family: 'Consolas','Microsoft YaHei'; font-size: 20px; font-weight: bold; }
            #metricValueAccent { color: #2f6db0; font-family: 'Consolas','Microsoft YaHei'; font-size: 20px; font-weight: bold; }
            #metricUnit { color: #aab2ba; font-size: 10px; }
            #plotCard { background: #ffffff; border: 1px solid #e9edf1; border-radius: 12px; }
            #plotDot { background: #2f6db0; border-radius: 4px; }
            #plotTitle { color: #2b333c; font-size: 12px; font-weight: bold; }
            QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right;
                                   width: 20px; border: none; }
            QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border;
                                    subcontrol-position: top right; width: 16px; border: none; }
            QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border;
                                    subcontrol-position: bottom right; width: 16px; border: none; }
            #mutedNote { color: #9aa4ae; font-size: 11px; }
            #pipelineNote { color: #6b7682; font-family: 'Consolas','Microsoft YaHei'; font-size: 11px;
                            background: #f6f8fa; border: 1px solid #eef1f4; border-radius: 6px; padding: 5px 8px; }
            #sourceNote { color: #3a444e; font-size: 12px; font-weight: bold;
                          background: #f6f8fa; border: 1px solid #e9edf1; border-radius: 6px; padding: 6px 9px; }
            #poseTile { background: #ffffff; border: 1px solid #eaeef2; border-radius: 11px; }
            #poseTile:hover { border-color: #2f6db0; background: #f6faff; }
            #poseTileActive { background: #eaf1f9; border: 1px solid #cfe0f1; border-radius: 11px; }
            #poseTileActive:hover { background: #dceaf7; }
            #poseIcon { font-size: 16px; color: #55606b; background: transparent; }
            #poseLabel { font-size: 10px; color: #6b7682; background: transparent; }
            #poseTileActive #poseIcon { color: #2f6db0; }
            #poseTileActive #poseLabel { color: #2f6db0; }
            #winBtn { background: transparent; border: none; border-radius: 6px;
                      color: #5b6672; font-size: 14px; padding: 0; }
            #winBtn:hover { background: #eef2f6; color: #1f2933; }
            #winClose { background: transparent; border: none; border-radius: 6px;
                        color: #5b6672; font-size: 13px; padding: 0; }
            #winClose:hover { background: #e15b4d; color: #ffffff; }
        """)
        # 用自绘 chevron 图标替换 Windows 原生下拉/微调箭头，统一为细线 V 形
        down = self._make_arrow_png('down')
        up = self._make_arrow_png('up')
        if down and up:
            self.setStyleSheet(self.styleSheet() + (
                "QComboBox::down-arrow { image: url(%s); width: 11px; height: 11px; margin-right: 5px; }"
                "QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(%s); width: 9px; height: 9px; }"
                "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(%s); width: 9px; height: 9px; }"
            ) % (down, up, down))

    @staticmethod
    def _make_arrow_png(direction='down', color='#8a949e', size=22):
        """生成一个细线 chevron(V形) PNG 并返回正斜杠路径，供 QSS image:url() 用。"""
        try:
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(color))
            pen.setWidthF(size * 0.11)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            pad = size * 0.30
            cx = size / 2.0
            if direction == 'down':
                a, b, c = QPointF(pad, size * 0.40), QPointF(cx, size * 0.62), QPointF(size - pad, size * 0.40)
            else:
                a, b, c = QPointF(pad, size * 0.60), QPointF(cx, size * 0.38), QPointF(size - pad, size * 0.60)
            p.drawLine(a, b)
            p.drawLine(b, c)
            p.end()
            path = os.path.join(tempfile.gettempdir(), f'sra_chevron_{direction}.png')
            pm.save(path)
            return path.replace('\\', '/')
        except Exception:
            return None

    def _add_shadow(self, widget, blur=22, dy=4, alpha=38):
        """给卡片挂柔和投影，营造模块化悬浮质感（理想车机 / Apple 风）。
        每个 widget 只能有一个 graphics effect，且父子不要同时挂以免重复渲染。"""
        eff = QGraphicsDropShadowEffect(widget)
        eff.setBlurRadius(blur)
        eff.setXOffset(0)
        eff.setYOffset(dy)
        eff.setColor(QColor(18, 28, 40, alpha))
        widget.setGraphicsEffect(eff)
        return widget

    def init_ui(self):
        self._apply_theme()
        # 无边框自绘标题栏：顶部应用栏兼任标题栏（拖动/双击最大化），状态栏带缩放手柄
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMinimumSize(1180, 700)
        central = QWidget()
        central.setObjectName("centralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 顶部应用栏：标题 + 版本 + Recipe / 大文件策略 ----------
        appbar = QWidget()
        appbar.setObjectName("appBar")
        appbar.setFixedHeight(50)
        ab = QHBoxLayout(appbar)
        ab.setContentsMargins(16, 0, 14, 0)
        ab.setSpacing(9)
        dot = QLabel(); dot.setObjectName("brandDot"); dot.setFixedSize(10, 10)
        app_title = QLabel("面型及 Rxy 分析"); app_title.setObjectName("appTitle")
        ver_pill = QLabel(self.APP_VERSION); ver_pill.setObjectName("verPill")
        ab.addWidget(dot); ab.addWidget(app_title); ab.addWidget(ver_pill)
        ab.addStretch()

        self.btn_import_recipe = QPushButton("导入 Recipe")
        self.btn_import_recipe.setToolTip("读取Recipe并自动写入当前UI参数；若尚未载入数据，列映射会在下次载入后自动应用。")
        self.btn_import_recipe.clicked.connect(self.import_recipe)
        self.btn_export_recipe = QPushButton("导出 Recipe")
        self.btn_export_recipe.setToolTip("保存当前单位、列映射、物料旋转组合、滤波、ROI、大文件显示和Gap参数。")
        self.btn_export_recipe.clicked.connect(self.export_recipe)
        self.btn_bigfile_settings = QPushButton("大文件策略")
        self.btn_bigfile_settings.setToolTip("设置超大TXT预抽样模式、导入上限、绘图显示上限。")
        self.btn_bigfile_settings.clicked.connect(self.show_bigfile_settings_dialog)
        for b in (self.btn_import_recipe, self.btn_export_recipe, self.btn_bigfile_settings):
            b.setFixedHeight(30)
            ab.addWidget(b)

        # 窗口控制按钮（最小化 / 最大化-还原 / 关闭）
        ab.addSpacing(8)
        self.btn_win_min = QPushButton("–"); self.btn_win_min.setObjectName("winBtn")
        self.btn_win_max = QPushButton("□"); self.btn_win_max.setObjectName("winBtn")
        self.btn_win_close = QPushButton("✕"); self.btn_win_close.setObjectName("winClose")
        for b in (self.btn_win_min, self.btn_win_max, self.btn_win_close):
            b.setFixedSize(36, 28)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ab.addWidget(b)
        self.btn_win_min.clicked.connect(self.showMinimized)
        self.btn_win_max.clicked.connect(self._toggle_max_restore)
        self.btn_win_close.clicked.connect(self.close)

        self._appbar = appbar
        appbar.installEventFilter(self)
        root.addWidget(appbar)

        # 隐藏状态标签：仅供内部更新/写入按钮 tooltip 与状态栏，不占UI
        self.lbl_import_status = QLabel("导入状态: --")
        self.lbl_import_status.setVisible(False)

        # ---------- 主体：左控制面板 | 右（结果条 + 工具条 + 四视图）----------
        body = QWidget()
        self._body = body
        body.setMouseTracking(True)
        body.installEventFilter(self)
        body_l = QHBoxLayout(body)
        body_l.setContentsMargins(12, 12, 12, 12)
        body_l.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tabs = QTabWidget()
        self.tabs.setFixedWidth(440)
        tab_main = QWidget()
        self.setup_main_tab(tab_main)
        self.tabs.addTab(tab_main, "单层 / 主控分析")
        tab_math = QWidget()
        self.setup_math_tab(tab_math)
        self.math_tab_index = self.tabs.addTab(tab_math, "多层胶厚扣减")

        tab_parallel = QWidget()
        self.setup_parallel_tab(tab_parallel)
        self.parallel_tab_index = self.tabs.addTab(tab_parallel, "平行度分析")

        right_main_panel = QWidget()
        right_layout = QVBoxLayout(right_main_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self._build_results_strip())

        self.canvas = MultiViewCanvas(self)
        self._xy_click_cid = self.canvas.ax_xy.figure.canvas.mpl_connect(
            'button_press_event', self.on_canvas_click)
        right_layout.addWidget(self.canvas, 1)

        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(right_main_panel)
        self.right_stack.addWidget(self._build_parallel_right_panel())
        self.right_stack.addWidget(self._build_gap_right_panel())
        self.tabs.currentChanged.connect(self._on_tab_changed)

        splitter.addWidget(self.tabs)
        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(1, 4)
        body_l.addWidget(splitter)
        root.addWidget(body, 1)

        # 无边框窗口：用状态栏右下角缩放手柄实现拖拽缩放
        self.statusBar().setSizeGripEnabled(True)

    def _toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
            self.btn_win_max.setText("□")
        else:
            self.showMaximized()
            self.btn_win_max.setText("❐")

    def _resize_edge_at(self, gpos):
        """根据全局光标位置判断是否贴近窗口边缘，返回组合 Qt.Edge 或 None。"""
        if self.isMaximized():
            return None
        p = self.mapFromGlobal(gpos)
        r = self.rect()
        m = 6
        parts = []
        if p.x() <= m:
            parts.append(Qt.Edge.LeftEdge)
        elif p.x() >= r.width() - m:
            parts.append(Qt.Edge.RightEdge)
        if p.y() <= m:
            parts.append(Qt.Edge.TopEdge)
        elif p.y() >= r.height() - m:
            parts.append(Qt.Edge.BottomEdge)
        if not parts:
            return None
        edges = parts[0]
        for x in parts[1:]:
            edges |= x
        return edges

    @staticmethod
    def _cursor_for_edges(edges):
        L, R = Qt.Edge.LeftEdge, Qt.Edge.RightEdge
        T, B = Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        if (bool(edges & L) and bool(edges & T)) or (bool(edges & R) and bool(edges & B)):
            return Qt.CursorShape.SizeFDiagCursor
        if (bool(edges & R) and bool(edges & T)) or (bool(edges & L) and bool(edges & B)):
            return Qt.CursorShape.SizeBDiagCursor
        if bool(edges & L) or bool(edges & R):
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    def eventFilter(self, obj, event):
        """应用栏兼任标题栏：左键拖动移动窗口、双击最大化/还原；
        主体外缘 6px 作为缩放边，左键按下触发系统缩放（无边框窗口的边缘拉伸）。"""
        if obj is getattr(self, '_appbar', None):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
                    return True
            elif et == QEvent.Type.MouseButtonDblClick:
                self._toggle_max_restore()
                return True
        elif obj is getattr(self, '_body', None):
            et = event.type()
            if et == QEvent.Type.MouseMove and not (event.buttons() & Qt.MouseButton.LeftButton):
                edges = self._resize_edge_at(event.globalPosition().toPoint())
                obj.setCursor(self._cursor_for_edges(edges) if edges else Qt.CursorShape.ArrowCursor)
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edges = self._resize_edge_at(event.globalPosition().toPoint())
                if edges is not None:
                    wh = self.windowHandle()
                    if wh is not None:
                        wh.startSystemResize(edges)
                        return True
        return super().eventFilter(obj, event)

    def _make_metric_card(self, title, value_label, accent=False):
        card = QFrame()
        card.setObjectName("metricCardAccent" if accent else "metricCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("metricTitleAccent" if accent else "metricTitle")
        value_label.setObjectName("metricValueAccent" if accent else "metricValue")
        v.addWidget(t)
        v.addWidget(value_label)
        self._add_shadow(card, blur=20, dy=3, alpha=34)
        return card

    def _build_results_strip(self):
        strip = QFrame()
        strip.setObjectName("resultsStrip")
        outer = QVBoxLayout(strip)
        outer.setContentsMargins(6, 2, 6, 8)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        cap = QLabel("实时分析结果")
        cap.setObjectName("stripCaption")
        self.lbl_eqn = QLabel("--")
        self.lbl_eqn.setObjectName("stripEqn")
        head.addWidget(cap)
        head.addWidget(self.lbl_eqn)
        head.addStretch()
        # 去倾斜显示开关并入标题行右侧，省掉单独一行（绘图区垂直空间紧张）
        self.chk_detrend_display = QCheckBox("去倾斜显示")
        self.chk_detrend_display.setToolTip(
            "开启后，3D/XZ/YZ图中的Z轴改为：实测Z - 当前最佳拟合平面，单位 µm。\n"
            "用于更清晰观察物料表面面型起伏；只影响显示和框选，不改变Rx/Ry/PV/TTV计算，也不修改原始数据。")
        self.chk_detrend_display.stateChanged.connect(self._on_detrend_display_changed)
        self.lbl_detrend_info = QLabel("原始Z高度 mm")
        self.lbl_detrend_info.setObjectName("mutedNote")
        head.addWidget(self.chk_detrend_display)
        head.addWidget(self.lbl_detrend_info)
        outer.addLayout(head)

        cards = QHBoxLayout()
        cards.setSpacing(13)
        cards.setContentsMargins(4, 2, 4, 4)
        self.lbl_z = QLabel("--")
        self.lbl_pv = QLabel("--")
        self.lbl_ttv = QLabel("--")
        self.lbl_rx = QLabel("--")
        self.lbl_ry = QLabel("--")
        cards.addWidget(self._make_metric_card("平均厚度 Z (mm)", self.lbl_z), 1)
        cards.addWidget(self._make_metric_card("面型 PV·法向 (µm)", self.lbl_pv, accent=True), 1)
        cards.addWidget(self._make_metric_card("TTV·Z极差 (µm)", self.lbl_ttv), 1)
        cards.addWidget(self._make_metric_card("物料 Rx (µrad)", self.lbl_rx), 1)
        cards.addWidget(self._make_metric_card("物料 Ry (µrad)", self.lbl_ry), 1)
        outer.addLayout(cards)
        return strip

    def _clear_result_labels(self):
        if not hasattr(self, 'lbl_eqn'):
            return
        self.lbl_eqn.setText("--")
        for lab in (self.lbl_z, self.lbl_pv, self.lbl_ttv, self.lbl_rx, self.lbl_ry):
            lab.setText("--")

    def _step_header(self, num, text, hint=None, active=False):
        """编号分步标题：序号徽章 + 标题 + 可选提示 + 分隔线。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        badge = QLabel(str(num))
        badge.setObjectName("stepBadgeActive" if active else "stepBadge")
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(text)
        title.setObjectName("stepTitle")
        h.addWidget(badge)
        h.addWidget(title)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("mutedNote")
            h.addWidget(hint_lbl)
        line = QWidget()
        line.setObjectName("stepLine")
        line.setFixedHeight(1)
        h.addWidget(line, 1)
        return row

    def _pose_tile(self, icon, label, slot, active=False, tooltip=None):
        """姿态变换磁贴：线性图标在上、文字在下，方块卡片样式（对齐方案A）。"""
        btn = QPushButton()
        btn.setObjectName("poseTileActive" if active else "poseTile")
        btn.setFixedHeight(52)
        btn.clicked.connect(slot)
        if tooltip:
            btn.setToolTip(tooltip)
        v = QVBoxLayout(btn)
        v.setContentsMargins(2, 5, 2, 5)
        v.setSpacing(1)
        ic = QLabel(icon); ic.setObjectName("poseIcon")
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tx = QLabel(label); tx.setObjectName("poseLabel")
        tx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for lab in (ic, tx):
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v.addWidget(ic); v.addWidget(tx)
        self._add_shadow(btn, blur=14, dy=2, alpha=26)
        return btn

    @staticmethod
    def _configure_left_scroll(scroll):
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def setup_main_tab(self, parent_widget):
        scroll = QScrollArea()
        self._configure_left_scroll(scroll)
        w = QWidget()
        ll = QVBoxLayout(w)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(9)

        # ---------- 1. 载入数据（批量处理紧邻）----------
        ll.addWidget(self._step_header(1, "载入数据", active=True))
        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.btn_open = QPushButton("载入测量数据")
        self.btn_open.setObjectName("accentBtn")
        self.btn_open.setFixedHeight(38)
        self.btn_open.clicked.connect(self.load_file)
        self.btn_reset_all = QPushButton("重置")
        self.btn_reset_all.setFixedHeight(38)
        self.btn_reset_all.setFixedWidth(92)
        self.btn_reset_all.clicked.connect(self.reset_all)
        file_layout.addWidget(self.btn_open)
        file_layout.addWidget(self.btn_reset_all)
        ll.addLayout(file_layout)

        self.lbl_source = QLabel("当前数据: 未载入")
        self.lbl_source.setObjectName("sourceNote")
        self.lbl_source.setWordWrap(True)
        ll.addWidget(self.lbl_source)

        self.btn_batch = QPushButton("批量处理 · 多选文件 → 每个出报告图")
        self.btn_batch.setFixedHeight(34)
        self.btn_batch.setToolTip(
            "导入时可多选文件，沿用当前界面(或已导入Recipe)的列映射/单位/旋转/滤波设置逐个处理。\n"
            "每个文件输出一张含主页面全部信息(指标+四视图)的报告图 result_<原文件名>.png，\n"
            "并汇总生成 result_batch_summary.csv。要求所有文件为同一设备、同样列格式。\n"
            "建议先载入其中一个文件调好参数(或导入Recipe)，再点此批量处理。")
        self.btn_batch.clicked.connect(self.batch_process)
        ll.addWidget(self.btn_batch)

        # ---------- 2. 列映射与单位 ----------
        ll.addWidget(self._step_header(2, "列映射与单位"))
        map_group = QGroupBox()
        map_group.setFlat(True)
        ml = QGridLayout(map_group)
        ml.setContentsMargins(2, 2, 2, 2)
        ml.setHorizontalSpacing(8)
        ml.setVerticalSpacing(7)
        ml.setColumnStretch(1, 1)   # 列名下拉自适应拉伸；标签/单位列贴紧内容
        self.cb_x_col = NoWheelComboBox(); self.cb_y_col = NoWheelComboBox(); self.cb_z_col = NoWheelComboBox()
        self.cb_x_unit = NoWheelComboBox(); self.cb_x_unit.addItems(["mm", "µm"])
        self.cb_y_unit = NoWheelComboBox(); self.cb_y_unit.addItems(["mm", "µm"])
        self.cb_z_unit = NoWheelComboBox(); self.cb_z_unit.addItems(["mm", "µm", "nm"])
        for cb in (self.cb_x_col, self.cb_y_col, self.cb_z_col):
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for cb in (self.cb_x_unit, self.cb_y_unit, self.cb_z_unit):
            cb.setFixedWidth(78)
        rlab = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        rows = [("X 列", self.cb_x_col, self.cb_x_unit),
                ("Y 列", self.cb_y_col, self.cb_y_unit),
                ("Z 列", self.cb_z_col, self.cb_z_unit)]
        for r, (name, col_cb, unit_cb) in enumerate(rows):
            ml.addWidget(QLabel(name), r, 0, rlab)
            ml.addWidget(col_cb, r, 1)
            ml.addWidget(QLabel("单位"), r, 2, rlab)
            ml.addWidget(unit_cb, r, 3)
        self.cb_z_unit.setCurrentText("µm")
        self.btn_apply_map = QPushButton("应用映射并解析数据")
        self.btn_apply_map.setObjectName("accentSoftBtn")
        self.btn_apply_map.setFixedHeight(33)
        self.btn_apply_map.clicked.connect(self.apply_mapping)
        ml.addWidget(self.btn_apply_map, 3, 0, 1, 4)
        ll.addWidget(map_group)

        # ---------- 3. 姿态变换 ----------
        ll.addWidget(self._step_header(3, "姿态变换", hint="点击叠加"))
        trans_group = QGroupBox()
        trans_group.setFlat(True)
        tl = QVBoxLayout(trans_group)
        tl.setContentsMargins(2, 2, 2, 2)
        grid_trans = QGridLayout()
        grid_trans.setSpacing(6)
        # (图标, 文字, 槽, 是否高亮, tooltip) —— 4列磁贴
        tiles = [
            ("↻", "顺时针90°", self.add_cw90, False, "物料顺时针旋转90°：顶部点转到右侧"),
            ("↺", "逆时针90°", self.add_ccw90, False, "物料逆时针旋转90°：顶部点转到左侧"),
            ("⟳", "旋转180°", self.add_rot180, False, "物料旋转180°"),
            ("⇄", "X-Y对调", self.add_swap, False, "X、Y 轴对调"),
            ("↕", "前后翻转", self.add_flipx, False, "X轴翻转(前后) = Y 镜像"),
            ("↔", "左右翻转", self.add_flipy, False, "Y轴翻转(左右) = X 镜像"),
            ("⊕", "平移归零", self.add_origin, True, "平移归零(0,0)：X,Y 包围盒原点对齐"),
            ("↶", "撤销", self.undo_transform, False, "撤销上一步姿态变换"),
        ]
        for i, (icon, name, func, active, tip) in enumerate(tiles):
            grid_trans.addWidget(self._pose_tile(icon, name, func, active, tip), i // 4, i % 4)

        tl.addLayout(grid_trans)
        self.lbl_pipeline = QLabel("变换路径: 原始状态")
        self.lbl_pipeline.setObjectName("pipelineNote")
        self.lbl_pipeline.setWordWrap(True)
        tl.addWidget(self.lbl_pipeline)
        ll.addWidget(trans_group)

        # ---------- 4. 异常点滤波 ----------
        ll.addWidget(self._step_header(4, "异常点滤波"))
        flt_group = QGroupBox()
        flt_group.setFlat(True)
        fl = QGridLayout(flt_group)
        fl.setContentsMargins(2, 2, 2, 2)
        fl.addWidget(QLabel("模式:"), 0, 0)
        self.cb_filter = NoWheelComboBox()
        self.cb_filter.addItems(["关闭", "MAD 全局鲁棒滤波", "局部中位数滤波 (邻域比较)",
                                 "迭代σ裁剪 (残差±Nσ重拟合)"])
        self.cb_filter.setToolTip(
            "MAD全局: 对拟合残差做鲁棒3.5σ判定，适合零散毛刺。\n"
            "局部中位数: 每个点与其 k 个最近邻的残差中位数比较，偏离超过阈值判为异常。\n"
            "  同时启用全局残差兜底：点相对全局残差中位数超过同一阈值也会被剔除。\n"
            "  适合已知面型上限的场景（如已知面型≤5µm 则阈值设5）。\n"
            "  单个离群点不会误杀周围正常点；成簇/边缘大离群点由全局兜底拦截。\n"
            "迭代σ裁剪: 反复用最佳拟合平面残差的标准差σ裁掉 |残差-均值|>Nσ 的点并重拟合，\n"
            "  直到残差std收敛或无新增剔除(单调裁剪，只剔不回收)。\n"
            "  适合“基本是平面+少量毛刺”的工件；面有真实弧度时会削减PV，弧形面请优先用局部中位数。")
        fl.addWidget(self.cb_filter, 0, 1, 1, 3)
        self.lbl_k = QLabel("邻居数 k:")
        fl.addWidget(self.lbl_k, 1, 0)
        self.spin_k = NoWheelSpinBox(); self.spin_k.setRange(3, 200); self.spin_k.setValue(12)
        self.spin_k.setToolTip("邻域比较的最近邻数量，范围 3~200；常用 8~20，点云很密或坏点成片时可适当调大")
        fl.addWidget(self.spin_k, 1, 1)
        self.lbl_thresh = QLabel("阈值 (µm):")
        fl.addWidget(self.lbl_thresh, 1, 2)
        self.spin_thresh = NoWheelDoubleSpinBox()
        self.spin_thresh.setDecimals(2); self.spin_thresh.setRange(0.01, 10000.0)
        self.spin_thresh.setValue(5.00); self.spin_thresh.setSingleStep(0.5)
        self.spin_thresh.setToolTip("局部中位数模式的判异阈值，同时作为全局残差兜底阈值；建议设为已知面型/噪声上限")
        fl.addWidget(self.spin_thresh, 1, 3)
        self.lbl_sigma = QLabel("σ倍数 N:")
        fl.addWidget(self.lbl_sigma, 2, 0)
        self.spin_sigma = NoWheelDoubleSpinBox()
        self.spin_sigma.setDecimals(1); self.spin_sigma.setRange(1.0, 6.0)
        self.spin_sigma.setValue(3.0); self.spin_sigma.setSingleStep(0.5)
        self.spin_sigma.setToolTip("迭代σ裁剪的σ倍数，常用 3.0；越小裁剪越激进")
        fl.addWidget(self.spin_sigma, 2, 1)
        self.lbl_sigma_iter = QLabel("迭代上限:")
        fl.addWidget(self.lbl_sigma_iter, 2, 2)
        self.spin_sigma_iter = NoWheelSpinBox()
        self.spin_sigma_iter.setRange(1, 20); self.spin_sigma_iter.setValue(5)
        self.spin_sigma_iter.setToolTip("迭代σ裁剪的最大轮数，达到收敛会提前停止；常用 3~8")
        fl.addWidget(self.spin_sigma_iter, 2, 3)
        self.lbl_filter_info = QLabel("滤波剔除: 0 点 | 手动删除: 0 点")
        self.lbl_filter_info.setObjectName("mutedNote")
        fl.addWidget(self.lbl_filter_info, 3, 0, 1, 4)
        self.cb_filter.currentIndexChanged.connect(self._on_filter_mode_changed)
        self.spin_k.valueChanged.connect(self._on_filter_param_changed)
        self.spin_thresh.valueChanged.connect(self._on_filter_param_changed)
        self.spin_sigma.valueChanged.connect(self._on_filter_param_changed)
        self.spin_sigma_iter.valueChanged.connect(self._on_filter_param_changed)
        self._sync_filter_enabled()
        ll.addWidget(flt_group)

        # ---------- 5. ROI 区域 ----------
        ll.addWidget(self._step_header(5, "ROI 区域", hint="XY保留区域"))
        roi_group = QGroupBox()
        roi_group.setFlat(True)
        rg = QGridLayout(roi_group)
        rg.setContentsMargins(2, 2, 2, 2)
        rg.setHorizontalSpacing(8)
        rg.setVerticalSpacing(7)

        self.chk_roi_enable = QCheckBox("启用 ROI 分析")
        self.chk_roi_enable.setToolTip("开启后，仅分析启用 ROI 内的点；多个 ROI 按并集合并。ROI 位于当前物料坐标 X/Y(mm)。")
        self.chk_roi_enable.stateChanged.connect(self._on_roi_changed)
        rg.addWidget(self.chk_roi_enable, 0, 0, 1, 2)
        rg.setColumnStretch(1, 1)

        self.cb_roi_shape = NoWheelComboBox()
        self.cb_roi_shape.addItems(["矩形 ROI", "圆形 ROI", "智能抓面"])
        self.cb_roi_shape.currentIndexChanged.connect(self._sync_roi_input_state)
        rg.addWidget(QLabel("形状:"), 1, 0)
        rg.addWidget(self.cb_roi_shape, 1, 1)

        self.btn_roi_mouse = QPushButton("开始框选 ROI")
        self.btn_roi_mouse.setCheckable(True)
        self.btn_roi_mouse.setObjectName("accentSoftBtn")
        self.btn_roi_mouse.clicked.connect(self.start_mouse_roi)
        rg.addWidget(self.btn_roi_mouse, 2, 0, 1, 2)

        self.cb_roi_select = NoWheelComboBox()
        self.cb_roi_select.setMinimumContentsLength(12)
        self.cb_roi_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        rg.addWidget(QLabel("当前ROI:"), 3, 0)
        rg.addWidget(self.cb_roi_select, 3, 1)

        roi_manage_row = QHBoxLayout()
        roi_manage_row.setSpacing(8)
        self.btn_roi_delete = QPushButton("删除当前")
        self.btn_roi_delete.setObjectName("dangerBtn")
        self.btn_roi_delete.clicked.connect(self.delete_selected_roi)
        self.btn_roi_clear = QPushButton("清空全部")
        self.btn_roi_clear.clicked.connect(self.clear_rois)
        roi_manage_row.addWidget(self.btn_roi_delete)
        roi_manage_row.addWidget(self.btn_roi_clear)
        rg.addLayout(roi_manage_row, 4, 0, 1, 2)

        self.lbl_roi_info = QLabel("ROI: 关闭 | 未定义")
        self.lbl_roi_info.setObjectName("mutedNote")
        self.lbl_roi_info.setWordWrap(True)
        rg.addWidget(self.lbl_roi_info, 5, 0, 1, 2)

        self.chk_roi_advanced = QCheckBox("高级 / 精确输入")
        self.chk_roi_advanced.setToolTip("展开后可通过中心坐标、宽高或半径精确创建 ROI。")
        self.chk_roi_advanced.stateChanged.connect(self._sync_roi_input_state)
        rg.addWidget(self.chk_roi_advanced, 6, 0, 1, 2)

        self.roi_advanced_widget = QWidget()
        adv = QGridLayout(self.roi_advanced_widget)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setHorizontalSpacing(6)
        adv.setVerticalSpacing(7)
        adv.setColumnMinimumWidth(0, 58)
        adv.setColumnStretch(0, 0)
        adv.setColumnStretch(1, 1)

        self.spin_roi_cx = NoWheelDoubleSpinBox(); self.spin_roi_cy = NoWheelDoubleSpinBox()
        self.spin_roi_w = NoWheelDoubleSpinBox(); self.spin_roi_h = NoWheelDoubleSpinBox()
        self.spin_roi_r = NoWheelDoubleSpinBox()
        self.cb_smart_mode = NoWheelComboBox()
        self.spin_smart_tol = NoWheelDoubleSpinBox()
        self.spin_smart_dilate = NoWheelSpinBox()
        self.spin_smart_erode = NoWheelSpinBox()
        for sp in (self.spin_roi_cx, self.spin_roi_cy):
            sp.setDecimals(4); sp.setRange(-1e9, 1e9); sp.setSingleStep(0.1)
        for sp in (self.spin_roi_w, self.spin_roi_h, self.spin_roi_r):
            sp.setDecimals(4); sp.setRange(0.0001, 1e9); sp.setSingleStep(0.1); sp.setValue(1.0)
        self.spin_smart_tol.setDecimals(4)
        self.spin_smart_tol.setRange(0.0001, 1000.0)
        self.spin_smart_tol.setSingleStep(0.01)
        self.spin_smart_tol.setValue(0.02)
        self.spin_smart_tol.setToolTip("智能抓面容差，单位 mm。导入文件后会按当前 Z 分布给出推荐值。")
        self.cb_smart_mode.addItem("同平面抓取", "plane_residual")
        self.cb_smart_mode.addItem("连通抓取", "connected")
        self.cb_smart_mode.setToolTip("同平面抓取按种子附近局部平面残差筛选，不做补洞；连通抓取按 XY 邻接和高度连续扩展。")
        for sp in (self.spin_smart_dilate, self.spin_smart_erode):
            sp.setRange(0, 20)
            sp.setValue(0)
            sp.setEnabled(False)
            sp.setVisible(False)
            sp.setToolTip("V3.9.2 起不允许自动补洞/连缝，避免跨缝抓错面。")
        self.lbl_roi_cx = QLabel("中心X:")
        self.lbl_roi_cy = QLabel("中心Y:")
        adv.addWidget(self.lbl_roi_cx, 0, 0); adv.addWidget(self.spin_roi_cx, 0, 1)
        adv.addWidget(self.lbl_roi_cy, 1, 0); adv.addWidget(self.spin_roi_cy, 1, 1)
        self.lbl_roi_w = QLabel("宽度:")
        self.lbl_roi_h = QLabel("高度:")
        self.lbl_roi_r = QLabel("半径:")
        self.lbl_smart_mode = QLabel("抓面模式:")
        self.lbl_smart_tol = QLabel("抓面容差:")
        self.lbl_smart_dilate = QLabel("抓面膨胀:")
        self.lbl_smart_erode = QLabel("抓面收缩:")
        self.lbl_smart_tol_hint = QLabel("推荐: --")
        self.lbl_smart_tol_hint.setObjectName("mutedNote")
        self.lbl_smart_dilate.setVisible(False)
        self.lbl_smart_erode.setVisible(False)
        for lab in (self.lbl_roi_cx, self.lbl_roi_cy, self.lbl_roi_w, self.lbl_roi_h, self.lbl_roi_r,
                    self.lbl_smart_mode, self.lbl_smart_tol):
            lab.setFixedWidth(58)
        adv.addWidget(self.lbl_roi_w, 2, 0); adv.addWidget(self.spin_roi_w, 2, 1)
        adv.addWidget(self.lbl_roi_h, 3, 0); adv.addWidget(self.spin_roi_h, 3, 1)
        adv.addWidget(self.lbl_roi_r, 4, 0); adv.addWidget(self.spin_roi_r, 4, 1)
        adv.addWidget(self.lbl_smart_mode, 5, 0); adv.addWidget(self.cb_smart_mode, 5, 1)
        adv.addWidget(self.lbl_smart_tol, 6, 0); adv.addWidget(self.spin_smart_tol, 6, 1)
        adv.addWidget(self.lbl_smart_tol_hint, 7, 0, 1, 2)
        self.btn_roi_add_input = QPushButton("添加输入ROI")
        self.btn_roi_add_input.setObjectName("accentSoftBtn")
        self.btn_roi_add_input.clicked.connect(self.add_roi_from_inputs)
        adv.addWidget(self.btn_roi_add_input, 8, 0, 1, 2)
        rg.addWidget(self.roi_advanced_widget, 7, 0, 1, 2)
        ll.addWidget(roi_group)
        self._sync_roi_input_state()
        self._refresh_roi_ui(update=False)

        ll.addStretch()

        # ---------- 底部操作：导出报告图(主) / 导出CSV / 删除框选(危险) ----------
        self.btn_export_report = QPushButton("导出测量报告图")
        self.btn_export_report.setObjectName("accentBtn")
        self.btn_export_report.setFixedHeight(38)
        self.btn_export_report.setToolTip(
            "导出当前测量的报告图（与批量处理同款：主页面全部指标 + 四视图）。\n"
            "默认命名 Result_<导入文件名>_<时间>.png。")
        self.btn_export_report.clicked.connect(self.export_report_image)
        ll.addWidget(self.btn_export_report)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_save = QPushButton("导出 CSV")
        self.btn_save.setObjectName("accentSoftBtn")
        self.btn_save.setFixedHeight(36)
        self.btn_save.clicked.connect(self.save_file)
        self.btn_del = QPushButton("删除已框选点")
        self.btn_del.setObjectName("dangerBtn")
        self.btn_del.setFixedHeight(36)
        self.btn_del.setFixedWidth(132)
        self.btn_del.clicked.connect(self.apply_manual_deletion)
        action_row.addWidget(self.btn_save)
        action_row.addWidget(self.btn_del)
        ll.addLayout(action_row)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

    def setup_math_tab(self, parent_widget):
        scroll = QScrollArea()
        self._configure_left_scroll(scroll)
        w = QWidget()
        ll = QVBoxLayout(w)

        guide_lbl = QLabel(
            "<div style='line-height: 1.5; font-size: 12px; color: #333;'>"
            "<b>🥪 堆叠胶厚(Gap)运算流程：</b><br>"
            "公式：<span style='color:red; font-weight:bold;'>Inner Gap = 堆叠总成 - 单片1 [- 单片2]</span><br>"
            "1. 务必确保所有数据在载入后，都点击了<b>[📍 平移归零]</b>，使X,Y坐标网格原点对齐。<br>"
            "2. 依次载入不同层数据并存入下方对应的寄存器中（寄存器会显示来源文件名，请核对）。<br>"
            "3. 【对齐误差窗口】：用于补偿机台定位偏差。容差越大，匹配点越多，但过大可能匹配到错误邻居。<br>"
            "4. 右侧匹配诊断图会显示哪些堆叠点没有在单片层中找到容差内匹配点。"
            "</div>"
        )
        guide_lbl.setWordWrap(True)
        ll.addWidget(guide_lbl)

        grp_stack = QGroupBox("1️⃣ 堆叠总成数据 (Stack / 顶层)")
        gl_stack = QVBoxLayout(grp_stack)
        self.lbl_stack_status = QLabel("❌ 尚未设置"); self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_stack_status.setWordWrap(True)
        btn_set_stack = QPushButton("👆 将当前视图设为【堆叠总成】"); btn_set_stack.setFixedHeight(35)
        btn_set_stack.setObjectName("accentSoftBtn")
        btn_set_stack.clicked.connect(lambda: self.set_memory_slot('stack'))
        gl_stack.addWidget(self.lbl_stack_status); gl_stack.addWidget(btn_set_stack)
        ll.addWidget(grp_stack)

        grp_base1 = QGroupBox("2️⃣ 单片 1 数据 (Base 1 / 底层)")
        gl_base1 = QVBoxLayout(grp_base1)
        self.lbl_base1_status = QLabel("❌ 尚未设置"); self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setWordWrap(True)
        btn_set_base1 = QPushButton("👇 将当前视图设为【单片 1】"); btn_set_base1.setFixedHeight(35)
        btn_set_base1.setObjectName("accentSoftBtn")
        btn_set_base1.clicked.connect(lambda: self.set_memory_slot('base1'))
        gl_base1.addWidget(self.lbl_base1_status); gl_base1.addWidget(btn_set_base1)
        ll.addWidget(grp_base1)

        grp_base2 = QGroupBox("3️⃣ 单片 2 数据 (Base 2 / 夹层) [选填]")
        gl_base2 = QVBoxLayout(grp_base2)
        self.lbl_base2_status = QLabel("⭕ 可选空置"); self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self.lbl_base2_status.setWordWrap(True)
        hl_b2 = QHBoxLayout()
        btn_set_base2 = QPushButton("📥 设为【单片 2】"); btn_set_base2.setFixedHeight(35); btn_set_base2.clicked.connect(lambda: self.set_memory_slot('base2'))
        btn_clear_base2 = QPushButton("✖ 清除"); btn_clear_base2.setFixedHeight(35)
        btn_clear_base2.clicked.connect(lambda: self.clear_memory_slot('base2'))
        hl_b2.addWidget(btn_set_base2); hl_b2.addWidget(btn_clear_base2)
        gl_base2.addWidget(self.lbl_base2_status); gl_base2.addLayout(hl_b2)
        ll.addWidget(grp_base2)

        btn_clear_all = QPushButton("🧹 清空全部寄存器")
        btn_clear_all.setFixedHeight(35)
        btn_clear_all.clicked.connect(self.clear_all_memory_slots)
        ll.addWidget(btn_clear_all)

        ll.addStretch()

        hl_tol = QHBoxLayout()
        lbl_tol = QLabel("⚙️ 坐标对齐误差窗口 (mm):")
        lbl_tol.setStyleSheet("font-weight: bold; color: #2c3e50;")
        hl_tol.addWidget(lbl_tol)

        self.spin_tol = NoWheelDoubleSpinBox()
        self.spin_tol.setDecimals(3)
        self.spin_tol.setRange(0.001, 10.000)
        self.spin_tol.setSingleStep(0.01)
        self.spin_tol.setValue(0.050)
        self.spin_tol.setFixedHeight(35)
        hl_tol.addWidget(self.spin_tol)
        ll.addLayout(hl_tol)

        action_row = QHBoxLayout()
        self.btn_calc_gap = QPushButton("📐 容差匹配点云并计算胶厚")
        self.btn_calc_gap.setObjectName("accentBtn")
        self.btn_calc_gap.setFixedHeight(60)
        self.btn_calc_gap.clicked.connect(self.calculate_gap)
        action_row.addWidget(self.btn_calc_gap, 1)
        ll.addLayout(action_row)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

    def _on_tab_changed(self, index):
        if hasattr(self, 'right_stack'):
            if index == getattr(self, 'parallel_tab_index', -1):
                self.right_stack.setCurrentIndex(1)
            elif index == getattr(self, 'math_tab_index', -1):
                self.right_stack.setCurrentIndex(2)
            else:
                self.right_stack.setCurrentIndex(0)

    def setup_parallel_tab(self, parent_widget):
        scroll = QScrollArea()
        self._configure_left_scroll(scroll)
        w = QWidget()
        ll = QVBoxLayout(w)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(10)

        title = QLabel("平行度分析")
        title.setObjectName("stepTitle")
        ll.addWidget(title)
        note = QLabel("从主页面已处理的当前数据写入，保留单位、旋转/翻转、滤波和手动删点结果。")
        note.setObjectName("mutedNote")
        note.setWordWrap(True)
        ll.addWidget(note)

        ll.addWidget(self._step_header(1, "基准面", active=True))
        grp_ref = QGroupBox()
        grp_ref.setFlat(True)
        gl_ref = QVBoxLayout(grp_ref)
        gl_ref.setContentsMargins(2, 2, 2, 2)
        gl_ref.setSpacing(6)
        self.lbl_parallel_base_status = QLabel("尚未设置")
        self.lbl_parallel_base_status.setObjectName("sourceNote")
        self.lbl_parallel_base_status.setWordWrap(True)
        btn_ref = QPushButton("设当前数据为基准面")
        btn_ref.setObjectName("accentSoftBtn")
        btn_ref.setFixedHeight(35)
        btn_ref.clicked.connect(lambda: self.set_parallel_surface('base'))
        gl_ref.addWidget(self.lbl_parallel_base_status)
        gl_ref.addWidget(btn_ref)
        ll.addWidget(grp_ref)

        ll.addWidget(self._step_header(2, "测量面"))
        grp_meas = QGroupBox()
        grp_meas.setFlat(True)
        gl_meas = QVBoxLayout(grp_meas)
        gl_meas.setContentsMargins(2, 2, 2, 2)
        gl_meas.setSpacing(6)
        self.lbl_parallel_measure_status = QLabel("尚未设置")
        self.lbl_parallel_measure_status.setObjectName("sourceNote")
        self.lbl_parallel_measure_status.setWordWrap(True)
        btn_meas = QPushButton("设当前数据为测量面")
        btn_meas.setObjectName("accentSoftBtn")
        btn_meas.setFixedHeight(35)
        btn_meas.clicked.connect(lambda: self.set_parallel_surface('measure'))
        gl_meas.addWidget(self.lbl_parallel_measure_status)
        gl_meas.addWidget(btn_meas)
        ll.addWidget(grp_meas)

        ll.addWidget(self._step_header(3, "计算与导出"))
        ops = QHBoxLayout()
        ops.setSpacing(8)
        btn_swap = QPushButton("交换")
        btn_swap.setFixedHeight(34)
        btn_swap.clicked.connect(self.swap_parallel_surfaces)
        btn_clear = QPushButton("清空")
        btn_clear.setFixedHeight(34)
        btn_clear.clicked.connect(self.clear_parallel_surfaces)
        ops.addWidget(btn_swap)
        ops.addWidget(btn_clear)
        ll.addLayout(ops)

        btn_calc = QPushButton("计算平行度")
        btn_calc.setObjectName("accentBtn")
        btn_calc.setFixedHeight(40)
        btn_calc.clicked.connect(self.calculate_parallelism)
        ll.addWidget(btn_calc)

        rule_box = QFrame()
        rule_box.setObjectName("pipelineNote")
        gr = QVBoxLayout(rule_box)
        gr.setContentsMargins(8, 6, 8, 6)
        gr.setSpacing(4)
        for text in (
            "不做对应点相减；两个文件可为空间上不重叠的区域。",
            "分别拟合 Z = aX + bY + c，再计算 ΔRx / ΔRy = 测量面 - 基准面。",
            "台阶高度差按 VR 口径：在两面质心中点处分别代入拟合平面求 Z 后相减。",
            "大文件抽样沿用顶部“大文件策略”；平行度页不单独改变导入方式。"):
            lab = QLabel(text)
            lab.setObjectName("mutedNote")
            lab.setWordWrap(True)
            gr.addWidget(lab)
        ll.addWidget(rule_box)

        exp = QHBoxLayout()
        exp.setSpacing(8)
        btn_export = QPushButton("导出CSV")
        btn_export.setFixedHeight(34)
        btn_export.clicked.connect(self.export_parallel_csv)
        btn_report = QPushButton("导出报告图")
        btn_report.setFixedHeight(34)
        btn_report.clicked.connect(self.export_parallel_report)
        exp.addWidget(btn_export)
        exp.addWidget(btn_report)
        ll.addLayout(exp)

        btn_copy = QPushButton("复制结果")
        btn_copy.setFixedHeight(34)
        btn_copy.clicked.connect(self.copy_parallel_result)
        ll.addWidget(btn_copy)

        warn = QLabel("提示：基准面和测量面需使用同一物料坐标方向；若 X/Y 单位为 µm，需先在主页面选对单位。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #9a3412; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; padding: 10px;")
        ll.addWidget(warn)
        ll.addStretch()

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

    def _make_value_card(self, title, label, accent=False):
        return self._make_metric_card(title, label, accent=accent)

    def _build_gap_right_panel(self):
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        strip = QFrame()
        strip.setObjectName("resultsStrip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(10, 4, 10, 8)
        sl.setSpacing(12)
        self.lbl_gap_matched = QLabel("--")
        self.lbl_gap_unmatched = QLabel("--")
        self.lbl_gap_tolerance = QLabel("--")
        self.lbl_gap_state = QLabel("待计算")
        sl.addWidget(self._make_value_card("成功匹配点", self.lbl_gap_matched, accent=True), 1)
        sl.addWidget(self._make_value_card("未参与扣减点", self.lbl_gap_unmatched, accent=True), 1)
        sl.addWidget(self._make_value_card("容差窗口 (mm)", self.lbl_gap_tolerance), 1)
        sl.addWidget(self._make_value_card("状态", self.lbl_gap_state), 1)
        outer.addWidget(strip)

        card = QFrame()
        card.setObjectName("plotCard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        head = QLabel("多层扣减匹配诊断")
        head.setObjectName("plotTitle")
        hint = QLabel("以堆叠总成 XY 点为基准，显示哪些点成功匹配、哪些点没有在单片层中找到容差内最近点。")
        hint.setObjectName("mutedNote")
        hint.setWordWrap(True)
        cv.addWidget(head)
        cv.addWidget(hint)
        self.gap_match_canvas = GapMatchCanvas(self)
        cv.addWidget(self.gap_match_canvas, 1)
        self._add_shadow(card, blur=20, dy=3, alpha=30)
        outer.addWidget(card, 1)
        return panel

    def _build_parallel_right_panel(self):
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        strip = QFrame()
        strip.setObjectName("resultsStrip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(10, 4, 10, 8)
        sl.setSpacing(12)
        self.lbl_par_drx = QLabel("--")
        self.lbl_par_dry = QLabel("--")
        self.lbl_par_angle = QLabel("--")
        self.lbl_par_step = QLabel("--")
        self.lbl_par_state = QLabel("待计算")
        sl.addWidget(self._make_value_card("平行度 ΔRx (µrad)", self.lbl_par_drx, accent=True), 1)
        sl.addWidget(self._make_value_card("平行度 ΔRy (µrad)", self.lbl_par_dry, accent=True), 1)
        sl.addWidget(self._make_value_card("合成夹角 (µrad)", self.lbl_par_angle), 1)
        sl.addWidget(self._make_value_card("台阶高度差 (mm)", self.lbl_par_step), 1)
        sl.addWidget(self._make_value_card("状态", self.lbl_par_state), 1)
        outer.addWidget(strip)

        self.parallel_canvas = ParallelismCanvas(self)
        canvas_card = QFrame()
        canvas_card.setObjectName("plotCard")
        cv = QVBoxLayout(canvas_card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        head = QLabel("静态 3D 预览")
        head.setObjectName("plotTitle")
        hint = QLabel("基准面和测量面分成两个 3D 图显示，点云按 Z 高度着色，并叠加半透明拟合面。")
        hint.setObjectName("mutedNote")
        hint.setWordWrap(True)
        cv.addWidget(head)
        cv.addWidget(hint)
        cv.addWidget(self.parallel_canvas, 1)
        self._add_shadow(canvas_card, blur=20, dy=3, alpha=30)
        outer.addWidget(canvas_card, 2)

        result_card = QFrame()
        result_card.setObjectName("plotCard")
        rl = QVBoxLayout(result_card)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(9)
        title = QLabel("平行度结果")
        title.setObjectName("plotTitle")
        rl.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        headers = ["指标", "基准面", "测量面", "差值"]
        for col, text in enumerate(headers):
            lab = QLabel(text)
            lab.setObjectName("mutedNote")
            grid.addWidget(lab, 0, col)
        self.parallel_result_labels = {}
        rows = [
            ('rx', "Rx (µrad)", True),
            ('ry', "Ry (µrad)", True),
            ('rms', "RMS (µm)", False),
            ('pv', "PV 法向 (µm)", False),
            ('ttv', "TTV Z极差 (µm)", False),
            ('mean_z', "平均 Z (mm)", False),
        ]
        for r, (key, name, has_delta) in enumerate(rows, start=1):
            grid.addWidget(QLabel(name), r, 0)
            base = QLabel("--")
            meas = QLabel("--")
            delta = QLabel("--")
            if has_delta:
                delta.setObjectName("metricTitleAccent")
            grid.addWidget(base, r, 1)
            grid.addWidget(meas, r, 2)
            grid.addWidget(delta, r, 3)
            self.parallel_result_labels[key] = (base, meas, delta)
        rl.addLayout(grid)

        self.lbl_par_eq_base = QLabel("基准面: --")
        self.lbl_par_eq_measure = QLabel("测量面: --")
        self.lbl_par_eq_note = QLabel("ΔRx = Rx测量 - Rx基准；ΔRy = Ry测量 - Ry基准")
        for lab in (self.lbl_par_eq_base, self.lbl_par_eq_measure, self.lbl_par_eq_note):
            lab.setObjectName("mutedNote")
            lab.setWordWrap(True)
            rl.addWidget(lab)
        self._add_shadow(result_card, blur=20, dy=3, alpha=30)
        outer.addWidget(result_card, 1)
        return panel

    def undo_transform(self):
        if self.transform_pipeline:
            action = self.transform_pipeline.pop()
            self.update_analysis()
            self.statusBar().showMessage(f"已撤销操作: {action}", 3000)
        else:
            QMessageBox.information(self, "提示", "已经退回原始状态，没有可以撤销的操作了。")

    def add_cw90(self): self.transform_pipeline.append("CW90"); self.update_analysis()

    def add_ccw90(self): self.transform_pipeline.append("CCW90"); self.update_analysis()

    def add_rot180(self): self.transform_pipeline.append("ROT180"); self.update_analysis()

    def add_swap(self): self.transform_pipeline.append("SWAP"); self.update_analysis()

    def add_flipx(self): self.transform_pipeline.append("FLIPX"); self.update_analysis()

    def add_flipy(self): self.transform_pipeline.append("FLIPY"); self.update_analysis()

    def add_origin(self): self.transform_pipeline.append("ORIGIN(0,0)"); self.update_analysis()

    def reset_all(self):
        if self.df_raw is None: return
        self.transform_pipeline = []
        self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
        self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
        self.current_coeffs = None
        self.cb_filter.blockSignals(True)
        self.cb_filter.setCurrentIndex(0)
        self.cb_filter.blockSignals(False)
        self.chk_detrend_display.blockSignals(True)
        self.chk_detrend_display.setChecked(False)
        self.chk_detrend_display.blockSignals(False)
        self.display_detrended = False
        self.lbl_detrend_info.setText("原始Z高度 mm")
        self.clear_rois(update=False)
        self.update_analysis()
        self.statusBar().showMessage("系统已完全重置（滤波已关闭，去倾斜显示已关闭）", 3000)

    def get_final_transformed_data(self, df):
        """对当前 df 应用 transform_pipeline，结果带缓存。"""
        key = (self._df_version, tuple(self.transform_pipeline))
        if self._trans_cache_key == key:
            return self._trans_cache_data

        x, y, z = self._apply_transform_pipeline(
            df['X'].values, df['Y'].values, df['Z'].values, self.transform_pipeline)

        self._trans_cache_key = key
        self._trans_cache_data = (x, y, z)
        return x, y, z

    def _update_pipeline_label(self):
        action_names = {
            "CW90": "物料顺时针90°",
            "CCW90": "物料逆时针90°",
            "ROT180": "物料旋转180°",
            "SWAP": "X-Y轴对调",
            "FLIPX": "X轴翻转(前后)",
            "FLIPY": "Y轴翻转(左右)",
            "ORIGIN(0,0)": "平移归零(0,0)",
        }
        t = "原始状态"
        if self.transform_pipeline:
            t += " -> " + " -> ".join(action_names.get(a, a) for a in self.transform_pipeline)
        self.lbl_pipeline.setText(f"变换路径: {t}")

    def _on_filter_param_changed(self):
        # 局部中位数(2) / 迭代σ裁剪(3) 模式下参数变化才需要重算
        if self.cb_filter.currentIndex() in (2, 3):
            self.update_analysis()

    def _sync_filter_enabled(self):
        """按当前滤波模式启用/禁用对应参数控件，避免误以为某参数对当前模式生效。"""
        m = self.cb_filter.currentIndex()
        local_on = (m == 2)
        sigma_on = (m == 3)
        for w in (self.lbl_k, self.spin_k, self.lbl_thresh, self.spin_thresh):
            w.setEnabled(local_on)
        for w in (self.lbl_sigma, self.spin_sigma, self.lbl_sigma_iter, self.spin_sigma_iter):
            w.setEnabled(sigma_on)

    def _on_filter_mode_changed(self):
        self._sync_filter_enabled()
        self.update_analysis()

    def _on_detrend_display_changed(self):
        self.display_detrended = self.chk_detrend_display.isChecked()
        if self.display_detrended:
            self.lbl_detrend_info.setText("去倾斜残差 µm")
        else:
            self.lbl_detrend_info.setText("原始Z高度 mm")
        self.update_plots_only()

    def _get_plot_z(self, tx, ty, tz):
        """返回绘图/框选使用的Z值和轴标签。
        原始模式：Z使用内部单位mm；
        去倾斜模式：Z为相对当前最佳拟合平面的残差，单位µm。
        注意：该函数只服务显示/框选，不改变原始数据和拟合指标。"""
        if self.display_detrended and self.current_coeffs is not None:
            c = self.current_coeffs
            plot_z = (tz - (c[0] * tx + c[1] * ty + c[2])) * 1000.0
            return plot_z, "Resid / 去倾斜残差 (µm)", "去倾斜残差 (µm)"
        return tz, "Z (mm)", "Z"

    def update_analysis(self):
        if self.df_raw is None: return
        try:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            self._update_pipeline_label()

            manual_deleted = int((~self.manual_mask).sum())
            idx = np.where(self.manual_mask)[0]
            self.last_roi_keep_count = None
            if len(idx) < 3:
                self.active_idx = idx
                self.n_filtered = 0
                self.last_metrics = None
                self.current_coeffs = None
                self._clear_result_labels()
                self.statusBar().showMessage("⚠ 有效点少于 3 个，无法拟合平面。请点击[♻️ 全部重置]恢复数据。", 10000)
                self.draw_plots(tx, ty, tz)
                self.setup_selectors()
                return

            if self._roi_is_active():
                roi_mask_all = self._roi_keep_mask_for_arrays(
                    tx, ty, tz, matrix_rc=self._matrix_rc_for_current_data())
                idx = idx[roi_mask_all[idx]]
                self.last_roi_keep_count = int(len(idx))
                if len(idx) < 3:
                    self.active_idx = idx
                    self.n_filtered = 0
                    self.last_metrics = None
                    self.current_coeffs = None
                    self._clear_result_labels()
                    self.lbl_filter_info.setText(
                        f"滤波剔除: 0 点 | 手动删除: {manual_deleted} 点 | ROI保留: {len(idx)} 点 | 参与拟合: {len(idx)} 点")
                    self._refresh_roi_ui(update=False)
                    self.statusBar().showMessage("⚠ ROI 内有效点少于 3 个，无法拟合平面。请调整或关闭 ROI。", 10000)
                    self.draw_plots(tx, ty, tz)
                    self.setup_selectors()
                    return

            xb, yb, zb = tx[idx], ty[idx], tz[idx]

            # 1. 滤波（主界面与批量共用同一分发 filter_keep_mask）
            mode = self.cb_filter.currentIndex()
            self.n_filtered = 0
            keep = self.filter_keep_mask(
                xb, yb, zb, mode,
                k=self.spin_k.value(),
                threshold_mm=self.spin_thresh.value() * 1e-3,
                sigma_k=self.spin_sigma.value(),
                sigma_iters=self.spin_sigma_iter.value())
            if mode != 0 and keep.sum() < 3:
                self.statusBar().showMessage("⚠ 滤波后点数不足 3 个，已自动退回未滤波状态。请调整参数。", 10000)
                self.active_idx = idx
            else:
                self.active_idx = idx[keep]
                self.n_filtered = int(len(idx) - keep.sum())

            info_parts = [f"滤波剔除: {self.n_filtered} 点", f"手动删除: {manual_deleted} 点"]
            if self._roi_is_active():
                info_parts.append(f"ROI保留: {self.last_roi_keep_count} 点")
            info_parts.append(f"参与拟合: {len(self.active_idx)} 点")
            self.lbl_filter_info.setText(" | ".join(info_parts))

            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]

            # 2. 最终拟合与指标（与批量处理共用 compute_plane_metrics）
            m = self.compute_plane_metrics(fx, fy, fz)
            c = m['coeffs']
            self.current_coeffs = c
            mean_z, ttv, pv, rx, ry = m['mean_z'], m['ttv'], m['pv'], m['rx'], m['ry']

            self.last_metrics = {'a': m['a'], 'b': m['b'], 'c': m['c'],
                                  'mean_z': mean_z, 'rms': m['rms'], 'pv': pv, 'ttv': ttv,
                                  'rx': rx, 'ry': ry,
                                  'estimated': self._current_metric_quality()['estimated'],
                                  'quality_label': self._current_metric_quality()['label']}

            # UI 更新
            quality = self._current_metric_quality()
            approx = "≈" if quality['estimated'] else ""
            relation = "≈" if quality['estimated'] else "="
            self.lbl_eqn.setText(f"Z {relation} {c[0]:.4f}·X + {c[1]:.4f}·Y + {c[2]:.4f}")
            # 单位已在结果卡片标题中展示，这里只写数值，避免重复
            self.lbl_z.setText(f"{approx}{mean_z:.5f}")
            self.lbl_pv.setText(f"{approx}{pv:.3f}"); self.lbl_ttv.setText(f"{approx}{ttv:.3f}")
            self.lbl_rx.setText(f"{approx}{rx:.2f}"); self.lbl_ry.setText(f"{approx}{ry:.2f}")
            if quality['estimated']:
                self.statusBar().showMessage(f"⚠ {quality['label']}：{quality['warning']}", 12000)
            self.draw_plots(tx, ty, tz)
            self.setup_selectors()
            self._refresh_roi_ui(update=False)
        except Exception as e:
            self.statusBar().showMessage(f"⚠ 分析出错: {e}", 10000)

    def draw_plots(self, tx, ty, tz):
        roi_active = self._roi_is_active()
        xy_plot_idx = self.active_idx
        detail_plot_idx = self.active_idx
        roi_plot_idx = None
        if roi_active and self.manual_mask is not None:
            all_idx = np.where(self.manual_mask)[0]
            roi_mask_all = self._roi_keep_mask_for_arrays(
                tx, ty, tz, matrix_rc=self._matrix_rc_for_current_data())
            roi_plot_idx = all_idx[roi_mask_all[all_idx]]
            xy_plot_idx = all_idx
            detail_plot_idx = self.active_idx
        display_limit = self._display_limit()

        def sample_for_display(source_idx):
            if len(source_idx) > display_limit:
                pick = np.linspace(0, len(source_idx) - 1, display_limit, dtype=int)
                return source_idx[pick], True
            return source_idx, False

        xy_plot_idx, xy_sampled = sample_for_display(xy_plot_idx)
        detail_plot_idx, detail_sampled = sample_for_display(detail_plot_idx)
        if xy_sampled or detail_sampled:
            self.statusBar().showMessage(
                f"数据共 {len(self.active_idx):,} 点；XY显示 {len(xy_plot_idx):,} 点，3D/XZ/YZ显示 {len(detail_plot_idx):,} 点，指标仍按当前分析数据计算。", 5000)
        if roi_plot_idx is not None and len(roi_plot_idx) > display_limit:
            pick = np.linspace(0, len(roi_plot_idx) - 1, display_limit, dtype=int)
            roi_plot_idx = roi_plot_idx[pick]

        self.last_displayed_points = len(detail_plot_idx) if roi_active else len(xy_plot_idx)
        self._update_import_status_label()

        plot_z_all, z_axis_label, z_short_label = self._get_plot_z(tx, ty, tz)
        xy_x, xy_y, xy_z = tx[xy_plot_idx], ty[xy_plot_idx], plot_z_all[xy_plot_idx]
        detail_x = detail_y = detail_z = np.array([])
        if detail_plot_idx is not None and len(detail_plot_idx) > 0:
            detail_x, detail_y, detail_z = tx[detail_plot_idx], ty[detail_plot_idx], plot_z_all[detail_plot_idx]
        roi_x = roi_y = roi_z = None
        if roi_plot_idx is not None and len(roi_plot_idx) > 0:
            roi_x, roi_y, roi_z = tx[roi_plot_idx], ty[roi_plot_idx], plot_z_all[roi_plot_idx]

        axes = [self.canvas.ax3d, self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]
        lbs = [("X (mm)", "Y (mm)", z_axis_label),
               ("X (mm)", "Y (mm)"),
               ("X (mm)", z_axis_label),
               ("Y (mm)", z_axis_label)]
        for ax, lb in zip(axes, lbs):
            ax.clear(); ax.grid(True, linestyle='-', linewidth=0.7, color='#edf0f3')
            ax.set_xlabel(lb[0]); ax.set_ylabel(lb[1])
            if len(lb) > 2: ax.set_zlabel(lb[2])
        # 2D 子图：隐藏上/右边框，坐标轴改浅灰，向方案A的卡片化绘图靠拢
        for ax in (self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz):
            ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#d8dee4'); ax.spines['bottom'].set_color('#d8dee4')
            ax.tick_params(colors='#9aa4ae', labelsize=8)
        # 3D 子图：背景面改浅色、网格变淡
        a3 = self.canvas.ax3d
        for pane in (a3.xaxis.pane, a3.yaxis.pane, a3.zaxis.pane):
            pane.set_facecolor('#fbfcfd'); pane.set_edgecolor('#e6eaee'); pane.set_alpha(1.0)
        a3.tick_params(colors='#9aa4ae', labelsize=7)

        self.canvas.set_titles(self.display_detrended)

        if len(xy_x) == 0 and len(detail_x) == 0:
            self._draw_roi_overlays(self.canvas.ax_xy)
            self.canvas.ax_xy.relim()
            self.canvas.ax_xy.autoscale_view()
            self.canvas.draw()
            return

        sc_params = {'cmap': 'turbo', 's': 14, 'alpha': 0.85, 'edgecolors': 'none'}
        if len(xy_x) > 0:
            self.canvas.ax_xy.scatter(xy_x, xy_y, c=xy_z, **sc_params, zorder=2)
        self._draw_roi_overlays(self.canvas.ax_xy)
        if len(detail_x) > 0:
            self.canvas.ax3d.scatter(detail_x, detail_y, detail_z, c=detail_z, **sc_params)
            self.canvas.ax_xz.scatter(detail_x, detail_z, c=detail_z, **sc_params)
            self.canvas.ax_yz.scatter(detail_y, detail_z, c=detail_z, **sc_params)

        if roi_x is not None and len(roi_x) > 0:
            roi_params = {
                'c': '#6b7280', 's': 24, 'alpha': 0.72,
                'edgecolors': '#f8fafc', 'linewidths': 0.25, 'rasterized': True
            }
            self.canvas.ax_xy.scatter(roi_x, roi_y, zorder=4, **roi_params)

        # 3D 视图渲染参考平面：原始模式显示最佳拟合平面；去倾斜模式显示残差零平面
        if self.current_coeffs is not None:
            c = self.current_coeffs
            fit_idx = self.active_idx if len(self.active_idx) >= 3 else detail_plot_idx
            fxp, fyp = tx[fit_idx], ty[fit_idx]
            xx, yy = np.meshgrid(np.linspace(fxp.min(), fxp.max(), 10), np.linspace(fyp.min(), fyp.max(), 10))
            if self.display_detrended:
                zz = np.zeros_like(xx)
            else:
                zz = c[0] * xx + c[1] * yy + c[2]
            self.canvas.ax3d.plot_surface(xx, yy, zz, color='#3498db', alpha=0.3, edgecolor='none')

        if self.temp_selected_mask is not None and self.temp_selected_mask.sum() > 0:
            selected_idx = np.where(self.temp_selected_mask)[0]
            if len(selected_idx) > display_limit:
                pick = np.linspace(0, len(selected_idx) - 1, display_limit, dtype=int)
                selected_idx = selected_idx[pick]
            txs, tys, tzs = tx[selected_idx], ty[selected_idx], plot_z_all[selected_idx]
            for ax in [self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]:
                sx, sy = (txs, tys) if ax == self.canvas.ax_xy else (txs, tzs) if ax == self.canvas.ax_xz else (tys, tzs)
                ax.scatter(sx, sy, c='red', s=50, marker='x', linewidth=2)

        self.canvas.draw()

    def update_plots_only(self):
        if self.df_raw is None or self.active_idx is None:
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        self.draw_plots(tx, ty, tz)
