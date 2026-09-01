"""DataIOMixin extracted from the V3.9.3 application."""

import sys
import os
import re
import mmap
import json
import random
import shlex
import tempfile
import unicodedata
import copy
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
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent, QSettings, QThread
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen
from scipy.spatial import cKDTree
from ..workers import TaskCancelled, sha256_file_dialog

from ..widgets import NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox



class DataIOMixin:
    TEXT_SUFFIXES = ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz')
    EXCEL_SUFFIXES = ('.xlsx', '.xls', '.xlsm')

    @staticmethod
    def _input_layout_label(layout_mode):
        return {
            'point_table': 'XYZ物理坐标',
            'pixel_xy': 'Pixel XY / 像素XY',
            'height_matrix': 'Z Matrix / 高度矩阵',
            'zygo_xyz': 'Zygo XYZ（兼容）',
        }.get(str(layout_mode), 'XYZ物理坐标')

    @staticmethod
    def _input_layout_short_label(layout_mode):
        return {
            'point_table': 'XYZ',
            'pixel_xy': 'Pixel XY',
            'height_matrix': 'Z矩阵',
            'zygo_xyz': 'Zygo',
        }.get(str(layout_mode), 'XYZ')

    @staticmethod
    def _process_ui_events():
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is app.thread():
            app.processEvents()

    def _bigfile_mode_label(self, mode_key=None):
        mode = mode_key or getattr(self, 'large_file_mode', 'standard')
        preset = self.BIGFILE_MODE_PRESETS.get(mode)
        return preset['label'] if preset else '自定义'

    def _bigfile_mode_description(self, mode_key=None):
        mode = mode_key or getattr(self, 'large_file_mode', 'standard')
        preset = self.BIGFILE_MODE_PRESETS.get(mode)
        if preset:
            return preset['description']
        return '手动参数：当前阈值、导入上限或显示上限与三档预设不完全一致。'

    def _sample_method_label(self, method=None):
        method = method or getattr(self, 'large_file_sample_method', 'file_position')
        if method == 'spatial_grid':
            return '空间网格均匀采样'
        return '文件位置均匀采样'

    def _grid_count_label(self, grid_count=None):
        grid = int(self.large_text_grid_count if grid_count is None else grid_count)
        return '自动' if grid <= 0 else f'{grid} × {grid}'

    def _matching_bigfile_mode(self, auto_sample=None, threshold_mb=None, import_limit=None,
                               display_limit=None, sample_method=None, grid_count=None,
                               matrix_analysis_threshold=None):
        auto = bool(self.auto_sample_large_text if auto_sample is None else auto_sample)
        threshold = int(self.large_text_threshold_mb if threshold_mb is None else threshold_mb)
        rows = int(self.large_text_import_limit if import_limit is None else import_limit)
        shown = int(self.display_point_limit if display_limit is None else display_limit)
        method = str(self.large_file_sample_method if sample_method is None else sample_method)
        grid = int(self.large_text_grid_count if grid_count is None else grid_count)
        matrix_limit = int(getattr(self, 'matrix_analysis_threshold', 400_000)
                           if matrix_analysis_threshold is None else matrix_analysis_threshold)
        for key, preset in self.BIGFILE_MODE_PRESETS.items():
            if (auto == bool(preset['auto_sample'])
                    and threshold == int(preset['threshold_mb'])
                    and rows == int(preset['import_limit'])
                    and shown == int(preset['display_limit'])
                    and ('file_position' if method == 'stride' else method) == str(preset.get('sample_method', 'file_position'))
                    and grid == int(preset.get('grid_count', 0))):
                if matrix_limit != int(preset.get('matrix_analysis_threshold', preset['import_limit'])):
                    continue
                return key
        return 'custom'

    def _large_text_threshold_bytes(self):
        return int(getattr(self, 'large_text_threshold_mb', self.LARGE_TEXT_FILE_BYTES // (1024 * 1024))) * 1024 * 1024

    def _large_text_import_limit(self):
        return int(getattr(self, 'large_text_import_limit', self.LARGE_TEXT_IMPORT_LIMIT))

    def _display_limit(self):
        return int(getattr(self, 'display_point_limit', self.DISPLAY_POINT_LIMIT))

    def _ensure_source_sha256(self):
        info = getattr(self, 'import_info', {}) or {}
        cached = str(info.get('source_sha256') or '').lower()
        if len(cached) == 64:
            return cached
        source_path = str(info.get('source_path') or '')
        if not source_path or not Path(source_path).is_file():
            return ''
        try:
            value = sha256_file_dialog(self, source_path).lower()
        except TaskCancelled:
            self._show_status("源文件 SHA-256 计算已取消", 5000)
            return ''
        self.import_info['source_sha256'] = value
        self._show_status(f"源文件 SHA-256 已计算: {value[:12]}…", 5000)
        return value

    @staticmethod
    def _metric_quality_from_import(import_info=None):
        info = import_info or {}
        sampled = bool(info.get('sampled', False))
        if not sampled:
            return {
                'estimated': False,
                'extrema_preserved': True,
                'code': 'full',
                'label': '全量计算',
                'warning': '',
            }
        extrema_preserved = bool(info.get('extrema_preserved', False))
        if extrema_preserved:
            return {
                'estimated': True,
                'extrema_preserved': True,
                'code': 'grid_extrema',
                'label': '抽样估计（网格保留Z极值）',
                'warning': '结果基于空间网格抽样；TTV保留Z极值信息，PV/Rx/Ry仍是抽样估计。',
            }
        return {
            'estimated': True,
            'extrema_preserved': False,
            'code': 'sampled_estimate',
            'label': '抽样估计（极值未保留）',
            'warning': '文件位置/倍率抽样未保留全量极值，PV/TTV可能低估；该结果不可直接用于产线放行。',
        }

    def _current_metric_quality(self):
        return self._metric_quality_from_import(getattr(self, 'import_info', {}))

    def _confirm_estimated_metrics(self, purpose='继续'):
        quality = self._current_metric_quality()
        if not quality['estimated']:
            return True
        ret = QMessageBox.question(
            self,
            '抽样结果确认',
            f"当前结果质量：{quality['label']}\n\n{quality['warning']}\n\n是否仍要{purpose}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        return ret == QMessageBox.StandardButton.Yes

    def _reset_import_info(self, path=None):
        size = 0
        if path:
            try:
                size = Path(path).stat().st_size
            except Exception:
                size = 0
        self.import_info = {
            'file_size_bytes': size,
            'file_size_mb': size / (1024 * 1024) if size else 0.0,
            'source_path': str(Path(path).expanduser().resolve()) if path else '',
            'source_sha256': '',
            'strategy': '--',
            'input_layout_mode': getattr(self, 'input_layout_mode', 'point_table'),
            'input_semantics': {
                'point_table': 'xyz_physical',
                'pixel_xy': 'pixel_xy',
                'height_matrix': 'height_matrix',
                'zygo_xyz': 'pixel_xy',
            }.get(getattr(self, 'input_layout_mode', 'point_table'), 'xyz_physical'),
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'import_rows': 0,
            'source_matrix_positions': 0,
            'original_valid_points': 0,
            'analysis_points': 0,
            'display_points': 0,
            'display_limit': self._display_limit(),
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label(),
            'grid_count': self.large_text_grid_count,
            'stride_n': self.large_text_stride_n,
            'height_matrix': False,
            'source_format': '--',
            'sampling_pitch_x_um': self.height_matrix_pitch_x_um,
            'sampling_pitch_y_um': self.height_matrix_pitch_y_um,
            'sampling_pitch_source': '--',
            'detected_camera_res_um': None,
            'missing_points': 0,
            'bad_rows': 0,
            'z_source_field': '',
            'z_source_unit': '',
            'header_source_line': None,
            'header_confidence': 'generated',
            'header_source': 'generated',
            'header_auto_mapping': {},
            'header_unit_hints': {},
            'sampling_downgraded': False,
            'sampling_downgrade_reason': '',
            'matrix_pitch_x_um': self.height_matrix_pitch_x_um,
            'matrix_pitch_y_um': self.height_matrix_pitch_y_um,
            'matrix_z_unit': self.height_matrix_z_unit,
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'matrix_requested_cols': int(getattr(self, 'height_matrix_cols', 0)),
            'matrix_requested_rows': int(getattr(self, 'height_matrix_rows', 0)),
            'input_encoding_override': str(getattr(self, 'import_encoding', 'auto')),
            'input_delimiter_override': str(getattr(self, 'import_delimiter', 'auto')),
            'input_data_start_row': int(getattr(self, 'import_start_row', 0)),
            'topology_method': '',
            'topology_fallback_reason': '',
            'notes': ''
        }

    def _update_import_status_label(self):
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            return
        info = getattr(self, 'import_info', {}) or {}
        strategy = info.get('strategy', '--')
        layout_mode = info.get('input_layout_mode', getattr(self, 'input_layout_mode', 'point_table'))
        layout_text = self._input_layout_label(layout_mode)
        file_size_mb = info.get('file_size_mb', 0.0)
        import_rows = info.get('import_rows', 0)
        display_limit = self._display_limit()
        shown = self.last_displayed_points if self.last_displayed_points else min(import_rows or 0, display_limit)
        sampled_text = '抽样' if info.get('sampled') else '全量/未抽样'
        quality = self._metric_quality_from_import(info)
        notes = info.get('notes') or ''
        valid_rows = info.get('valid_rows', None)
        valid_text = f" | 有效 {int(valid_rows):,} 点" if valid_rows is not None else ""
        missing_points = int(info.get('missing_points', 0) or 0)
        bad_rows = int(info.get('bad_rows', 0) or 0)
        issue_text = ''
        if missing_points:
            issue_text += f" | 缺测 {missing_points:,} 点"
        if bad_rows:
            issue_text += f" | 坏行 {bad_rows:,}"
        header_confidence = str(info.get('header_confidence', '') or '')
        header_line = info.get('header_source_line')
        header_text = ''
        if header_confidence:
            confidence_label = {'semantic': '语义表头', 'candidate': '候选表头',
                                'generated': '生成列名'}.get(header_confidence, header_confidence)
            header_text = f" | {confidence_label}"
            if header_line:
                header_text += f"(第{int(header_line)}行)"
        if info.get('height_matrix') and info.get('matrix_rows') and info.get('matrix_cols'):
            matrix_rows = int(info.get('matrix_rows', 0))
            matrix_cols = int(info.get('matrix_cols', 0))
            original_valid = int(info.get('original_valid_points', info.get('source_valid_rows', import_rows)) or 0)
            analysis_points = int(info.get('analysis_points', import_rows) or 0)
            display_points = int(info.get('display_points', shown) or 0)
            text = (f"导入状态: Z矩阵 {matrix_rows:,}×{matrix_cols:,} | 有效 {original_valid:,} | "
                    f"分析 {analysis_points:,} | 显示 {display_points:,} | {strategy} | {sampled_text}")
        else:
            text = (f"导入状态: {layout_text} | {strategy} | {sampled_text} | 文件 {file_size_mb:.1f} MB | "
                    f"读入 {int(import_rows):,} 行{valid_text}{issue_text}{header_text} | 显示 {int(shown):,}/{int(display_limit):,} 点")
        if quality['estimated']:
            text += f" | 结果质量: {quality['label']}"
        if notes:
            text += f" | {notes}"
        if info.get('sampling_downgraded'):
            text += f" | 采样降级: {info.get('sampling_downgrade_reason', '列语义不确定')}"
        if info.get('topology_method'):
            text += f" | Topology: {info['topology_method']}"
        if info.get('topology_fallback_reason'):
            text += f" | Fallback: {info['topology_fallback_reason']}"
        if hasattr(self, 'lbl_import_status'):
            self.lbl_import_status.setText(text)
        if hasattr(self, 'btn_bigfile_settings'):
            self.btn_bigfile_settings.setText(
                f"导入策略 · {self._input_layout_short_label(layout_mode)}")
            cfg = (f"大文件策略\n"
                   f"模式: {self._bigfile_mode_label()}\n"
                   f"自动抽样: {'开启' if self.auto_sample_large_text else '关闭'}\n"
                   f"采样方式: {self._sample_method_label()}\n"
                   f"空间网格数: {self._grid_count_label()}\n"
                   f"触发阈值: {self.large_text_threshold_mb} MB\n"
                   f"导入上限: {self.large_text_import_limit:,} 行\n"
                   f"显示上限: {self.display_point_limit:,} 点\n\n{text}")
            self.btn_bigfile_settings.setToolTip(cfg)
        if text and strategy != '--':
            self._show_status(text, 5000)

    def _on_display_limit_changed(self):
        self.import_info['display_limit'] = self._display_limit()
        self._update_import_status_label()
        if self.df_raw is not None and self.active_idx is not None:
            self.update_plots_only()

    def show_bigfile_settings_dialog(self):
        """V3.5.1: 大文件导入/显示策略弹窗。
        正常界面只保留右侧工具条按钮，避免占用左侧主控区。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("文件导入 / 显示策略")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)

        layout_group = QGroupBox("文件数据布局")
        layout_grid = QGridLayout(layout_group)
        layout_grid.addWidget(QLabel("导入类型:"), 0, 0)
        cb_input_layout = NoWheelComboBox()
        cb_input_layout.addItem("XYZ 物理坐标", "point_table")
        cb_input_layout.addItem("Pixel XY / 像素XY", "pixel_xy")
        cb_input_layout.addItem("Z Matrix / 高度矩阵", "height_matrix")
        cb_input_layout.addItem("Zygo XYZ（兼容）", "zygo_xyz")
        layout_index = cb_input_layout.findData(getattr(self, 'input_layout_mode', 'point_table'))
        cb_input_layout.setCurrentIndex(layout_index if layout_index >= 0 else 0)
        cb_input_layout.setToolTip(
            "XYZ物理坐标：X/Y为真实物理坐标。\n"
            "Pixel XY：X/Y为像素序号，使用Pitch和Origin生成物理坐标。\n"
            "Z Matrix：主体为二维高度数组。\n"
            "Zygo XYZ：旧Recipe兼容入口，内部使用Pixel XY标准化管线。")
        layout_grid.addWidget(cb_input_layout, 0, 1)
        layout_note = QLabel("此选择会自动记忆，后续导入沿用；更换数据类型时再修改。")
        layout_note.setWordWrap(True)
        layout_note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout_grid.addWidget(layout_note, 1, 0, 1, 2)
        layout.addWidget(layout_group)

        group = QGroupBox("Zeiss / TXT / ASC / XYZ 大文件策略")
        grid = QGridLayout(group)

        chk_auto = QCheckBox("超大文本自动抽样")
        chk_auto.setChecked(self.auto_sample_large_text)
        chk_auto.setToolTip("开启后，超过阈值的TXT/CSV/ASC/XYZ等文本文件不会全量读入，而是按设定采样方式预抽样，避免大文件卡死。")
        grid.addWidget(chk_auto, 0, 0, 1, 2)

        grid.addWidget(QLabel("策略模式:"), 1, 0)
        cb_mode = NoWheelComboBox()
        for key in ('fast', 'standard', 'precise'):
            cb_mode.addItem(f"{self.BIGFILE_MODE_PRESETS[key]['label']}模式", key)
        cb_mode.addItem("自定义", "custom")
        current_mode = self._matching_bigfile_mode()
        mode_idx = cb_mode.findData(current_mode)
        cb_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else cb_mode.findData("custom"))
        cb_mode.setToolTip("快速更流畅，标准为默认推荐，精确保留更多点但会更慢。")
        grid.addWidget(cb_mode, 1, 1)

        mode_note = QLabel(self._bigfile_mode_description(current_mode))
        mode_note.setWordWrap(True)
        mode_note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        grid.addWidget(mode_note, 2, 0, 1, 2)

        grid.addWidget(QLabel("采样方式:"), 3, 0)
        cb_sample_method = NoWheelComboBox()
        cb_sample_method.addItem("文件位置均匀采样", "file_position")
        cb_sample_method.addItem("空间网格均匀采样", "spatial_grid")
        current_method = getattr(self, 'large_file_sample_method', 'file_position')
        if current_method == 'stride':
            current_method = 'file_position'
        sample_idx = cb_sample_method.findData(current_method)
        cb_sample_method.setCurrentIndex(sample_idx if sample_idx >= 0 else 0)
        cb_sample_method.setToolTip("文件位置采样按文件字节位置均匀抽取有效行，优先流畅；空间网格采样按 X/Y 分格，每格保留代表点、Z最小点和Z最大点。")
        grid.addWidget(cb_sample_method, 3, 1)

        grid.addWidget(QLabel("空间网格数:"), 4, 0)
        spin_grid = NoWheelSpinBox()
        spin_grid.setRange(0, 2000)
        spin_grid.setSingleStep(10)
        spin_grid.setSpecialValueText("自动")
        spin_grid.setValue(int(getattr(self, 'large_text_grid_count', 0)))
        spin_grid.setToolTip("0=自动按导入上限计算；自定义时表示 X/Y 单边网格数，例如 300 表示 300×300。每格最多保留3个点。")
        grid.addWidget(spin_grid, 4, 1)

        grid.addWidget(QLabel("触发阈值(MB):"), 5, 0)
        spin_mb = NoWheelSpinBox()
        spin_mb.setRange(1, 4096)
        spin_mb.setValue(int(self.large_text_threshold_mb))
        spin_mb.setToolTip("文件大小达到该阈值时触发预抽样导入。V3.9.2 默认64MB，优先保证流畅。")
        grid.addWidget(spin_mb, 5, 1)

        grid.addWidget(QLabel("导入上限(行):"), 6, 0)
        spin_import = NoWheelSpinBox()
        spin_import.setRange(10000, 5000000)
        spin_import.setSingleStep(50000)
        spin_import.setValue(int(self.large_text_import_limit))
        spin_import.setToolTip("超大文本预抽样最多导入的行数。注意：该上限影响后续拟合/滤波指标。")
        grid.addWidget(spin_import, 6, 1)

        grid.addWidget(QLabel("矩阵分析触发点数:"), 12, 0)
        spin_matrix_threshold = NoWheelSpinBox()
        spin_matrix_threshold.setRange(10_000, 10_000_000)
        spin_matrix_threshold.setSingleStep(50_000)
        spin_matrix_threshold.setValue(int(getattr(self, 'matrix_analysis_threshold', 400_000)))
        spin_matrix_threshold.setToolTip(
            "Z矩阵有效点数超过该值时，即使文件未达到MB阈值也会先抽样再生成XYZ。")
        grid.addWidget(spin_matrix_threshold, 12, 1)

        grid.addWidget(QLabel("显示上限(点):"), 7, 0)
        spin_display = NoWheelSpinBox()
        spin_display.setRange(5000, 1000000)
        spin_display.setSingleStep(5000)
        spin_display.setValue(int(self.display_point_limit))
        spin_display.setToolTip("仅限制右侧绘图显示点数，不改变已导入数据和Rx/Ry/PV/TTV计算。")
        grid.addWidget(spin_display, 7, 1)

        grid.addWidget(QLabel("X 采样间距 (µm/点):"), 8, 0)
        spin_pitch_x = NoWheelDoubleSpinBox()
        spin_pitch_x.setDecimals(4)
        spin_pitch_x.setRange(0.0001, 1e6)
        spin_pitch_x.setValue(float(self.height_matrix_pitch_x_um))
        spin_pitch_x.setToolTip("Z矩阵与Zygo XYZ使用该X方向点间距生成实际物料坐标；普通点表不使用。")
        grid.addWidget(spin_pitch_x, 8, 1)

        grid.addWidget(QLabel("Y 采样间距 (µm/点):"), 9, 0)
        spin_pitch_y = NoWheelDoubleSpinBox()
        spin_pitch_y.setDecimals(4)
        spin_pitch_y.setRange(0.0001, 1e6)
        spin_pitch_y.setValue(float(self.height_matrix_pitch_y_um))
        spin_pitch_y.setToolTip("Z矩阵与Zygo XYZ使用该Y方向点间距生成实际物料坐标；普通点表不使用。")
        grid.addWidget(spin_pitch_y, 9, 1)

        grid.addWidget(QLabel("矩阵Z默认单位:"), 10, 0)
        cb_matrix_z_unit = NoWheelComboBox()
        cb_matrix_z_unit.addItems(["µm", "mm"])
        cb_matrix_z_unit.setCurrentText(self.height_matrix_z_unit)
        cb_matrix_z_unit.setToolTip("高度矩阵表头未写 Z Unit 时使用；若表头写明 um/mm，会优先采用表头。")
        grid.addWidget(cb_matrix_z_unit, 10, 1)

        grid.addWidget(QLabel("矩阵数据起始行:"), 11, 0)
        spin_matrix_start = NoWheelSpinBox()
        spin_matrix_start.setRange(0, 50000)
        spin_matrix_start.setSpecialValueText("自动")
        spin_matrix_start.setValue(int(getattr(self, 'height_matrix_start_row', 0)))
        spin_matrix_start.setToolTip(
            "0=自动扫描候选数值区；识别错误时填写高度矩阵第一行在原文件中的行号（从1开始）。")
        grid.addWidget(spin_matrix_start, 11, 1)

        grid.addWidget(QLabel("矩阵列数:"), 13, 0)
        spin_matrix_cols = NoWheelSpinBox()
        spin_matrix_cols.setRange(0, 100000)
        spin_matrix_cols.setSpecialValueText("自动")
        spin_matrix_cols.setValue(int(getattr(self, 'height_matrix_cols', 0)))
        spin_matrix_cols.setToolTip("0=由表头或固定分隔符推断；格式存在歧义时可填写真实矩阵列数。")
        grid.addWidget(spin_matrix_cols, 13, 1)

        advanced_group = QGroupBox("高级解析覆盖（Auto优先使用可靠表头）")
        advanced = QGridLayout(advanced_group)
        advanced.addWidget(QLabel("数据起始行:"), 0, 0)
        spin_start_row = NoWheelSpinBox(); spin_start_row.setRange(0, 10_000_000)
        spin_start_row.setSpecialValueText("自动")
        spin_start_row.setValue(int(getattr(self, 'import_start_row', 0)))
        advanced.addWidget(spin_start_row, 0, 1)
        advanced.addWidget(QLabel("编码:"), 1, 0)
        cb_encoding = NoWheelComboBox()
        for label, value in (("Auto", "auto"), ("UTF-8-SIG", "utf-8-sig"),
                             ("GBK", "gbk"), ("UTF-16", "utf-16"),
                             ("Latin-1", "latin-1")):
            cb_encoding.addItem(label, value)
        cb_encoding.setCurrentIndex(max(0, cb_encoding.findData(
            getattr(self, 'import_encoding', 'auto'))))
        advanced.addWidget(cb_encoding, 1, 1)
        advanced.addWidget(QLabel("分隔符:"), 2, 0)
        cb_delimiter = NoWheelComboBox()
        for label, value in (("Auto", "auto"), ("逗号", ","), ("Tab", "\t"),
                             ("分号", ";"), ("中文分号", "；"),
                             ("竖线", "|"), ("空白", "whitespace")):
            cb_delimiter.addItem(label, value)
        cb_delimiter.setCurrentIndex(max(0, cb_delimiter.findData(
            getattr(self, 'import_delimiter', 'auto'))))
        advanced.addWidget(cb_delimiter, 2, 1)

        column_spins = []
        for row, (label, attr) in enumerate((('X / Pixel X列号:', 'import_x_col'),
                                             ('Y / Pixel Y列号:', 'import_y_col'),
                                             ('Z列号:', 'import_z_col')), start=3):
            advanced.addWidget(QLabel(label), row, 0)
            spin = NoWheelSpinBox(); spin.setRange(0, 100_000)
            spin.setSpecialValueText("自动")
            spin.setValue(int(getattr(self, attr, 0)))
            advanced.addWidget(spin, row, 1); column_spins.append(spin)

        unit_combos = []
        for row, (label, attr) in enumerate((('X单位:', 'import_x_unit'),
                                             ('Y单位:', 'import_y_unit'),
                                             ('Z单位:', 'import_z_unit')), start=6):
            advanced.addWidget(QLabel(label), row, 0)
            combo = NoWheelComboBox(); combo.addItems(['auto', 'mm', 'µm', 'nm'])
            combo.setCurrentText(str(getattr(self, attr, 'auto')))
            advanced.addWidget(combo, row, 1); unit_combos.append(combo)

        advanced.addWidget(QLabel("Pixel Origin X/Y:"), 9, 0)
        origin_row = QHBoxLayout()
        spin_origin_x = NoWheelDoubleSpinBox(); spin_origin_x.setRange(-1e9, 1e9)
        spin_origin_y = NoWheelDoubleSpinBox(); spin_origin_y.setRange(-1e9, 1e9)
        spin_origin_x.setValue(float(getattr(self, 'pixel_origin_x', 0.0)))
        spin_origin_y.setValue(float(getattr(self, 'pixel_origin_y', 0.0)))
        origin_row.addWidget(spin_origin_x); origin_row.addWidget(spin_origin_y)
        advanced.addLayout(origin_row, 9, 1)

        advanced.addWidget(QLabel("Pitch来源:"), 10, 0)
        cb_pitch_source = NoWheelComboBox()
        cb_pitch_source.addItem("Auto（可靠文件值优先）", "auto")
        cb_pitch_source.addItem("手动输入", "manual")
        cb_pitch_source.setCurrentIndex(max(0, cb_pitch_source.findData(
            getattr(self, 'pitch_source', 'manual'))))
        advanced.addWidget(cb_pitch_source, 10, 1)
        advanced.addWidget(QLabel("矩阵行数:"), 11, 0)
        spin_matrix_rows = NoWheelSpinBox(); spin_matrix_rows.setRange(0, 100_000)
        spin_matrix_rows.setSpecialValueText("自动")
        spin_matrix_rows.setValue(int(getattr(self, 'height_matrix_rows', 0)))
        advanced.addWidget(spin_matrix_rows, 11, 1)
        layout.addWidget(advanced_group)

        applying_preset = {'active': False}

        def set_mode_index(mode_key):
            idx = cb_mode.findData(mode_key)
            if idx >= 0 and cb_mode.currentIndex() != idx:
                cb_mode.setCurrentIndex(idx)

        def sync_mode_from_values(*_args):
            if applying_preset['active']:
                return
            mode_key = self._matching_bigfile_mode(chk_auto.isChecked(), spin_mb.value(),
                                                   spin_import.value(), spin_display.value(),
                                                   cb_sample_method.currentData(), spin_grid.value(),
                                                   spin_matrix_threshold.value())
            set_mode_index(mode_key)
            mode_note.setText(self._bigfile_mode_description(mode_key))

        def apply_preset_from_combo(*_args):
            mode_key = cb_mode.currentData()
            preset = self.BIGFILE_MODE_PRESETS.get(mode_key)
            if not preset:
                mode_note.setText(self._bigfile_mode_description('custom'))
                return
            applying_preset['active'] = True
            try:
                chk_auto.setChecked(bool(preset['auto_sample']))
                idx = cb_sample_method.findData(str(preset.get('sample_method', 'file_position')))
                cb_sample_method.setCurrentIndex(idx if idx >= 0 else 0)
                spin_grid.setValue(int(preset.get('grid_count', 0)))
                spin_mb.setValue(int(preset['threshold_mb']))
                spin_import.setValue(int(preset['import_limit']))
                spin_matrix_threshold.setValue(int(preset['matrix_analysis_threshold']))
                spin_display.setValue(int(preset['display_limit']))
            finally:
                applying_preset['active'] = False
            mode_note.setText(self._bigfile_mode_description(mode_key))

        def update_grid_enabled(*_args):
            spin_grid.setEnabled(cb_sample_method.currentData() == 'spatial_grid')
            sync_mode_from_values()

        def update_layout_controls(*_args):
            mode = str(cb_input_layout.currentData())
            uses_pitch = mode in ('pixel_xy', 'height_matrix', 'zygo_xyz')
            spin_pitch_x.setEnabled(uses_pitch)
            spin_pitch_y.setEnabled(uses_pitch)
            cb_matrix_z_unit.setEnabled(mode == 'height_matrix')
            spin_matrix_start.setEnabled(mode == 'height_matrix')
            spin_matrix_cols.setEnabled(mode == 'height_matrix')
            spin_matrix_rows.setEnabled(mode == 'height_matrix')
            spin_origin_x.setEnabled(mode == 'pixel_xy')
            spin_origin_y.setEnabled(mode == 'pixel_xy')

        cb_mode.currentIndexChanged.connect(apply_preset_from_combo)
        cb_input_layout.currentIndexChanged.connect(update_layout_controls)
        chk_auto.toggled.connect(sync_mode_from_values)
        cb_sample_method.currentIndexChanged.connect(update_grid_enabled)
        spin_grid.valueChanged.connect(sync_mode_from_values)
        spin_mb.valueChanged.connect(sync_mode_from_values)
        spin_import.valueChanged.connect(sync_mode_from_values)
        spin_matrix_threshold.valueChanged.connect(sync_mode_from_values)
        spin_display.valueChanged.connect(sync_mode_from_values)
        update_grid_enabled()
        update_layout_controls()

        note = QLabel("说明：文件位置采样优先保证导入和交互流畅；空间网格采样会先扫描全文件确定 X/Y 范围，再按网格保留代表点、Z最小点和Z最大点。导入抽样会影响参与分析的数据量，显示上限只影响绘图。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        grid.addWidget(note, 15, 0, 1, 2)
        layout.addWidget(group)

        status = QLabel(self.lbl_import_status.text() if hasattr(self, 'lbl_import_status') else "导入状态: --")
        status.setWordWrap(True)
        status.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            old_display_limit = self.display_point_limit
            self.input_layout_mode = str(cb_input_layout.currentData())
            QSettings("SurfaceRxyZxyAnalyzer", "SurfaceAnalyzer").setValue(
                "input_layout_mode", self.input_layout_mode)
            self.auto_sample_large_text = chk_auto.isChecked()
            self.large_file_sample_method = str(cb_sample_method.currentData())
            self.large_text_grid_count = int(spin_grid.value())
            self.large_text_threshold_mb = int(spin_mb.value())
            self.large_text_import_limit = int(spin_import.value())
            self.matrix_analysis_threshold = int(spin_matrix_threshold.value())
            self.display_point_limit = int(spin_display.value())
            self.height_matrix_pitch_x_um = float(spin_pitch_x.value())
            self.height_matrix_pitch_y_um = float(spin_pitch_y.value())
            self.height_matrix_z_unit = str(cb_matrix_z_unit.currentText())
            self.height_matrix_start_row = int(spin_matrix_start.value())
            self.height_matrix_cols = int(spin_matrix_cols.value())
            self.height_matrix_rows = int(spin_matrix_rows.value())
            self.import_start_row = int(spin_start_row.value())
            self.import_encoding = str(cb_encoding.currentData())
            self.import_delimiter = str(cb_delimiter.currentData())
            self.import_x_col, self.import_y_col, self.import_z_col = (
                int(spin.value()) for spin in column_spins)
            self.import_x_unit, self.import_y_unit, self.import_z_unit = (
                str(combo.currentText()) for combo in unit_combos)
            self.pixel_origin_x = float(spin_origin_x.value())
            self.pixel_origin_y = float(spin_origin_y.value())
            self.pitch_source = str(cb_pitch_source.currentData())
            self.settings.setValue("pitch_source", self.pitch_source)
            self.large_file_mode = self._matching_bigfile_mode()
            self.import_info['display_limit'] = self.display_point_limit
            self.import_info['large_file_mode'] = self._bigfile_mode_label()
            self.import_info['sample_method'] = self._sample_method_label()
            self.import_info['grid_count'] = self.large_text_grid_count
            self.import_info['stride_n'] = self.large_text_stride_n
            self.import_info['matrix_pitch_x_um'] = self.height_matrix_pitch_x_um
            self.import_info['matrix_pitch_y_um'] = self.height_matrix_pitch_y_um
            self.import_info['sampling_pitch_x_um'] = self.height_matrix_pitch_x_um
            self.import_info['sampling_pitch_y_um'] = self.height_matrix_pitch_y_um
            self.import_info['matrix_z_unit'] = self.height_matrix_z_unit
            self.import_info['matrix_start_row'] = self.height_matrix_start_row
            self.import_info['matrix_requested_cols'] = self.height_matrix_cols
            self.import_info['input_layout_mode'] = self.input_layout_mode
            self._update_import_status_label()
            if old_display_limit != self.display_point_limit and self.df_raw is not None and self.active_idx is not None:
                self.update_plots_only()
            layout_text = self._input_layout_label(self.input_layout_mode)
            self._show_status(f"文件导入策略已更新：{layout_text}", 5000)

    @staticmethod
    def _detect_sep_from_line(line):
        if '\t' in line:
            return '\t'
        if '；' in line:
            return '；'
        if ',' in line:
            return ','
        if ';' in line:
            return ';'
        return r'\s+'

    def _encoding_candidates(self):
        selected = str(getattr(self, 'import_encoding', 'auto') or 'auto')
        if selected != 'auto':
            return (selected,)
        return ('utf-8-sig', 'gbk', 'utf-16', 'latin-1')

    def _delimiter_override(self):
        selected = str(getattr(self, 'import_delimiter', 'auto') or 'auto')
        if selected == 'auto':
            return None
        return r'\s+' if selected == 'whitespace' else selected

    def _configured_start_line(self, layout_mode):
        common = max(0, int(getattr(self, 'import_start_row', 0) or 0) - 1)
        if layout_mode == 'height_matrix':
            legacy = max(0, int(getattr(self, 'height_matrix_start_row', 0) or 0) - 1)
            return legacy if legacy > 0 else common
        return common

    @staticmethod
    def _split_text_line(line, sep):
        line = re.sub(r'(?i)\bno\s+data\b', 'NoData', str(line))
        if sep == r'\s+':
            return [t for t in re.split(r'\s+', line.strip()) if t]
        return [t.strip() for t in line.strip().split(sep)]

    @staticmethod
    def _split_matrix_line(line, sep):
        """Split one fixed-grid row without moving logical matrix columns."""
        text = str(line).rstrip('\r\n')
        if text.startswith('\ufeff'):
            text = text[1:]
        if sep == r'\s+':
            return [token for token in re.split(r'\s+', text.strip()) if token]
        return [token.strip() for token in text.split(sep)]

    @classmethod
    def _normalize_matrix_tokens(cls, tokens, expected_cols=None,
                                 value_start=0, trailing_terminator=False):
        values = list(tokens)
        if trailing_terminator and values and cls._is_missing_token(values[-1]):
            values.pop()
        values = values[int(value_start):]
        if expected_cols is not None:
            expected = int(expected_cols)
            if len(values) < expected:
                values.extend([''] * (expected - len(values)))
            elif len(values) > expected:
                raise ValueError(
                    f"Z Matrix 实际逻辑列数 {len(values)} 与表头声明 {expected} 不一致。")
        return values

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()

    @staticmethod
    def _trim_trailing_empty_tokens(tokens):
        values = list(tokens)
        while values and not str(values[-1]).strip():
            values.pop()
        return values

    @classmethod
    def _is_missing_token(cls, value):
        token = re.sub(r'\s+', ' ', str(value).strip()).casefold()
        known = {re.sub(r'\s+', ' ', str(item).strip()).casefold()
                 for item in cls.MISSING_TEXT_TOKENS}
        return not token or token in known

    @classmethod
    def _is_float_token(cls, value):
        try:
            float(str(value))
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def _is_float_or_missing_token(cls, value):
        if cls._is_missing_token(value):
            return True
        return cls._is_float_token(value)

    @classmethod
    def _looks_like_numeric_text_row(cls, tokens):
        if len(tokens) < 2:
            return False
        numeric_count = sum(cls._is_float_token(t) for t in tokens)
        return numeric_count >= 2 and all(cls._is_float_or_missing_token(t) for t in tokens)

    @classmethod
    def _looks_like_matrix_row(cls, tokens, expected_cols=None):
        if len(tokens) < 2 or not all(cls._is_float_or_missing_token(t) for t in tokens):
            return False
        width = int(expected_cols or len(tokens))
        minimum_numeric = 2 if width <= 10 else min(16, max(3, width // 20))
        return sum(cls._is_float_token(token) for token in tokens) >= minimum_numeric

    @classmethod
    def _looks_like_point_record_row(cls, tokens):
        """XYZ point records may contain extra text fields such as probe or quality."""
        if len(tokens) < 3:
            return False
        return sum(cls._is_float_token(t) for t in tokens) >= 3

    @classmethod
    def _looks_like_pixel_record_row(cls, tokens):
        """Pixel records remain part of the raster when Z is an explicit missing token."""
        if len(tokens) < 3:
            return False
        numeric = sum(cls._is_float_token(token) for token in tokens)
        missing = sum(cls._is_missing_token(token) for token in tokens)
        return numeric >= 2 and numeric + missing >= 3

    @staticmethod
    def _normalize_header_label(value):
        """Normalize only for matching; the original label is kept for the UI."""
        text = unicodedata.normalize('NFKC', str(value).replace('\ufeff', '').strip())
        text = text.replace('µ', 'u').replace('μ', 'u').lower()
        text = re.sub(r'[\[\](){}_/\\-]+', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    @classmethod
    def _header_axis_kind(cls, value):
        normalized = cls._normalize_header_label(value)
        compact = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', normalized)
        words = set(re.findall(r'[a-z]+|[\u4e00-\u9fff]+', normalized))

        def axis_match(axis):
            if compact in (axis, f'{axis}mm', f'{axis}um', f'{axis}nm'):
                return True
            if f'{axis}坐标' in compact or f'坐标{axis}' in compact:
                return True
            return axis in words and bool(words & {'pos', 'position', 'coordinate', 'coord'})

        if axis_match('x'):
            return 'x'
        if axis_match('y'):
            return 'y'
        if axis_match('z'):
            return 'z'
        if any(term in compact for term in ('height', 'thickness', '厚度', '高度')):
            return 'z'
        return None

    @classmethod
    def _header_unit_hint(cls, value):
        normalized = cls._normalize_header_label(value)
        compact = re.sub(r'[^a-z0-9]+', '', normalized)
        if re.search(r'(^|\W)nm($|\W)', normalized) or compact.endswith('nm'):
            return 'nm'
        if re.search(r'(^|\W)um($|\W)', normalized) or compact.endswith('um'):
            return 'µm'
        if re.search(r'(^|\W)mm($|\W)', normalized) or compact.endswith('mm'):
            return 'mm'
        return None

    @classmethod
    def _header_semantics(cls, tokens):
        axis_candidates = {'x': [], 'y': [], 'z': []}
        unit_hints = {}
        for index, token in enumerate(tokens):
            axis = cls._header_axis_kind(token)
            if axis:
                axis_candidates[axis].append(index)
                unit = cls._header_unit_hint(token)
                if unit:
                    unit_hints[axis] = unit
        mapping = {
            axis: indices[0]
            for axis, indices in axis_candidates.items()
            if len(indices) == 1
        }
        unambiguous = len(mapping) == 3 and len(set(mapping.values())) == 3
        return {
            'mapping': mapping,
            'unit_hints': unit_hints,
            'unambiguous': unambiguous,
            'axis_candidates': axis_candidates,
        }

    @classmethod
    def _pixel_header_semantics(cls, tokens):
        candidates = {'x': [], 'y': [], 'z': []}
        unit_hints = {}
        for index, token in enumerate(tokens):
            normalized = cls._normalize_header_label(token)
            compact = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', normalized)
            if compact in {'pixelx', 'xpixel', 'xindex', 'column', 'col', '像素x', 'x像素'}:
                candidates['x'].append(index)
            elif compact in {'pixely', 'ypixel', 'yindex', 'row', '像素y', 'y像素'}:
                candidates['y'].append(index)
            elif cls._header_axis_kind(token) == 'z':
                candidates['z'].append(index)
                unit = cls._header_unit_hint(token)
                if unit:
                    unit_hints['z'] = unit
        mapping = {axis: values[0] for axis, values in candidates.items() if len(values) == 1}
        return {
            'mapping': mapping,
            'unit_hints': unit_hints,
            'unambiguous': len(mapping) == 3 and len(set(mapping.values())) == 3,
            'axis_candidates': candidates,
        }

    @classmethod
    def _looks_like_xyz_header(cls, tokens):
        return bool(cls._header_semantics(tokens)['unambiguous'])

    @staticmethod
    def _dedupe_header_tokens(tokens):
        result = []
        seen = {}
        for index, token in enumerate(tokens, start=1):
            base = str(token).replace('\ufeff', '').strip() or f'Col{index}'
            key = base.casefold()
            seen[key] = seen.get(key, 0) + 1
            result.append(base if seen[key] == 1 else f'{base}_{seen[key]}')
        return result

    @classmethod
    def _is_plausible_custom_header(cls, tokens, sep=None):
        cleaned = [str(token).replace('\ufeff', '').strip() for token in tokens]
        nonempty = [token for token in cleaned if token]
        if len(cleaned) < 2 or len(nonempty) < 2:
            return False
        if len({token.casefold() for token in nonempty}) < 2:
            return False
        if sum(cls._is_float_token(token) for token in nonempty) >= max(1, len(nonempty) - 1):
            return False
        joined = ' '.join(nonempty)
        assignment_marks = joined.count(':') + joined.count('=')
        if assignment_marks >= max(2, int(np.ceil(len(cleaned) * 0.3))):
            return False
        if len(joined) > max(240, len(cleaned) * 60) or any(len(token) > 100 for token in nonempty):
            return False
        sentence_words = re.findall(r'[A-Za-z\u4e00-\u9fff]+', joined)
        if len(cleaned) <= 3 and len(sentence_words) > 16:
            return False
        return True

    @classmethod
    def _header_candidate_info(cls, tokens, sep, expected_ncols=None):
        original = [str(token).replace('\ufeff', '').strip() for token in tokens]
        if expected_ncols is not None and len(original) != int(expected_ncols):
            return None
        semantics = cls._header_semantics(original)
        if semantics['unambiguous']:
            confidence = 'semantic'
        elif cls._is_plausible_custom_header(original, sep):
            confidence = 'candidate'
        else:
            return None
        return {
            'tokens': cls._dedupe_header_tokens(original),
            'sep': sep,
            'confidence': confidence,
            'mapping': semantics['mapping'] if semantics['unambiguous'] else {},
            'unit_hints': semantics['unit_hints'] if semantics['unambiguous'] else {},
        }

    @classmethod
    def _token_to_float(cls, value):
        if cls._is_missing_token(value):
            return np.nan
        try:
            return float(value)
        except (TypeError, ValueError):
            return np.nan

    @classmethod
    def _detect_text_layout(cls, path, enc, max_scan_lines=50000, start_line_no=0,
                            layout_mode='point_table', matrix_metadata=None,
                            progress=None, cancel_event=None, forced_sep=None):
        """扫描文本开头，识别第一行有效数值数据、分隔符、列数和可选表头。
        不再命中第一组数值行就立即返回，而是比较多个候选区，避免把设备参数表误认成数据。"""
        candidates = []
        run = None
        min_data_rows = 3
        header_candidate = None
        matrix_metadata = matrix_metadata or {}
        expected_cols = matrix_metadata.get('expected_cols')
        height_marker_line = matrix_metadata.get('height_marker_line')
        ambiguous_whitespace_matrix = False

        def finish_run(end_line_no=None):
            nonlocal run
            if run is not None and run['count'] >= min_data_rows:
                item = dict(run)
                item['data_end_line_no'] = end_line_no
                item['data_row_count'] = int(item.pop('count'))
                item.pop('last_line_no', None)
                candidates.append(item)
            run = None

        def score(item):
            count = int(item.get('data_row_count', item.get('count', 0)))
            ncols = int(item.get('ncols', 0))
            stable_matrix = layout_mode == 'height_matrix' and ncols >= 2 and count >= 2
            after_height = bool(height_marker_line is not None
                                and int(item['data_line_no']) > int(height_marker_line))
            return (int(stable_matrix), int(after_height), count * ncols, count, ncols,
                    int(item['data_line_no']))

        def best_candidate(open_run=None):
            pool = list(candidates)
            if open_run is not None and open_run.get('count', 0) >= min_data_rows:
                item = dict(open_run)
                item['data_end_line_no'] = None
                item['data_row_count'] = int(item.pop('count'))
                item.pop('last_line_no', None)
                pool.append(item)
            if not pool:
                return None
            selected = max(pool, key=score)
            selected['candidate_count'] = len(pool)
            return selected

        with open(path, 'r', encoding=enc, errors='strict') as fh:
            for line_no, line in enumerate(fh):
                cls._check_cancel(cancel_event)
                if line_no < max(0, int(start_line_no)):
                    continue
                if line_no >= max(0, int(start_line_no)) + max_scan_lines:
                    return best_candidate(run)
                stripped = line.strip().lstrip('\ufeff')
                if not stripped:
                    finish_run(line_no)
                    continue
                is_comment = stripped.startswith('#')
                candidate_text = stripped[1:].strip() if is_comment else stripped
                if is_comment and not candidate_text:
                    finish_run(line_no)
                    continue
                if is_comment:
                    candidate_sep = forced_sep or cls._detect_sep_from_line(candidate_text)
                    candidate_tokens = cls._trim_trailing_empty_tokens(
                        cls._split_text_line(candidate_text, candidate_sep))
                    candidate = cls._header_candidate_info(candidate_tokens, candidate_sep)
                    if candidate and candidate['confidence'] == 'semantic':
                        candidate['line_no'] = line_no
                        header_candidate = candidate
                    finish_run(line_no)
                    continue
                sep = forced_sep or cls._detect_sep_from_line(line.rstrip('\r\n'))
                if layout_mode == 'height_matrix' and sep == r'\s+':
                    whitespace_tokens = cls._split_text_line(stripped, sep)
                    if expected_cols is not None and len(whitespace_tokens) == int(expected_cols):
                        tokens = whitespace_tokens
                    else:
                        tokens = None
                    if tokens is None and cls._looks_like_numeric_text_row(whitespace_tokens):
                        ambiguous_whitespace_matrix = True
                        finish_run(line_no)
                        continue
                elif layout_mode == 'height_matrix':
                    tokens = cls._split_matrix_line(line, sep)
                    if expected_cols is not None and len(tokens) <= int(expected_cols):
                        tokens = cls._normalize_matrix_tokens(tokens, int(expected_cols))
                else:
                    tokens = cls._trim_trailing_empty_tokens(cls._split_text_line(stripped, sep))
                if layout_mode == 'height_matrix':
                    is_numeric = cls._looks_like_matrix_row(tokens, expected_cols)
                else:
                    is_numeric = cls._looks_like_point_record_row(tokens)
                    if layout_mode == 'pixel_xy' and not is_numeric:
                        is_numeric = cls._looks_like_pixel_record_row(tokens)
                if is_numeric:
                    same_run = (
                        run is not None and run['sep'] == sep and run['ncols'] == len(tokens)
                        and line_no == run['last_line_no'] + 1
                    )
                    if not same_run:
                        finish_run(line_no)
                        chosen_header = None
                        if (header_candidate and header_candidate['sep'] == sep
                                and len(header_candidate['tokens']) == len(tokens)):
                            chosen_header = header_candidate
                        run = {
                            'encoding': enc,
                            'sep': sep,
                            'ncols': len(tokens),
                            'data_line_no': line_no,
                            'header_tokens': (list(chosen_header['tokens'])
                                              if chosen_header else None),
                            'first_numeric_line': stripped,
                            'header_line_no': (chosen_header.get('line_no')
                                               if chosen_header else None),
                            'header_sep': (chosen_header.get('sep') if chosen_header else None),
                            'header_confidence': (chosen_header.get('confidence')
                                                  if chosen_header else 'generated'),
                            'header_mapping': (dict(chosen_header.get('mapping', {}))
                                               if chosen_header else {}),
                            'header_unit_hints': (dict(chosen_header.get('unit_hints', {}))
                                                  if chosen_header else {}),
                            'expected_cols': (int(expected_cols) if expected_cols is not None else None),
                            'expected_rows': matrix_metadata.get('expected_rows'),
                            'count': 1,
                            'last_line_no': line_no,
                        }
                    else:
                        run['count'] += 1
                        run['last_line_no'] = line_no
                    header_candidate = None
                    # 宽矩阵读取单行成本很高；确认稳定后即可停止布局扫描。
                    if ((layout_mode == 'height_matrix' and run['ncols'] >= 8 and run['count'] >= 32)
                            or run['count'] >= 512):
                        return best_candidate(run)
                    if progress is not None and line_no and line_no % 1000 == 0:
                        progress(20, f"正在识别矩阵数据区: {line_no:,} 行")
                    continue
                finish_run(line_no)
                candidate = cls._header_candidate_info(tokens, sep)
                if candidate:
                    candidate['line_no'] = line_no
                header_candidate = candidate
        finish_run(run['last_line_no'] + 1 if run is not None else None)
        selected = best_candidate()
        if selected is None and layout_mode == 'height_matrix' and ambiguous_whitespace_matrix:
            raise ValueError(
                "无法唯一确定 Z Matrix 列宽，请手动指定矩阵列数或使用具有固定分隔符的文件。")
        return selected

    @classmethod
    def _packed_excel_candidate(cls, raw, start_row=0, forced_delimiter=None):
        """Find a semicolon-like logical table stored inside one physical Excel column."""
        best = None
        delimiters = ((forced_delimiter,) if forced_delimiter is not None
                      else (';', '；', '\t', '|', ','))
        for physical_col in raw.columns:
            values = raw[physical_col].tolist()
            for delimiter in delimiters:
                run = None
                candidates = []
                for row_index in range(max(0, int(start_row)), len(values)):
                    value = values[row_index]
                    text = '' if pd.isna(value) else str(value).strip()
                    tokens = cls._trim_trailing_empty_tokens(cls._split_text_line(text, delimiter))
                    valid = cls._looks_like_point_record_row(tokens)
                    if valid:
                        if run is not None and run['ncols'] == len(tokens) and row_index == run['end'] + 1:
                            run['rows'].append(tokens)
                            run['end'] = row_index
                        else:
                            if run is not None and len(run['rows']) >= 3:
                                candidates.append(run)
                            run = {'physical_col': physical_col, 'delimiter': delimiter,
                                   'start': row_index, 'end': row_index,
                                   'ncols': len(tokens), 'rows': [tokens]}
                    else:
                        if run is not None and len(run['rows']) >= 3:
                            candidates.append(run)
                        run = None
                if run is not None and len(run['rows']) >= 3:
                    candidates.append(run)
                for candidate in candidates:
                    score = len(candidate['rows']) * candidate['ncols']
                    if best is None or score > best['score']:
                        best = dict(candidate, score=score)
        return best

    def _read_packed_single_column_excel(self, path, raw):
        delimiter = self._delimiter_override()
        if delimiter == r'\s+':
            delimiter = '\t'
        candidate = self._packed_excel_candidate(
            raw, start_row=self._configured_start_line(self.input_layout_mode),
            forced_delimiter=delimiter)
        if candidate is None:
            raise ValueError(
                "XYZ点表模式下，Excel只有一个物理列，但未找到连续的分隔式XYZ数据区。\n"
                "支持英文/中文分号、Tab、竖线或逗号；每条记录至少需包含3个数值字段。")
        physical_col = candidate['physical_col']
        delimiter = candidate['delimiter']
        start = int(candidate['start'])
        header_info = None
        header_row = None
        for row_index in range(start):
            value = raw.at[row_index, physical_col]
            text = '' if pd.isna(value) else str(value).strip()
            if not text:
                continue
            is_comment = text.startswith('#')
            candidate_text = text[1:].strip() if is_comment else text
            possible = self._trim_trailing_empty_tokens(
                self._split_text_line(candidate_text, delimiter))
            possible_info = self._header_candidate_info(
                possible, delimiter, expected_ncols=candidate['ncols'])
            if is_comment and possible_info and possible_info['confidence'] != 'semantic':
                continue
            if possible_info:
                header_info = possible_info
                header_row = row_index
            elif not is_comment:
                header_info = None
                header_row = None
        header_tokens = (list(header_info['tokens']) if header_info else
                         [f'Col{i+1}' for i in range(candidate['ncols'])])

        logical_rows = []
        for row_index in range(start, len(raw)):
            value = raw.at[row_index, physical_col]
            text = '' if pd.isna(value) else str(value).strip()
            if not text or text.startswith('#'):
                continue
            tokens = self._trim_trailing_empty_tokens(self._split_text_line(text, delimiter))
            if len(tokens) == candidate['ncols']:
                logical_rows.append(tokens)
        frame = pd.DataFrame(logical_rows or candidate['rows'], columns=header_tokens)
        metadata = {}
        metadata_end = header_row if header_row is not None else start
        for row_index in range(max(0, metadata_end)):
            value = raw.at[row_index, physical_col]
            text = '' if pd.isna(value) else str(value).strip()
            if not text:
                continue
            parts = self._trim_trailing_empty_tokens(self._split_text_line(text, delimiter))
            if len(parts) >= 2 and str(parts[0]).strip():
                metadata[str(parts[0]).strip()] = delimiter.join(str(part).strip() for part in parts[1:])
        delimiter_label = {';': ';', '；': '；', '\t': 'Tab', '|': '|', ',': ','}[delimiter]
        self.import_info.update({
            'strategy': 'Excel单列分隔式XYZ读取',
            'source_format': 'Excel单列分隔XYZ点表',
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'import_rows': len(frame),
            'packed_single_column': True,
            'packed_delimiter': delimiter_label,
            'packed_physical_column': int(physical_col) + 1 if isinstance(physical_col, (int, np.integer)) else str(physical_col),
            'header_source_line': (header_row + 1) if header_row is not None else None,
            'header_confidence': header_info['confidence'] if header_info else 'generated',
            'header_source': 'excel_single_column',
            'header_auto_mapping': dict(header_info.get('mapping', {})) if header_info else {},
            'header_unit_hints': dict(header_info.get('unit_hints', {})) if header_info else {},
            'metadata': metadata,
            'notes': f"单列拆分 {delimiter_label} | 跳过前置说明 {start} 行",
        })
        self.last_import_note = (
            f"已将Excel物理列拆分为 {candidate['ncols']} 个逻辑字段；"
            f"数据从第 {start + 1} 行开始，保留 {len(metadata)} 项前置扫描参数。")
        return frame

    @classmethod
    def _extract_text_preamble_metadata(cls, path, enc, data_line_no, header_line_no=None):
        metadata = {}
        try:
            with open(path, 'r', encoding=enc, errors='ignore') as handle:
                for row_index, line in enumerate(handle):
                    if row_index >= int(data_line_no):
                        break
                    if header_line_no is not None and row_index == int(header_line_no):
                        continue
                    text = line.strip().lstrip('\ufeff')
                    if not text or text.startswith('#'):
                        continue
                    delimiter = cls._detect_sep_from_line(text)
                    parts = cls._trim_trailing_empty_tokens(cls._split_text_line(text, delimiter))
                    if len(parts) >= 2 and str(parts[0]).strip():
                        metadata[str(parts[0]).strip()] = (
                            delimiter.join(str(part).strip() for part in parts[1:]))
        except Exception:
            return {}
        return metadata

    def _excel_height_matrix_metadata(self, raw):
        metadata = {
            'expected_rows': None, 'expected_cols': None,
            'pitch_x_um': float(self.height_matrix_pitch_x_um),
            'pitch_y_um': float(self.height_matrix_pitch_y_um),
            'z_unit': str(self.height_matrix_z_unit),
            'source_format': 'Excel Z矩阵', 'detected_fields': [], 'metadata': {},
        }
        keyence = False
        for _, row in raw.head(5000).iterrows():
            values = ['' if pd.isna(value) else str(value).strip() for value in row.tolist()]
            text = ' '.join(value for value in values if value)
            if not text:
                continue
            normalized = unicodedata.normalize('NFKC', text).replace('μ', 'µ')
            lowered = normalized.lower()
            if 'imagedatacsv' in lowered or 'keyence' in lowered or '基恩士' in lowered:
                keyence = True
            numbers = re.findall(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', normalized)
            value = float(numbers[-1]) if numbers else None
            if value is not None and '水平' in normalized:
                metadata['expected_cols'] = int(round(value)); metadata['detected_fields'].append('水平')
            if value is not None and '垂直' in normalized:
                metadata['expected_rows'] = int(round(value)); metadata['detected_fields'].append('垂直')
            if value is not None and re.search(r'xy\s*校准', lowered):
                factor = 0.001 if 'nm' in lowered else (1000.0 if 'mm' in lowered else 1.0)
                metadata['pitch_x_um'] = value * factor
                metadata['pitch_y_um'] = value * factor
                metadata['detected_fields'].append('XY校准')
            if ('单位' in normalized or 'unit' in lowered) and ('高度' in normalized or 'z' in lowered):
                if re.search(r'(^|\W)mm(\W|$)', lowered): metadata['z_unit'] = 'mm'
                elif re.search(r'(^|\W)(um|µm)(\W|$)', lowered): metadata['z_unit'] = 'µm'
                metadata['detected_fields'].append('Z单位')
        if keyence:
            metadata['source_format'] = 'Keyence VR ImageDataCsv'
        metadata['detected_fields'] = list(dict.fromkeys(metadata['detected_fields']))
        return metadata

    def _read_excel_height_matrix(self, path, raw, progress=None, cancel_event=None):
        """Read an explicitly selected Excel Z matrix, allowing metadata rows above it."""
        manual_start = self._configured_start_line('height_matrix')
        metadata = self._excel_height_matrix_metadata(raw)
        manual_cols = int(getattr(self, 'height_matrix_cols', 0) or 0)
        manual_rows = int(getattr(self, 'height_matrix_rows', 0) or 0)
        if manual_cols > 0:
            metadata['expected_cols'] = manual_cols
        if manual_rows > 0:
            metadata['expected_rows'] = manual_rows
        expected_cols = metadata.get('expected_cols')
        expected_rows = metadata.get('expected_rows')
        runs = []
        run = None
        for row_index in range(manual_start, len(raw)):
            self._check_cancel(cancel_event)
            row = raw.iloc[row_index].tolist()
            if expected_cols is not None:
                row = row[:int(expected_cols)]
                if len(row) < int(expected_cols):
                    row.extend([np.nan] * (int(expected_cols) - len(row)))
            valid = self._looks_like_matrix_row(row, expected_cols)
            if valid:
                if run is not None and run['ncols'] == len(row) and row_index == run['end'] + 1:
                    run['rows'].append(row)
                    run['end'] = row_index
                else:
                    if run is not None and len(run['rows']) >= 3:
                        runs.append(run)
                    run = {'start': row_index, 'end': row_index, 'ncols': len(row), 'rows': [row]}
            else:
                if run is not None and len(run['rows']) >= 3:
                    runs.append(run)
                run = None
        if run is not None and len(run['rows']) >= 3:
            runs.append(run)
        if not runs:
            raise ValueError("Z矩阵模式下，Excel中未找到至少3行、2列的连续数值矩阵区。")
        selected = max(runs, key=lambda item: len(item['rows']) * item['ncols'])
        values = np.asarray(
            [[self._token_to_float(value) for value in row] for row in selected['rows']], dtype=float)
        coordinate_header = False
        if values.shape[1] >= 3 and np.isnan(values[0, 0]) and self._regular_numeric_sequence(values[0, 1:]):
            values = values[1:, 1:]
            coordinate_header = True
        elif values.shape[1] >= 3 and values.shape[0] >= 3:
            first_column = values[:, 0]
            if (self._regular_numeric_sequence(first_column)
                    and np.allclose(first_column, np.round(first_column), rtol=0.0, atol=1e-9)
                    and abs(abs(float(np.median(np.diff(first_column)))) - 1.0) <= 1e-9):
                values = values[:, 1:]
                coordinate_header = True
        if values.shape[0] < 2 or values.shape[1] < 2:
            raise ValueError("Excel矩阵去除坐标标签后小于2×2，无法生成面型。")
        if expected_rows is not None and values.shape[0] != int(expected_rows):
            raise ValueError(
                f"Excel Z Matrix 行数与表头冲突：声明 {int(expected_rows)}，实际 {values.shape[0]}。")
        if expected_cols is not None and values.shape[1] != int(expected_cols):
            raise ValueError(
                f"Excel Z Matrix 列数与表头冲突：声明 {int(expected_cols)}，实际 {values.shape[1]}。")
        if str(getattr(self, 'pitch_source', 'manual')) == 'manual':
            pitch_x = float(self.height_matrix_pitch_x_um)
            pitch_y = float(self.height_matrix_pitch_y_um)
            pitch_source = '用户手动'
        else:
            pitch_x = float(metadata['pitch_x_um'])
            pitch_y = float(metadata['pitch_y_um'])
            pitch_source = ('可靠文件metadata' if 'XY校准' in metadata.get('detected_fields', [])
                            else '用户默认')
        z_override = str(getattr(self, 'import_z_unit', 'auto'))
        z_unit = z_override if z_override != 'auto' else str(metadata['z_unit'])
        valid_points = int(np.isfinite(values).sum())
        point_threshold = int(getattr(self, 'matrix_analysis_threshold', 400_000))
        sampled = bool(getattr(self, 'auto_sample_large_text', True)
                       and valid_points > point_threshold)
        if progress is not None:
            progress(55, f"Excel矩阵有效点 {valid_points:,}，正在准备分析数据")
        if sampled:
            frame, method_key, extrema_preserved = self._sample_height_matrix_array(
                values, pitch_x, pitch_y, method=self.large_file_sample_method)
            strategy = 'Excel高度矩阵分析前采样'
        else:
            frame = self._height_matrix_dataframe(
                values, values.shape[0], values.shape[1], pitch_x, pitch_y)
            method_key, extrema_preserved, strategy = 'full', True, 'Excel高度矩阵全量读取'
        self.import_info.update({
            'strategy': strategy,
            'source_format': metadata['source_format'],
            'sampled': sampled,
            'sample_method_key': method_key,
            'extrema_preserved': extrema_preserved,
            'height_matrix': True,
            'matrix_rows': int(values.shape[0]),
            'matrix_cols': int(values.shape[1]),
            'source_matrix_positions': int(values.size),
            'source_valid_rows': valid_points,
            'original_valid_points': valid_points,
            'analysis_points': len(frame),
            'display_points': min(len(frame), self._display_limit()),
            'matrix_data_start_row': int(selected['start']) + 1,
            'matrix_coordinate_header': coordinate_header,
            'matrix_pitch_x_um': pitch_x,
            'matrix_pitch_y_um': pitch_y,
            'sampling_pitch_x_um': pitch_x,
            'sampling_pitch_y_um': pitch_y,
            'sampling_pitch_source': pitch_source,
            'matrix_z_unit': z_unit,
            'z_source_field': '矩阵高度值',
            'z_source_unit': z_unit,
            'import_rows': len(frame),
            'valid_rows': len(frame),
            'matrix_metadata': metadata,
            'matrix_analysis_threshold': point_threshold,
            'topology_method': ('sampled_matrix8'
                                if self.import_info.get('sample_method_key') == 'stride'
                                else ('matrix8' if self.import_info.get('sample_method_key') == 'full'
                                      else 'Delaunay/adaptive kNN')),
            'notes': (f"Excel矩阵 {values.shape[0]}×{values.shape[1]} | 有效 {valid_points:,} | "
                      f"分析 {len(frame):,} | 跳过前置说明 {selected['start']} 行"),
        })
        self.last_import_note = (
            f"已按Z矩阵读取Excel：{values.shape[0]}×{values.shape[1]}；有效 {valid_points:,}，"
            f"分析 {len(frame):,}；跳过前置说明 {selected['start']} 行。")
        if progress is not None:
            progress(85, "Excel Z Matrix 解析完成，正在初始化分析数据")
        return frame

    @staticmethod
    def _normalize_unit_label(text, default_unit="µm"):
        raw = str(text or "").strip().lower().replace("μ", "µ")
        if raw in ("mm", "millimeter", "millimeters"):
            return "mm"
        if raw in ("um", "µm", "micron", "microns"):
            return "µm"
        return default_unit

    @staticmethod
    def _regular_numeric_sequence(values):
        arr = np.asarray(values, dtype=float)
        if arr.size < 3 or not np.all(np.isfinite(arr)):
            return False
        diffs = np.diff(arr)
        median = float(np.median(diffs))
        if abs(median) <= 1e-12 or not (np.all(diffs > 0) or np.all(diffs < 0)):
            return False
        atol = max(abs(median) * 0.05, 1e-9)
        return bool(np.allclose(diffs, median, rtol=0.05, atol=atol))

    def _prepare_height_matrix_layout(self, path, enc, layout):
        """识别矩阵顶部列坐标、左侧行号和尾部空列，并返回标准 Z 区域布局。"""
        prepared = dict(layout)
        raw_ncols = int(prepared['ncols'])
        original_start = int(prepared['data_line_no'])
        sample_rows = []
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
                if line_no < original_start:
                    continue
                if len(sample_rows) >= 16:
                    break
                stripped = line.strip().lstrip('\ufeff')
                if not stripped:
                    continue
                tokens = self._split_matrix_line(line, prepared['sep'])
                if prepared.get('expected_cols') is not None and len(tokens) <= int(prepared['expected_cols']):
                    tokens = self._normalize_matrix_tokens(tokens, int(prepared['expected_cols']))
                if len(tokens) != raw_ncols:
                    break
                sample_rows.append((line_no, tokens))

        value_start = 0
        coordinate_header = False
        data_start = original_start
        header = list(prepared.get('header_tokens') or [])
        axis_words = (
            'y/x', 'y\\x', 'x', 'x坐标', 'xcoordinate', 'row', 'index',
            '行号', '行', '列坐标', 'y坐标', 'ycoordinate')

        if header and len(header) == raw_ncols:
            first_label = str(header[0]).strip().lower().replace(' ', '')
            rest = [self._token_to_float(value) for value in header[1:]]
            if any(word in first_label for word in axis_words) and len(rest) >= 2:
                value_start = 1
                coordinate_header = True

        if sample_rows:
            first_tokens = sample_rows[0][1]
            rest = [self._token_to_float(value) for value in first_tokens[1:]]
            coordinate_values = rest[:-1] if rest and not np.isfinite(rest[-1]) else rest
            if (self._is_missing_token(first_tokens[0]) and len(coordinate_values) >= 2
                    and self._regular_numeric_sequence(coordinate_values)):
                value_start = 1
                coordinate_header = True
                data_start = int(sample_rows[0][0]) + 1

        data_samples = [tokens for line_no, tokens in sample_rows if line_no >= data_start]
        first_column = [self._token_to_float(tokens[0]) for tokens in data_samples if tokens]
        integer_row_index = (
            len(first_column) >= 3 and self._regular_numeric_sequence(first_column)
            and np.allclose(first_column, np.round(first_column), rtol=0.0, atol=1e-9)
            and abs(abs(float(np.median(np.diff(first_column)))) - 1.0) <= 1e-9)
        if value_start == 0 and raw_ncols >= 3 and integer_row_index:
            value_start = 1
            coordinate_header = True

        trailing_terminator = bool(
            coordinate_header and sample_rows and sample_rows[0][1]
            and self._is_missing_token(sample_rows[0][1][-1]))
        value_count = raw_ncols - value_start - int(trailing_terminator)
        prepared.update({
            'raw_ncols': raw_ncols,
            'ncols': value_count,
            'matrix_value_start': value_start,
            'matrix_coordinate_header': coordinate_header,
            'matrix_trailing_terminator': trailing_terminator,
            'detected_data_line_no': original_start,
            'data_line_no': data_start,
            'header_rows_skipped': data_start,
        })
        return prepared

    def _scan_height_matrix_metadata(self, path, enc, max_lines=50000,
                                     progress=None, cancel_event=None):
        """Normalize optional vendor metadata for the generic matrix parser."""
        result = {
            'expected_rows': None,
            'expected_cols': None,
            'pitch_x_um': float(getattr(self, 'height_matrix_pitch_x_um', 47.242)),
            'pitch_y_um': float(getattr(self, 'height_matrix_pitch_y_um', 47.242)),
            'z_unit': str(getattr(self, 'height_matrix_z_unit', 'µm')),
            'source_format': '通用Z矩阵',
            'invalid_values': [],
            'height_marker_line': None,
            'metadata': {},
            'detected_fields': [],
        }
        output_is_height = False
        keyence = False
        matrix_run_key = None
        matrix_run_count = 0
        with open(path, 'r', encoding=enc, errors='strict') as handle:
            for line_no, raw_line in enumerate(handle):
                if line_no >= int(max_lines):
                    break
                self._check_cancel(cancel_event)
                text = raw_line.rstrip('\r\n').lstrip('\ufeff')
                stripped = text.strip()
                if not stripped:
                    continue
                if (line_no >= 4096 and not keyence
                        and result.get('expected_rows') is None
                        and result.get('expected_cols') is None):
                    break
                normalized = unicodedata.normalize('NFKC', stripped).replace('μ', 'µ')
                lowered = normalized.lower()
                sep = self._detect_sep_from_line(text)
                if sep != r'\s+':
                    tokens = self._split_matrix_line(raw_line, sep)
                    expected_cols = result.get('expected_cols')
                    if expected_cols is not None and len(tokens) <= int(expected_cols):
                        tokens = self._normalize_matrix_tokens(tokens, int(expected_cols))
                    if self._looks_like_matrix_row(tokens, expected_cols):
                        key = (sep, len(tokens))
                        matrix_run_count = matrix_run_count + 1 if key == matrix_run_key else 1
                        matrix_run_key = key
                        if result.get('height_marker_line') is not None and (
                                expected_cols is not None or matrix_run_count >= 3):
                            break
                        continue
                    matrix_run_key = None
                    matrix_run_count = 0
                if 'imagedatacsv' in lowered or 'keyence' in lowered or '基恩士' in lowered:
                    keyence = True
                if re.search(r'输出图像数据\s*[,;:\t=]?\s*高度', normalized, re.I):
                    output_is_height = True
                    result['height_marker_line'] = line_no
                elif stripped in ('高度', 'Height', 'HEIGHT'):
                    output_is_height = True
                    result['height_marker_line'] = line_no

                parts = [part.strip() for part in re.split(r'[,;\t:=]+', normalized) if part.strip()]
                if len(parts) >= 2:
                    result['metadata'][parts[0]] = ' | '.join(parts[1:])
                numbers = re.findall(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', normalized)
                value = float(numbers[-1]) if numbers else None
                if value is not None and re.search(r'(^|\W)水平(\W|$)', normalized):
                    result['expected_cols'] = int(round(value))
                    result['detected_fields'].append('水平')
                if value is not None and re.search(r'(^|\W)垂直(\W|$)', normalized):
                    result['expected_rows'] = int(round(value))
                    result['detected_fields'].append('垂直')
                if value is not None and re.search(r'xy\s*校准', lowered, re.I):
                    factor = 0.001 if re.search(r'(^|\W)nm(\W|$)', lowered) else (
                        1000.0 if re.search(r'(^|\W)mm(\W|$)', lowered) else 1.0)
                    result['pitch_x_um'] = value * factor
                    result['pitch_y_um'] = value * factor
                    result['detected_fields'].append('XY校准')

                pitch_hint = any(word in lowered for word in (
                    'pitch', 'pixel size', 'pixel spacing', 'resolution', 'spacing',
                    'interval', '间距', '像素尺寸', '分辨率', 'ピッチ'))
                x_hint = bool(re.search(r'(^|[^a-z])x([^a-z]|$)', lowered)) or '横向' in lowered
                y_hint = bool(re.search(r'(^|[^a-z])y([^a-z]|$)', lowered)) or '纵向' in lowered
                factor = 0.001 if re.search(r'(^|\W)nm(\W|$)', lowered) else (
                    1000.0 if re.search(r'(^|\W)mm(\W|$)', lowered) else 1.0)
                if value is not None and pitch_hint and x_hint:
                    result['pitch_x_um'] = value * factor
                    result['detected_fields'].append('Pitch X')
                elif value is not None and pitch_hint and y_hint:
                    result['pitch_y_um'] = value * factor
                    result['detected_fields'].append('Pitch Y')

                unit_context = ('单位' in normalized or 'unit' in lowered)
                if unit_context and (output_is_height or '高度' in normalized or 'z' in lowered):
                    if re.search(r'(^|\W)mm(\W|$)', lowered):
                        result['z_unit'] = 'mm'
                    elif re.search(r'(^|\W)(um|µm)(\W|$)', lowered.replace('μ', 'µ')):
                        result['z_unit'] = 'µm'
                    result['detected_fields'].append('Z单位')

                invalid_hint = any(word in lowered for word in (
                    'invalid', 'missing', 'no data', 'nodata', '无效', '缺测', '欠測', 'データなし'))
                if invalid_hint and value is not None:
                    result['invalid_values'].append(value)
                    result['detected_fields'].append('无效值')
                if progress is not None and line_no and line_no % 1000 == 0:
                    progress(10, f"正在扫描矩阵表头: {line_no:,} 行")
        if keyence:
            result['source_format'] = 'Keyence VR ImageDataCsv'
        result['invalid_values'] = list(dict.fromkeys(result['invalid_values']))
        result['detected_fields'] = list(dict.fromkeys(result['detected_fields']))
        return result

    def _height_matrix_header_meta(self, path, enc, data_line_no):
        meta = self._scan_height_matrix_metadata(path, enc, max_lines=max(1, int(data_line_no)))
        source = ("表头: " + '/'.join(meta['detected_fields'])
                  if meta['detected_fields'] else "默认")
        return (meta['pitch_x_um'], meta['pitch_y_um'], meta['z_unit'],
                tuple(meta['invalid_values']), source)

    def _legacy_height_matrix_header_meta(self, path, enc, data_line_no):
        pitch_x = float(getattr(self, 'height_matrix_pitch_x_um', 47.242))
        pitch_y = float(getattr(self, 'height_matrix_pitch_y_um', 47.242))
        z_unit = str(getattr(self, 'height_matrix_z_unit', "µm"))
        invalid_values = []
        header_lines = []
        try:
            with open(path, 'r', encoding=enc, errors='ignore') as fh:
                for i, line in enumerate(fh):
                    if i >= data_line_no:
                        break
                    header_lines.append(line.strip())
        except Exception:
            return pitch_x, pitch_y, z_unit, tuple(invalid_values), "默认"

        detected = []
        for line in header_lines:
            lowered = line.lower().replace("μ", "µ")
            parts = re.split(r'[,;\t]+', line)
            numeric_values = re.findall(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', line)
            value = float(numeric_values[-1]) if numeric_values else None
            pitch_hint = any(word in lowered for word in (
                'pitch', 'pixel size', 'pixel spacing', 'resolution', 'spacing', 'interval',
                '间距', '像素尺寸', '分辨率', 'ピッチ'))
            x_hint = bool(re.search(r'(^|[^a-z])x([^a-z]|$)', lowered)) or '横向' in lowered
            y_hint = bool(re.search(r'(^|[^a-z])y([^a-z]|$)', lowered)) or '纵向' in lowered
            factor = 1.0
            if re.search(r'(^|[^a-z])nm([^a-z]|$)', lowered):
                factor = 0.001
            elif re.search(r'(^|[^a-z])mm([^a-z]|$)', lowered):
                factor = 1000.0
            if value is not None and pitch_hint and x_hint:
                pitch_x = value * factor
                detected.append('Pitch X')
            elif value is not None and pitch_hint and y_hint:
                pitch_y = value * factor
                detected.append('Pitch Y')

            z_unit_hint = (
                'z unit' in lowered or ('z' in lowered and any(w in lowered for w in ('unit', '单位', '単位')))
                or ('高度' in lowered and any(w in lowered for w in ('单位', '単位'))))
            if z_unit_hint:
                unit_text = parts[-1] if len(parts) >= 2 else line
                if re.search(r'(^|[^a-z])mm([^a-z]|$)', unit_text.lower()):
                    z_unit = 'mm'
                    detected.append('Z单位')
                elif re.search(r'(^|[^a-z])(um|µm)([^a-z]|$)', unit_text.lower().replace('μ', 'µ')):
                    z_unit = 'µm'
                    detected.append('Z单位')

            invalid_hint = any(word in lowered for word in (
                'invalid', 'missing', 'no data', 'nodata', '无效', '缺测', '欠測', 'データなし'))
            if invalid_hint and value is not None:
                invalid_values.append(value)
                detected.append('无效值')
        source = "表头: " + '/'.join(dict.fromkeys(detected)) if detected else "默认"
        return pitch_x, pitch_y, z_unit, tuple(dict.fromkeys(invalid_values)), source

    @staticmethod
    def _mask_matrix_missing_values(values, invalid_values=()):
        arr = np.asarray(values, dtype=float)
        if invalid_values:
            for marker in invalid_values:
                atol = max(1e-9, abs(float(marker)) * 1e-9)
                arr[np.isclose(arr, float(marker), rtol=0.0, atol=atol)] = np.nan
        else:
            # 无表头声明时只兼容历史上实际使用过的精确哨兵，避免把真实深台阶静默删除。
            for marker in (-1000.0, -999.999):
                arr[np.isclose(arr, marker, rtol=0.0, atol=1e-9)] = np.nan
        return arr

    def _looks_like_height_matrix_layout(self, path, enc, layout):
        """判断文本数据是否为二维高度矩阵，而不是普通 XYZ 表格。"""
        if not layout or int(layout.get('ncols', 0)) < 2:
            return False
        prepared = self._prepare_height_matrix_layout(path, enc, layout)
        if int(prepared.get('ncols', 0)) < 2:
            return False
        layout.update(prepared)
        header = [str(x).strip().lower() for x in (layout.get('header_tokens') or [])]
        if header and self._header_semantics(header)['unambiguous']:
            return False

        target_cols = int(layout['ncols'])
        raw_cols = int(layout.get('raw_ncols', target_cols))
        value_start = int(layout.get('matrix_value_start', 0))
        good_rows = 0
        scanned = 0
        try:
            with open(path, 'r', encoding=enc, errors='ignore') as fh:
                for line_no, line in enumerate(fh):
                    if line_no < int(layout['data_line_no']):
                        continue
                    stripped = line.strip().lstrip('\ufeff')
                    if not stripped:
                        continue
                    tokens = self._split_matrix_line(line, layout['sep'])
                    if layout.get('expected_cols') is not None and len(tokens) <= int(layout['expected_cols']):
                        tokens = self._normalize_matrix_tokens(tokens, int(layout['expected_cols']))
                    scanned += 1
                    values = self._normalize_matrix_tokens(
                        tokens, target_cols, value_start,
                        bool(layout.get('matrix_trailing_terminator', False)))
                    if len(tokens) == raw_cols and self._looks_like_matrix_row(values, target_cols):
                        good_rows += 1
                    if scanned >= 16:
                        break
        except Exception:
            return False
        return good_rows >= 3

    def _height_matrix_dataframe(self, z_values, rows_count, cols_count, pitch_x_um, pitch_y_um,
                                 invalid_values=()):
        arr = self._mask_matrix_missing_values(np.asarray(z_values, dtype=float), invalid_values)
        valid = np.isfinite(arr)
        if not np.any(valid):
            raise ValueError("高度矩阵未识别到有效 Z 数据。")
        rr, cc = np.nonzero(valid)
        pitch_x_mm = float(pitch_x_um) / 1000.0
        pitch_y_mm = float(pitch_y_um) / 1000.0
        return pd.DataFrame({
            'X': cc.astype(float) * pitch_x_mm,
            'Y': (float(rows_count - 1) - rr.astype(float)) * pitch_y_mm,
            'Z': arr[rr, cc],
            '_matrix_row': rr.astype(int),
            '_matrix_col': cc.astype(int),
            '_topology_row': rr.astype(int),
            '_topology_col': cc.astype(int),
        })

    def _parse_height_matrix_line(self, line, sep, ncols, value_start=0,
                                  invalid_values=(), trailing_terminator=False,
                                  allow_trailing_padding=False):
        if not str(line).strip():
            return None
        tokens = self._split_matrix_line(line, sep)
        try:
            values_tokens = self._normalize_matrix_tokens(
                tokens, ncols if allow_trailing_padding else None,
                value_start, trailing_terminator)
        except ValueError:
            return None
        if not allow_trailing_padding:
            if len(values_tokens) != int(ncols):
                return None
        if len(values_tokens) < int(ncols):
            return None
        values_tokens = values_tokens[:int(ncols)]
        if not self._looks_like_matrix_row(values_tokens, ncols):
            return None
        values = np.asarray([self._token_to_float(token) for token in values_tokens], dtype=float)
        return self._mask_matrix_missing_values(values, invalid_values)

    def _prescan_height_matrix(self, path, enc, layout, invalid_values=(),
                               progress=None, cancel_event=None):
        ncols = int(layout['ncols'])
        data_line_no = int(layout['data_line_no'])
        data_end_line_no = layout.get('data_end_line_no')
        expected_rows = layout.get('expected_rows')
        allow_padding = bool(layout.get('expected_cols'))
        matrix_started = False
        row_count = 0
        valid_points = 0
        z_min = np.inf
        z_max = -np.inf
        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle):
                self._check_cancel(cancel_event)
                if line_no < data_line_no:
                    continue
                if data_end_line_no is not None and line_no >= int(data_end_line_no):
                    break
                values = self._parse_height_matrix_line(
                    line, layout['sep'], ncols,
                    int(layout.get('matrix_value_start', 0)), invalid_values,
                    bool(layout.get('matrix_trailing_terminator', False)), allow_padding)
                if values is None:
                    if matrix_started:
                        break
                    continue
                matrix_started = True
                row_count += 1
                finite = values[np.isfinite(values)]
                valid_points += int(finite.size)
                if finite.size:
                    z_min = min(z_min, float(np.min(finite)))
                    z_max = max(z_max, float(np.max(finite)))
                if progress is not None and (row_count % 64 == 0 or row_count == expected_rows):
                    if expected_rows:
                        fraction = min(1.0, row_count / max(1, int(expected_rows)))
                        progress(20 + int(25 * fraction),
                                 f"正在预扫描高度矩阵: {row_count:,}/{int(expected_rows):,} 行")
                    else:
                        progress(min(44, 20 + row_count // 100),
                                 f"正在预扫描高度矩阵: {row_count:,} 行")
        if row_count == 0 or valid_points == 0:
            raise ValueError("Z Matrix 未识别到有效的连续二维数据区。")
        if expected_rows is not None and row_count != int(expected_rows):
            raise ValueError(
                f"Z Matrix 行数与表头冲突：表头声明 {int(expected_rows):,} 行，实际识别 {row_count:,} 行。")
        return {
            'matrix_rows': row_count,
            'matrix_cols': ncols,
            'source_matrix_positions': row_count * ncols,
            'original_valid_points': valid_points,
            'z_min': z_min,
            'z_max': z_max,
        }

    def _sample_height_matrix_array(self, values, pitch_x_um, pitch_y_um,
                                    invalid_values=(), method=None):
        arr = self._mask_matrix_missing_values(np.asarray(values, dtype=float), invalid_values)
        valid_points = int(np.isfinite(arr).sum())
        if valid_points == 0:
            raise ValueError("高度矩阵未识别到有效 Z 数据。")
        max_points = self._large_text_import_limit()
        method = str(method or getattr(self, 'large_file_sample_method', 'file_position'))
        if method in ('file_position', 'stride'):
            stride = max(1, int(getattr(self, 'large_text_stride_n', 10)),
                         int(np.ceil(np.sqrt(valid_points / max(1, max_points)))))
            rr, cc = np.mgrid[0:arr.shape[0]:stride, 0:arr.shape[1]:stride]
            rr, cc = rr.ravel(), cc.ravel()
            keep = np.isfinite(arr[rr, cc])
            rr, cc = rr[keep], cc[keep]
            extrema_preserved = False
            method_key = 'stride'
        else:
            max_side = self._max_safe_grid_side(max_points)
            requested = int(getattr(self, 'large_text_grid_count', 0))
            side = min(requested if requested > 0 else self._auto_spatial_grid_side(valid_points, max_points),
                       max_side)
            row_edges = np.linspace(0, arr.shape[0], side + 1, dtype=int)
            col_edges = np.linspace(0, arr.shape[1], side + 1, dtype=int)
            selected = []
            for iy in range(side):
                for ix in range(side):
                    block = arr[row_edges[iy]:row_edges[iy + 1], col_edges[ix]:col_edges[ix + 1]]
                    finite = np.argwhere(np.isfinite(block))
                    if finite.size == 0:
                        continue
                    block_values = block[finite[:, 0], finite[:, 1]]
                    picks = [0, int(np.argmin(block_values)), int(np.argmax(block_values))]
                    seen = set()
                    for pick in picks:
                        r = int(row_edges[iy] + finite[pick, 0])
                        c = int(col_edges[ix] + finite[pick, 1])
                        if (r, c) not in seen:
                            selected.append((r, c)); seen.add((r, c))
            rr = np.asarray([item[0] for item in selected], dtype=int)
            cc = np.asarray([item[1] for item in selected], dtype=int)
            extrema_preserved = True
            method_key = 'spatial_grid'
        pitch_x_mm = float(pitch_x_um) / 1000.0
        pitch_y_mm = float(pitch_y_um) / 1000.0
        frame = pd.DataFrame({
            'X': cc.astype(float) * pitch_x_mm,
            'Y': (float(arr.shape[0] - 1) - rr.astype(float)) * pitch_y_mm,
            'Z': arr[rr, cc],
            '_matrix_row': rr,
            '_matrix_col': cc,
        })
        if method_key == 'stride':
            frame['_topology_row'] = (rr // stride).astype(int)
            frame['_topology_col'] = (cc // stride).astype(int)
        return frame, method_key, extrema_preserved

    def _sample_large_height_matrix_by_stride(self, path, enc, sep, ncols, data_line_no,
                                              pitch_x_um, pitch_y_um, z_unit, meta_source,
                                              data_end_line_no=None, value_start=0, invalid_values=(),
                                              prescan=None, progress=None, cancel_event=None,
                                              trailing_terminator=False, allow_padding=False):
        file_size = Path(path).stat().st_size
        stride = max(1, int(getattr(self, 'large_text_stride_n', 10)))
        max_rows = self._large_text_import_limit()

        def parse_matrix_line(line):
            return self._parse_height_matrix_line(
                line, sep, ncols, value_start, invalid_values,
                trailing_terminator, allow_padding)

        if prescan is None:
            temp_layout = {
                'sep': sep, 'ncols': ncols, 'data_line_no': data_line_no,
                'data_end_line_no': data_end_line_no, 'matrix_value_start': value_start,
                'matrix_trailing_terminator': trailing_terminator,
                'expected_cols': ncols if allow_padding else None,
            }
            prescan = self._prescan_height_matrix(
                path, enc, temp_layout, invalid_values, progress, cancel_event)
        row_count = int(prescan['matrix_rows'])
        valid_points = int(prescan['original_valid_points'])

        if row_count == 0 or valid_points == 0:
            raise ValueError("高度矩阵倍率降采样未识别到有效数据。")

        stride = max(stride, int(np.ceil(np.sqrt(valid_points / max(1, max_rows)))))
        pitch_x_mm = float(pitch_x_um) / 1000.0
        pitch_y_mm = float(pitch_y_um) / 1000.0
        rows = []
        matrix_row = 0
        matrix_started = False
        z_min = np.inf
        z_max = -np.inf
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
                self._check_cancel(cancel_event)
                if line_no < data_line_no:
                    continue
                if data_end_line_no is not None and line_no >= data_end_line_no:
                    break
                values = parse_matrix_line(line)
                if values is None:
                    if matrix_started:
                        break
                    continue
                matrix_started = True
                if matrix_row % stride == 0:
                    for col_idx in range(0, ncols, stride):
                        z = float(values[col_idx])
                        if not np.isfinite(z):
                            continue
                        x = float(col_idx) * pitch_x_mm
                        y = float(row_count - 1 - matrix_row) * pitch_y_mm
                        rows.append([x, y, z, int(matrix_row), int(col_idx)])
                        z_min = min(z_min, z)
                        z_max = max(z_max, z)
                        if len(rows) >= max_rows:
                            break
                matrix_row += 1
                if len(rows) >= max_rows:
                    break
                if progress is not None and matrix_row % 64 == 0:
                    progress(45 + int(30 * matrix_row / max(1, row_count)),
                             f"正在采样高度矩阵: {matrix_row:,}/{row_count:,} 行")

        if not rows:
            raise ValueError("高度矩阵倍率降采样未得到有效点，请调小降采样倍率 N。")

        df = pd.DataFrame(rows, columns=['X', 'Y', 'Z', '_matrix_row', '_matrix_col'])
        df['_topology_row'] = (df['_matrix_row'].to_numpy(dtype=int) // stride)
        df['_topology_col'] = (df['_matrix_col'].to_numpy(dtype=int) // stride)
        self.last_import_note = (
            f"VR/基恩士高度矩阵已按倍率降采样导入。\n"
            f"数据起始行: {data_line_no + 1} | 跳过前置说明: {data_line_no} 行\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB | 矩阵尺寸: {row_count:,} × {ncols:,}\n"
            f"降采样倍率: N={stride}（行列每 {stride} 个像素取 1 点）\n"
            f"Pitch: X={pitch_x_um:g}µm, Y={pitch_y_um:g}µm（{meta_source}）| Z单位: {z_unit}\n"
            f"有效点: {valid_points:,} | 实际导入: {len(df):,} 点 | Z范围(采样后): {z_min:.6g} ~ {z_max:.6g}\n"
            f"注意: 倍率降采样不保留每格 Z min/max，PV/TTV 可能低估；最终复核建议使用空间网格采样。"
        )
        self.import_info.update({
            'strategy': '高度矩阵倍率降采样导入',
            'source_format': '文本Z矩阵',
            'sampled': True,
            'sample_method_key': 'stride',
            'extrema_preserved': False,
            'height_matrix': True,
            'import_rows': len(df),
            'source_valid_rows': valid_points,
            'source_matrix_positions': row_count * ncols,
            'original_valid_points': valid_points,
            'analysis_points': len(df),
            'display_points': min(len(df), self._display_limit()),
            'matrix_rows': row_count,
            'matrix_cols': ncols,
            'matrix_pitch_x_um': float(pitch_x_um),
            'matrix_pitch_y_um': float(pitch_y_um),
            'sampling_pitch_x_um': float(pitch_x_um),
            'sampling_pitch_y_um': float(pitch_y_um),
            'sampling_pitch_source': meta_source,
            'matrix_z_unit': z_unit,
            'z_source_field': '矩阵高度值',
            'z_source_unit': z_unit,
            'matrix_data_start_row': data_line_no + 1,
            'matrix_header_rows_skipped': data_line_no,
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'matrix_invalid_values': list(invalid_values),
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label('stride'),
            'stride_n': stride,
            'notes': f"高度矩阵 | 倍率降采样 N={stride} | Pitch {pitch_x_um:g}/{pitch_y_um:g}µm"
        })
        return df

    def _sample_large_height_matrix(self, path, enc, sep, ncols, data_line_no,
                                    pitch_x_um, pitch_y_um, z_unit, meta_source,
                                    data_end_line_no=None, value_start=0, invalid_values=(),
                                    prescan=None, progress=None, cancel_event=None,
                                    trailing_terminator=False, allow_padding=False):
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()

        def parse_matrix_line(line):
            return self._parse_height_matrix_line(
                line, sep, ncols, value_start, invalid_values,
                trailing_terminator, allow_padding)

        if prescan is None:
            temp_layout = {
                'sep': sep, 'ncols': ncols, 'data_line_no': data_line_no,
                'data_end_line_no': data_end_line_no, 'matrix_value_start': value_start,
                'matrix_trailing_terminator': trailing_terminator,
                'expected_cols': ncols if allow_padding else None,
            }
            prescan = self._prescan_height_matrix(
                path, enc, temp_layout, invalid_values, progress, cancel_event)
        row_count = int(prescan['matrix_rows'])
        valid_points = int(prescan['original_valid_points'])
        z_min = float(prescan['z_min'])
        z_max = float(prescan['z_max'])

        if row_count == 0 or valid_points == 0:
            raise ValueError("高度矩阵大文件采样未识别到有效数据。")

        max_safe_side = self._max_safe_grid_side(max_rows)
        requested_side = int(getattr(self, 'large_text_grid_count', 0))
        auto_side = self._auto_spatial_grid_side(valid_points, max_rows)
        if requested_side > 0:
            grid_side = min(requested_side, max_safe_side)
            grid_source = f"用户设定 {requested_side}×{requested_side}"
            if grid_side != requested_side:
                grid_source += f"，实际使用 {grid_side}×{grid_side}"
        else:
            grid_side = min(auto_side, max_safe_side)
            grid_source = f"自动 {grid_side}×{grid_side}"

        def cell_index(row_idx, col_idx):
            ix = int(col_idx / max(1, ncols - 1) * grid_side) if ncols > 1 else 0
            iy = int(row_idx / max(1, row_count - 1) * grid_side) if row_count > 1 else 0
            ix = min(max(ix, 0), grid_side - 1)
            iy = min(max(iy, 0), grid_side - 1)
            return iy * grid_side + ix

        pitch_x_mm = float(pitch_x_um) / 1000.0
        pitch_y_mm = float(pitch_y_um) / 1000.0
        cells = {}
        matrix_row = 0
        matrix_started = False
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
                self._check_cancel(cancel_event)
                if line_no < data_line_no:
                    continue
                if data_end_line_no is not None and line_no >= data_end_line_no:
                    break
                values = parse_matrix_line(line)
                if values is None:
                    if matrix_started:
                        break
                    continue
                matrix_started = True
                finite_cols = np.where(np.isfinite(values))[0]
                for col_idx in finite_cols:
                    z = float(values[col_idx])
                    x = float(col_idx) * pitch_x_mm
                    y = (float(row_count - 1 - matrix_row)) * pitch_y_mm
                    row = [x, y, z, int(matrix_row), int(col_idx)]
                    key = cell_index(matrix_row, int(col_idx))
                    state = cells.get(key)
                    if state is None:
                        cells[key] = {'first': row, 'min_row': row, 'min_z': z, 'max_row': row, 'max_z': z}
                    else:
                        if z < state['min_z']:
                            state['min_z'] = z
                            state['min_row'] = row
                        if z > state['max_z']:
                            state['max_z'] = z
                            state['max_row'] = row
                matrix_row += 1
                if progress is not None and matrix_row % 64 == 0:
                    progress(45 + int(30 * matrix_row / max(1, row_count)),
                             f"正在采样高度矩阵: {matrix_row:,}/{row_count:,} 行")

        rows = []
        for key in sorted(cells):
            state = cells[key]
            seen = set()
            for row_key in ('first', 'min_row', 'max_row'):
                row = state[row_key]
                row_id = (row[3], row[4])
                if row_id in seen:
                    continue
                seen.add(row_id)
                rows.append(row)
        if not rows:
            raise ValueError("高度矩阵空间采样未得到有效采样点。")

        df = pd.DataFrame(rows, columns=['X', 'Y', 'Z', '_matrix_row', '_matrix_col'])
        total_cells = grid_side * grid_side
        self.last_import_note = (
            f"VR/基恩士高度矩阵已按空间网格采样导入。\n"
            f"数据起始行: {data_line_no + 1} | 跳过前置说明: {data_line_no} 行\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB | 触发阈值: {self.large_text_threshold_mb} MB\n"
            f"矩阵尺寸: {row_count:,} × {ncols:,} | 有效点: {valid_points:,}\n"
            f"Pitch: X={pitch_x_um:g}µm, Y={pitch_y_um:g}µm（{meta_source}）| Z单位: {z_unit}\n"
            f"网格设置: {grid_source}，总网格 {total_cells:,}，占用网格 {len(cells):,}\n"
            f"每格保留: 首个代表点 + Z最小点 + Z最大点 | Z范围: {z_min:.6g} ~ {z_max:.6g}\n"
            f"实际导入: {len(df):,} 点。"
        )
        self.import_info.update({
            'strategy': '高度矩阵空间网格采样导入',
            'source_format': '文本Z矩阵',
            'sampled': True,
            'sample_method_key': 'spatial_grid',
            'extrema_preserved': True,
            'height_matrix': True,
            'import_rows': len(df),
            'source_valid_rows': valid_points,
            'source_matrix_positions': row_count * ncols,
            'original_valid_points': valid_points,
            'analysis_points': len(df),
            'display_points': min(len(df), self._display_limit()),
            'matrix_rows': row_count,
            'matrix_cols': ncols,
            'matrix_pitch_x_um': float(pitch_x_um),
            'matrix_pitch_y_um': float(pitch_y_um),
            'sampling_pitch_x_um': float(pitch_x_um),
            'sampling_pitch_y_um': float(pitch_y_um),
            'sampling_pitch_source': meta_source,
            'matrix_z_unit': z_unit,
            'z_source_field': '矩阵高度值',
            'z_source_unit': z_unit,
            'matrix_data_start_row': data_line_no + 1,
            'matrix_header_rows_skipped': data_line_no,
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'matrix_invalid_values': list(invalid_values),
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label('spatial_grid'),
            'grid_count': grid_side,
            'grid_cells': total_cells,
            'occupied_grid_cells': len(cells),
            'notes': f"高度矩阵 | {grid_side}×{grid_side} 网格 | Pitch {pitch_x_um:g}/{pitch_y_um:g}µm"
        })
        return df

    def _read_height_matrix_table(self, path, enc, layout, file_size,
                                  progress=None, cancel_event=None):
        metadata = dict(layout.get('matrix_metadata') or self._scan_height_matrix_metadata(
            path, enc, progress=progress, cancel_event=cancel_event))
        expected_cols = metadata.get('expected_cols')
        if expected_cols is not None and int(layout['ncols']) != int(expected_cols):
            raise ValueError(
                f"Z Matrix 列数与表头冲突：表头声明 {int(expected_cols):,} 列，"
                f"实际识别 {int(layout['ncols']):,} 列。")
        layout['expected_cols'] = int(expected_cols) if expected_cols is not None else None
        layout['expected_rows'] = metadata.get('expected_rows')
        if str(getattr(self, 'pitch_source', 'manual')) == 'manual':
            pitch_x = float(self.height_matrix_pitch_x_um)
            pitch_y = float(self.height_matrix_pitch_y_um)
            pitch_source = '用户手动'
        else:
            pitch_x = float(metadata['pitch_x_um'])
            pitch_y = float(metadata['pitch_y_um'])
            pitch_source = ('可靠文件metadata' if metadata.get('detected_fields')
                            else '用户默认')
        z_override = str(getattr(self, 'import_z_unit', 'auto'))
        z_unit = z_override if z_override != 'auto' else str(metadata['z_unit'])
        invalid_values = tuple(metadata.get('invalid_values') or ())
        meta_source = pitch_source
        if progress is not None:
            progress(20, "已识别矩阵数据区，开始完整性预扫描")
        prescan = self._prescan_height_matrix(
            path, enc, layout, invalid_values, progress, cancel_event)
        rows_count = int(prescan['matrix_rows'])
        cols_count = int(prescan['matrix_cols'])
        valid_points = int(prescan['original_valid_points'])
        auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
        point_threshold = int(getattr(self, 'matrix_analysis_threshold', 400_000))
        large_matrix = (file_size >= self._large_text_threshold_bytes()
                        or valid_points > point_threshold)
        sampled = bool(auto_sample and large_matrix)
        common_args = (
            path, enc, layout['sep'], cols_count, int(layout['data_line_no']),
            pitch_x, pitch_y, z_unit, meta_source, layout.get('data_end_line_no'),
            int(layout.get('matrix_value_start', 0)), invalid_values)
        if sampled:
            method = str(getattr(self, 'large_file_sample_method', 'file_position'))
            kwargs = {
                'prescan': prescan, 'progress': progress, 'cancel_event': cancel_event,
                'trailing_terminator': bool(layout.get('matrix_trailing_terminator', False)),
                'allow_padding': expected_cols is not None,
            }
            if method in ('file_position', 'stride'):
                frame = self._sample_large_height_matrix_by_stride(*common_args, **kwargs)
            else:
                frame = self._sample_large_height_matrix(*common_args, **kwargs)
        else:
            matrix_rows = []
            matrix_started = False
            with open(path, 'r', encoding=enc, errors='ignore') as handle:
                for line_no, line in enumerate(handle):
                    self._check_cancel(cancel_event)
                    if line_no < int(layout['data_line_no']):
                        continue
                    if layout.get('data_end_line_no') is not None and line_no >= int(layout['data_end_line_no']):
                        break
                    values = self._parse_height_matrix_line(
                        line, layout['sep'], cols_count,
                        int(layout.get('matrix_value_start', 0)), invalid_values,
                        bool(layout.get('matrix_trailing_terminator', False)),
                        expected_cols is not None)
                    if values is None:
                        if matrix_started:
                            break
                        continue
                    matrix_started = True
                    matrix_rows.append(values)
                    if progress is not None and len(matrix_rows) % 64 == 0:
                        progress(45 + int(30 * len(matrix_rows) / max(1, rows_count)),
                                 f"正在读取高度矩阵: {len(matrix_rows):,}/{rows_count:,} 行")
            z_values = np.asarray(matrix_rows, dtype=float)
            if z_values.shape != (rows_count, cols_count):
                raise ValueError(
                    f"Z Matrix 完整性校验失败：预扫描为 {rows_count}×{cols_count}，"
                    f"实际读取为 {z_values.shape[0]}×{z_values.shape[1] if z_values.ndim == 2 else 0}。")
            if progress is not None:
                progress(78, "正在生成矩阵物理坐标")
            frame = self._height_matrix_dataframe(
                z_values, rows_count, cols_count, pitch_x, pitch_y, invalid_values)
            self.import_info.update({
                'strategy': '高度矩阵全量读取', 'sampled': False,
                'sample_method_key': 'full', 'extrema_preserved': True,
            })

        analysis_points = len(frame)
        source_format = str(metadata.get('source_format') or '通用Z矩阵')
        self.import_info.update({
            'source_format': source_format,
            'height_matrix': True,
            'import_rows': analysis_points,
            'source_matrix_positions': rows_count * cols_count,
            'source_valid_rows': valid_points,
            'original_valid_points': valid_points,
            'analysis_points': analysis_points,
            'display_points': min(analysis_points, self._display_limit()),
            'matrix_rows': rows_count,
            'matrix_cols': cols_count,
            'matrix_pitch_x_um': pitch_x,
            'matrix_pitch_y_um': pitch_y,
            'sampling_pitch_x_um': pitch_x,
            'sampling_pitch_y_um': pitch_y,
            'sampling_pitch_source': meta_source,
            'matrix_z_unit': z_unit,
            'z_source_field': '矩阵高度值',
            'z_source_unit': z_unit,
            'matrix_data_start_row': int(layout['data_line_no']) + 1,
            'matrix_header_rows_skipped': int(layout['data_line_no']),
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'matrix_coordinate_header': bool(layout.get('matrix_coordinate_header')),
            'matrix_invalid_values': list(invalid_values),
            'matrix_metadata': metadata,
            'layout_candidate_count': int(layout.get('candidate_count', 1)),
            'matrix_analysis_threshold': point_threshold,
            'topology_method': ('sampled_matrix8'
                                if self.import_info.get('sample_method_key') == 'stride'
                                else ('matrix8' if self.import_info.get('sample_method_key') == 'full'
                                      else 'Delaunay/adaptive kNN')),
        })
        trigger = []
        if file_size >= self._large_text_threshold_bytes():
            trigger.append('文件大小')
        if valid_points > point_threshold:
            trigger.append('有效点数')
        self.import_info['notes'] = (
            f"{source_format} {rows_count}×{cols_count} | 有效 {valid_points:,} | "
            f"分析 {analysis_points:,} | Pitch {pitch_x:g}/{pitch_y:g}µm"
            + (f" | 采样触发: {'+'.join(trigger)}" if sampled else ""))
        self.last_import_note = (
            f"{source_format} 已{'抽样' if sampled else '全量'}导入；矩阵 {rows_count:,}×{cols_count:,}，"
            f"有效 {valid_points:,} 点，参与分析 {analysis_points:,} 点；"
            f"跳过前置说明: {int(layout['data_line_no'])} 行；"
            f"Pitch X={pitch_x:g}µm / Y={pitch_y:g}µm（{meta_source}），Z单位 {z_unit}。")
        if progress is not None:
            progress(85, "Z Matrix 解析完成，正在初始化分析数据")
        return frame

    def _infer_xyz_column_indices(self, column_names, ncols):
        """Return unambiguous semantic XYZ columns, never guess positional columns."""
        if ncols < 3:
            return None
        manual = tuple(int(getattr(self, name, 0) or 0) - 1
                       for name in ('import_x_col', 'import_y_col', 'import_z_col'))
        if all(index >= 0 for index in manual):
            if len(set(manual)) != 3 or max(manual) >= int(ncols):
                raise ValueError("手动 X/Y/Z 列号必须互不重复且位于文件列范围内。")
            return manual
        names = list(column_names or [])[:ncols]
        semantics = self._header_semantics(names)
        if not semantics['unambiguous']:
            return None
        mapping = semantics['mapping']
        return mapping['x'], mapping['y'], mapping['z']

    def _infer_pixel_column_indices(self, column_names, ncols):
        manual = tuple(int(getattr(self, name, 0) or 0) - 1
                       for name in ('import_x_col', 'import_y_col', 'import_z_col'))
        if all(index >= 0 for index in manual):
            if len(set(manual)) != 3 or max(manual) >= int(ncols):
                raise ValueError("手动 PixelX/PixelY/Z 列号必须互不重复且位于文件列范围内。")
            return manual
        semantics = self._pixel_header_semantics(list(column_names or [])[:ncols])
        if semantics['unambiguous']:
            mapping = semantics['mapping']
            return mapping['x'], mapping['y'], mapping['z']
        if int(ncols) == 3 and all(re.fullmatch(r'Col\d+', str(name))
                                   for name in list(column_names or [])[:3]):
            return 0, 1, 2
        return None

    def _read_full_delimited_text(self, path, enc, sep, ncols, column_names,
                                  data_line_no, progress=None, cancel_event=None):
        """Read one detected logical point table without requiring auxiliary fields to be numeric."""
        rows = []
        bad_rows = 0
        consumed_bytes = 0
        file_size = max(1, Path(path).stat().st_size)
        consumed_bytes = 0
        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle):
                self._check_cancel(cancel_event)
                consumed_bytes += len(line.encode(enc, errors='ignore'))
                consumed_bytes += len(line.encode(enc, errors='ignore'))
                if line_no < int(data_line_no):
                    continue
                text = line.strip().lstrip('\ufeff')
                if not text or text.startswith('#'):
                    continue
                tokens = self._trim_trailing_empty_tokens(self._split_text_line(text, sep))
                if len(tokens) > int(ncols):
                    bad_rows += 1
                    continue
                if len(tokens) < int(ncols):
                    tokens.extend([''] * (int(ncols) - len(tokens)))
                valid_record = self._looks_like_point_record_row(tokens)
                if (not valid_record and
                        getattr(self, 'input_layout_mode', 'point_table') == 'pixel_xy'):
                    valid_record = self._looks_like_pixel_record_row(tokens)
                if not valid_record:
                    bad_rows += 1
                rows.append(tokens)
                if progress is not None and len(rows) % 50000 == 0:
                    progress(min(78, 35 + int(40 * consumed_bytes / file_size)),
                             f"正在读取点表: {len(rows):,} 行")
        if not rows:
            raise ValueError("未读取到与已识别数据区匹配的点记录。")
        self.import_info['bad_rows'] = int(bad_rows)
        return pd.DataFrame(rows, columns=list(column_names))

    def _sample_large_pixel_text(self, path, enc, sep, ncols, column_names,
                                 data_line_no, progress=None, cancel_event=None):
        indices = self._infer_pixel_column_indices(column_names, ncols)
        if indices is None:
            raise ValueError(
                "Pixel XY大文件无法唯一确定 PixelX/PixelY/Z 列；请在高级解析覆盖中填写列号。")
        x_idx, y_idx, z_idx = indices
        valid_count = 0
        missing_count = 0
        min_x = min_y = max_x = max_y = None

        def parse(line):
            text = line.strip().lstrip('\ufeff')
            if not text or text.startswith('#'):
                return None
            tokens = self._split_text_line(text, sep)
            if len(tokens) < ncols:
                tokens.extend([''] * (ncols - len(tokens)))
            try:
                px = float(tokens[x_idx]); py = float(tokens[y_idx])
                z = self._token_to_float(tokens[z_idx])
            except (ValueError, IndexError):
                return None
            if (not np.isfinite(px) or not np.isfinite(py)
                    or abs(px - round(px)) > 1e-6 or abs(py - round(py)) > 1e-6):
                return None
            return tokens[:ncols], int(round(px)), int(round(py)), float(z)

        file_size = max(1, Path(path).stat().st_size)
        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle):
                self._check_cancel(cancel_event)
                if line_no < int(data_line_no):
                    continue
                parsed = parse(line)
                if parsed is None:
                    continue
                _, px, py, z = parsed
                min_x = px if min_x is None else min(min_x, px)
                min_y = py if min_y is None else min(min_y, py)
                max_x = px if max_x is None else max(max_x, px)
                max_y = py if max_y is None else max(max_y, py)
                if not np.isfinite(z):
                    missing_count += 1
                    continue
                valid_count += 1
                if progress is not None and valid_count % 100000 == 0:
                    progress(min(42, 20 + int(20 * consumed_bytes / file_size)),
                             f"正在预扫描 Pixel XY: {valid_count:,} 点")
        if valid_count < 3:
            raise ValueError("Pixel XY有效记录少于3条。")
        stride = max(1, int(np.ceil(np.sqrt(
            valid_count / max(1, self._large_text_import_limit())))))
        rows = []
        consumed_bytes = 0
        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle):
                self._check_cancel(cancel_event)
                consumed_bytes += len(line.encode(enc, errors='ignore'))
                if line_no < int(data_line_no):
                    continue
                parsed = parse(line)
                if parsed is None:
                    continue
                tokens, px, py, z = parsed
                if not np.isfinite(z):
                    continue
                if ((px - min_x) % stride or (py - min_y) % stride):
                    continue
                rows.append(tokens + [py, px, (py - min_y) // stride,
                                      (px - min_x) // stride])
                if len(rows) >= self._large_text_import_limit():
                    break
                if progress is not None and len(rows) % 50000 == 0:
                    progress(min(78, 45 + int(30 * consumed_bytes / file_size)),
                             f"正在规则采样 Pixel XY: {len(rows):,} 点")
        if len(rows) < 3:
            raise ValueError("Pixel XY规则采样后有效点少于3条，请提高导入上限。")
        columns = list(column_names) + [
            '_matrix_row', '_matrix_col', '_topology_row', '_topology_col']
        frame = pd.DataFrame(rows, columns=columns)
        self.import_info.update({
            'strategy': 'Pixel XY规则stride采样导入',
            'source_format': '通用文本Pixel XY点表',
            'sampled': True,
            'sample_method_key': 'stride',
            'extrema_preserved': False,
            'import_rows': len(frame),
            'source_valid_rows': valid_count,
            'original_valid_points': valid_count,
            'missing_points': int(missing_count),
            'matrix_rows': int(max_y - min_y + 1),
            'matrix_cols': int(max_x - min_x + 1),
            'source_matrix_positions': int((max_y - min_y + 1) * (max_x - min_x + 1)),
            'analysis_points': len(frame),
            'stride_n': stride,
            'topology_method': 'sampled_matrix8',
            'notes': f'Pixel XY规则stride N={stride} | 原始 {valid_count:,} | 分析 {len(frame):,}',
        })
        self.last_import_note = self.import_info['notes']
        return frame

    def _max_safe_grid_side(self, max_rows):
        # 每格最多保留：代表点 + Z最小点 + Z最大点。
        return max(1, int(np.floor(np.sqrt(max(1, int(max_rows)) / 3.0))))

    def _auto_spatial_grid_side(self, valid_rows, max_rows):
        target_rows = max(1, min(int(valid_rows), int(max_rows)))
        target_cells = max(1, int(np.ceil(target_rows / 3.0)))
        return max(1, int(np.ceil(np.sqrt(target_cells))))

    def _sample_large_text(self, path, enc, sep, ncols, column_names=None,
                           progress=None, cancel_event=None):
        method = getattr(self, 'large_file_sample_method', 'spatial_grid')
        if method == 'stride':
            method = 'file_position'
        if method == 'file_position':
            return self._sample_large_text_by_position(
                path, enc, sep, ncols, column_names, progress, cancel_event)
        if self._infer_xyz_column_indices(column_names, ncols) is None:
            reason = '表头无法唯一确定 X/Y/Z，已从空间网格采样降级为文件位置采样'
            self.import_info['sampling_downgraded'] = True
            self.import_info['sampling_downgrade_reason'] = reason
            if progress is None:
                self._show_status(reason, 12000)
            frame = self._sample_large_text_by_position(
                path, enc, sep, ncols, column_names, progress, cancel_event)
            self.import_info['notes'] = f"{self.import_info.get('notes', '')} | {reason}".strip(' |')
            self.last_import_note += f"\n{reason}；未静默使用前三列。"
            return frame
        return self._sample_large_text_by_spatial_grid(
            path, enc, sep, ncols, column_names, progress, cancel_event)

    def _sample_large_text_by_stride(self, path, enc, sep, ncols, column_names=None,
                                     progress=None, cancel_event=None):
        """倍率降采样：按有效数值行每 N 行取 1 行。速度快，但不保留局部 min/max。"""
        file_size = Path(path).stat().st_size
        stride = max(1, int(getattr(self, 'large_text_stride_n', 10)))
        max_rows = self._large_text_import_limit()
        rows = []
        valid_rows = 0

        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh, start=1):
                self._check_cancel(cancel_event)
                stripped = line.strip().lstrip('\ufeff')
                if not stripped or stripped.startswith('#'):
                    continue
                tokens = self._split_text_line(stripped, sep)
                if not self._looks_like_point_record_row(tokens):
                    continue
                values = list(tokens[:ncols])
                if len(values) < ncols:
                    values.extend([''] * (ncols - len(values)))
                valid_rows += 1
                if (valid_rows - 1) % stride == 0:
                    rows.append(values)
                    if len(rows) >= max_rows:
                        break
                if valid_rows % 100000 == 0:
                    if progress is not None:
                        progress(min(78, 30 + valid_rows // 100000),
                                 f"正在倍率采样: {valid_rows:,} 行")
                    else:
                        self._show_status(
                            f"倍率降采样: 已扫描 {valid_rows:,} 行 | 已取 {len(rows):,} 行 | N={stride}", 1000)
                        self._process_ui_events()

        if not rows:
            raise ValueError("倍率降采样未得到有效数值行，请检查文件格式或调小降采样倍率 N。")

        cols = column_names if column_names and len(column_names) == ncols else [f'Col{i+1}' for i in range(ncols)]
        df = pd.DataFrame(rows, columns=cols)
        self.last_import_note = (
            f"超大文本已按倍率降采样导入。\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB | 触发阈值: {self.large_text_threshold_mb} MB\n"
            f"降采样倍率: N={stride}（每 {stride} 行取 1 行）\n"
            f"扫描有效行: {valid_rows:,} | 实际导入: {len(df):,} 行 | 导入上限: {max_rows:,}\n"
            f"注意: 倍率降采样不保留每格 Z min/max，PV/TTV 可能低估；最终复核建议使用空间网格采样。"
        )
        self.import_info.update({
            'strategy': '超大文本倍率降采样导入',
            'sampled': True,
            'sample_method_key': 'stride',
            'extrema_preserved': False,
            'import_rows': len(df),
            'source_valid_rows': valid_rows,
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label('stride'),
            'stride_n': stride,
            'notes': f"{self._bigfile_mode_label()}模式 | 倍率降采样 N={stride}"
        })
        return df

    def _sample_large_text_by_position(self, path, enc, sep, ncols, column_names=None,
                                       progress=None, cancel_event=None):
        """旧版超大文本预抽样：按文件字节位置均匀抽取数据行。"""
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()
        rows = []
        seen_starts = set()

        with open(path, 'rb') as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                offsets = np.linspace(0, max(0, file_size - 1), max_rows, dtype=np.int64)
                for i, offset in enumerate(offsets):
                    self._check_cancel(cancel_event)
                    offset = int(offset)
                    if offset <= 0:
                        start = 0
                    else:
                        start = mm.find(b'\n', offset)
                        if start < 0:
                            continue
                        start += 1
                    if start in seen_starts:
                        continue
                    seen_starts.add(start)
                    end = mm.find(b'\n', start)
                    if end < 0:
                        end = file_size
                    raw = mm[start:end].strip()
                    if not raw:
                        continue
                    line = raw.decode(enc, errors='ignore').strip().lstrip('\ufeff')
                    if not line or line.startswith('#'):
                        continue
                    tokens = self._split_text_line(line, sep)
                    if not self._looks_like_point_record_row(tokens):
                        continue
                    values = list(tokens[:ncols])
                    if len(values) < ncols:
                        values.extend([''] * (ncols - len(values)))
                    rows.append(values)
                    if i % 5000 == 0:
                        if progress is not None:
                            progress(45 + int(30 * (i + 1) / max(1, max_rows)),
                                     f"正在按文件位置采样: {i + 1:,}/{max_rows:,}")
                        if progress is None:
                            self._show_status(
                                f"正在抽样导入超大TXT: {i + 1:,}/{max_rows:,} | 已取有效行 {len(rows):,}", 1000)
                            self._process_ui_events()
            finally:
                mm.close()

        if not rows:
            raise ValueError("超大文本抽样未得到有效数值行，请检查文件格式或关闭自动抽样后重试。")

        cols = column_names if column_names and len(column_names) == ncols else [f'Col{i+1}' for i in range(ncols)]
        df = pd.DataFrame(rows, columns=cols)
        self.last_import_note = (
            f"超大文本已预抽样导入，避免全量读入导致卡死。\n"
            f"策略模式: {self._bigfile_mode_label()}\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB\n"
            f"触发阈值: {self.large_text_threshold_mb} MB\n"
            f"抽样上限: {max_rows:,} 行\n"
            f"实际导入行数: {len(df):,} 行\n"
            f"抽样方式: 文件位置均匀采样。\n"
            f"缺测值标记({', '.join(sorted(self.MISSING_TEXT_TOKENS))})已按空值处理。\n"
            f"注意: 文件位置采样不保留全量极值，PV/TTV 为估计值，不可直接用于产线放行。"
        )
        self.import_info.update({
            'strategy': '超大文本文件位置采样导入',
            'sampled': True,
            'sample_method_key': 'file_position',
            'extrema_preserved': False,
            'import_rows': len(df),
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label('file_position'),
            'grid_count': 0,
            'notes': f"{self._bigfile_mode_label()}模式 | 文件位置采样 | 抽样上限 {max_rows:,} 行"
        })
        return df

    def _sample_large_text_by_spatial_grid(self, path, enc, sep, ncols, column_names=None,
                                           progress=None, cancel_event=None):
        """V3.8.3: 空间网格均匀采样。

        按 X/Y 分格，每格最多保留三类点：首个代表点、Z最小点、Z最大点。
        这样 TTV 的局部极值更不容易被采样丢掉；PV 仍以导入后的采样点参与拟合。
        """
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()
        inferred = self._infer_xyz_column_indices(column_names, ncols)
        if inferred is None:
            raise ValueError("空间网格采样需要能从表头唯一识别 X/Y/Z；当前列语义不明确。")
        x_idx, y_idx, z_idx = inferred
        cols = column_names if column_names and len(column_names) == ncols else [f'Col{i+1}' for i in range(ncols)]

        x_min = y_min = z_min = np.inf
        x_max = y_max = z_max = -np.inf
        valid_rows = 0

        def parse_numeric_line(line):
            stripped = line.strip().lstrip('\ufeff')
            if not stripped or stripped.startswith('#'):
                return None
            tokens = self._split_text_line(stripped, sep)
            if not self._looks_like_point_record_row(tokens):
                return None
            raw_values = list(tokens[:ncols])
            if len(raw_values) < ncols:
                raw_values.extend([''] * (ncols - len(raw_values)))
            numeric = [self._token_to_float(raw_values[index])
                       for index in (x_idx, y_idx, z_idx)]
            return raw_values, numeric

        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh, start=1):
                self._check_cancel(cancel_event)
                parsed = parse_numeric_line(line)
                if parsed is None:
                    continue
                _, (x, y, z) = parsed
                if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
                    continue
                valid_rows += 1
                x_min = min(x_min, x); x_max = max(x_max, x)
                y_min = min(y_min, y); y_max = max(y_max, y)
                z_min = min(z_min, z); z_max = max(z_max, z)
                if valid_rows % 100000 == 0:
                    if progress is not None:
                        progress(min(44, 25 + valid_rows // 100000),
                                 f"正在预扫描空间范围: {valid_rows:,} 点")
                    if progress is None:
                        self._show_status(
                            f"空间网格采样预扫描: 已识别 {valid_rows:,} 个有效 XYZ 点", 1000)
                        self._process_ui_events()

        if valid_rows == 0:
            raise ValueError("空间网格采样未识别到有效 XYZ 点，请检查文件列顺序/缺测值或改用文件位置采样。")

        max_safe_side = self._max_safe_grid_side(max_rows)
        requested_side = int(getattr(self, 'large_text_grid_count', 0))
        auto_side = self._auto_spatial_grid_side(valid_rows, max_rows)
        if requested_side > 0:
            grid_side = min(requested_side, max_safe_side)
            grid_source = f"用户设定 {requested_side}×{requested_side}"
            if grid_side != requested_side:
                grid_source += f"，受导入上限约束实际使用 {grid_side}×{grid_side}"
        else:
            grid_side = min(auto_side, max_safe_side)
            grid_source = f"自动 {grid_side}×{grid_side}"

        x_span = x_max - x_min
        y_span = y_max - y_min

        def cell_index(x, y):
            if x_span <= 0:
                ix = 0
            else:
                ix = int((x - x_min) / x_span * grid_side)
                ix = min(max(ix, 0), grid_side - 1)
            if y_span <= 0:
                iy = 0
            else:
                iy = int((y - y_min) / y_span * grid_side)
                iy = min(max(iy, 0), grid_side - 1)
            return iy * grid_side + ix

        cells = {}
        scanned = 0
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh, start=1):
                self._check_cancel(cancel_event)
                parsed = parse_numeric_line(line)
                if parsed is None:
                    continue
                values, (x, y, z) = parsed
                if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
                    continue
                scanned += 1
                key = cell_index(x, y)
                state = cells.get(key)
                if state is None:
                    state = {'first': values, 'min_row': values, 'min_z': z, 'max_row': values, 'max_z': z}
                    cells[key] = state
                else:
                    if z < state['min_z']:
                        state['min_z'] = z
                        state['min_row'] = values
                    if z > state['max_z']:
                        state['max_z'] = z
                        state['max_row'] = values
                if scanned % 100000 == 0:
                    if progress is not None:
                        progress(min(78, 45 + int(30 * scanned / max(1, valid_rows))),
                                 f"正在空间网格采样: {scanned:,}/{valid_rows:,}")
                    if progress is None:
                        self._show_status(
                            f"空间网格采样落格: {scanned:,}/{valid_rows:,} | 已占用网格 {len(cells):,}", 1000)
                        self._process_ui_events()

        rows = []
        for key in sorted(cells):
            state = cells[key]
            seen_ids = set()
            for row_key in ('first', 'min_row', 'max_row'):
                row = state[row_key]
                if id(row) in seen_ids:
                    continue
                seen_ids.add(id(row))
                rows.append(row)

        if not rows:
            raise ValueError("空间网格采样未得到有效采样点，请检查文件格式或改用文件位置采样。")

        df = pd.DataFrame(rows, columns=cols)
        total_cells = grid_side * grid_side
        self.last_import_note = (
            f"超大文本已按空间网格均匀采样导入，避免全量读入导致卡死。\n"
            f"策略模式: {self._bigfile_mode_label()}\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB\n"
            f"触发阈值: {self.large_text_threshold_mb} MB\n"
            f"原始有效XYZ点: {valid_rows:,} 点\n"
            f"网格设置: {grid_source}，总网格 {total_cells:,}，占用网格 {len(cells):,}\n"
            f"每格保留: 首个代表点 + Z最小点 + Z最大点\n"
            f"原始Z范围: {z_min:.6g} ~ {z_max:.6g}\n"
            f"导入上限: {max_rows:,} 行 | 实际导入: {len(df):,} 行\n"
            f"XYZ推断列: X={cols[x_idx]}, Y={cols[y_idx]}, Z={cols[z_idx]}\n"
            f"缺测值标记({', '.join(sorted(self.MISSING_TEXT_TOKENS))})已按空值处理。"
        )
        self.import_info.update({
            'strategy': '超大文本空间网格采样导入',
            'sampled': True,
            'sample_method_key': 'spatial_grid',
            'extrema_preserved': True,
            'import_rows': len(df),
            'source_valid_rows': valid_rows,
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label('spatial_grid'),
            'grid_count': grid_side,
            'grid_cells': total_cells,
            'occupied_grid_cells': len(cells),
            'notes': f"{self._bigfile_mode_label()}模式 | 空间网格 {grid_side}×{grid_side} | 占用 {len(cells):,} 格"
        })
        return df

    @staticmethod
    def _read_text_header(path, max_lines=50000):
        """Return a usable text encoding and the requested leading lines."""
        last_error = None
        for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
            try:
                lines = []
                with open(path, 'r', encoding=enc, errors='strict') as handle:
                    for _, line in zip(range(max_lines), handle):
                        lines.append(line.rstrip('\r\n'))
                return enc, lines
            except (UnicodeError, OSError) as exc:
                last_error = exc
        raise ValueError(f"无法识别文本编码: {last_error}")

    @classmethod
    def _text_format_signature(cls, path):
        """Detect only deterministic device signatures; never infer from numeric width."""
        try:
            enc, lines = cls._read_text_header(path, max_lines=12)
        except Exception:
            return None, None
        first = lines[0].strip().lstrip('\ufeff') if lines else ''
        if first == 'Zygo XYZ Data File - Format 1':
            return 'zygo_xyz_format_1', enc
        if 'Precitec Optronik' in first and 'FSS Explorer' in first:
            return 'precitec_fss', enc
        return None, enc

    @staticmethod
    def _reservoir_add(reservoir, item, seen_count, limit, rng):
        if len(reservoir) < limit:
            reservoir.append(item)
            return
        replacement = rng.randrange(seen_count)
        if replacement < limit:
            reservoir[replacement] = item

    def _read_zygo_xyz(self, path, enc, file_size, progress=None, cancel_event=None):
        """Parse Zygo XYZ Data File - Format 1 without numeric-width heuristics."""
        self._check_cancel(cancel_event)
        if progress is not None:
            progress(12, "正在读取 Zygo 表头")
        with open(path, 'r', encoding=enc, errors='strict') as handle:
            header = [handle.readline().rstrip('\r\n') for _ in range(14)]
        if not header or header[0].strip().lstrip('\ufeff') != 'Zygo XYZ Data File - Format 1':
            raise ValueError("当前文件不是 Zygo XYZ Data File - Format 1。请检查导入类型或文件内容。")

        try:
            phase_tokens = shlex.split(header[3], posix=True)
            if len(phase_tokens) < 4:
                raise ValueError
            origin_x = int(float(phase_tokens[0]))
            origin_y = int(float(phase_tokens[1]))
            phase_width = int(float(phase_tokens[2]))
            phase_height = int(float(phase_tokens[3]))
            if phase_width <= 0 or phase_height <= 0:
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise ValueError("Zygo 第4行 PhaseOrigin/PhaseWidth/PhaseHeight 解析失败。") from exc

        try:
            camera_tokens = shlex.split(header[7], posix=True)
            if len(camera_tokens) < 8:
                raise ValueError
            camera_res_m = float(camera_tokens[6])
            float(camera_tokens[7])  # 固定第8字段是时间戳，只校验，不参与分辨率识别。
            if not np.isfinite(camera_res_m) or camera_res_m <= 0:
                raise ValueError
            camera_res_um = camera_res_m * 1e6
        except (ValueError, IndexError) as exc:
            raise ValueError(
                "Zygo 第8行 CameraRes 解析失败；格式要求 CameraRes 为引号感知分词后的第7字段，时间戳为第8字段。") from exc

        if str(getattr(self, 'pitch_source', 'manual')) == 'auto':
            pitch_x = pitch_y = float(camera_res_um)
            pitch_source = 'Zygo CameraRes'
        else:
            pitch_x = float(self.height_matrix_pitch_x_um)
            pitch_y = float(self.height_matrix_pitch_y_um)
            pitch_source = '用户手动'
        mismatch_x = (pitch_source == '用户手动' and
                      abs(pitch_x - camera_res_um) / camera_res_um > 0.01)
        mismatch_y = (pitch_source == '用户手动' and
                      abs(pitch_y - camera_res_um) / camera_res_um > 0.01)
        pitch_warning = ''
        if mismatch_x or mismatch_y:
            pitch_warning = (
                f"手动采样间距 X/Y={pitch_x:g}/{pitch_y:g} µm/点与 CameraRes "
                f"{camera_res_um:.4g} µm/点相差超过1%；坐标仍按手动值生成。")

        auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
        sampled = auto_sample and file_size >= self._large_text_threshold_bytes()
        method = str(getattr(self, 'large_file_sample_method', 'file_position'))
        if method == 'stride':
            method = 'file_position'
        max_rows = self._large_text_import_limit()
        rows = []
        cells = {}
        rng = random.Random(0)
        valid_count = missing_count = bad_count = body_rows = 0
        in_body = False
        body_closed = False

        if sampled and method == 'spatial_grid':
            max_safe_side = self._max_safe_grid_side(max_rows)
            requested = int(getattr(self, 'large_text_grid_count', 0))
            auto_side = self._auto_spatial_grid_side(phase_width * phase_height, max_rows)
            grid_side = min(requested if requested > 0 else auto_side, max_safe_side)
        else:
            grid_side = 0

        def grid_key(pixel_x, pixel_y):
            ix = int((pixel_x - origin_x) / max(phase_width, 1) * grid_side)
            iy = int((pixel_y - origin_y) / max(phase_height, 1) * grid_side)
            ix = min(max(ix, 0), grid_side - 1)
            iy = min(max(iy, 0), grid_side - 1)
            return iy * grid_side + ix

        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle, start=1):
                self._check_cancel(cancel_event)
                text = line.strip().lstrip('\ufeff')
                if text == '#':
                    if not in_body:
                        in_body = True
                    else:
                        body_closed = True
                        break
                    continue
                if not in_body or not text:
                    continue
                body_rows += 1
                tokens = re.split(r'\s+', text)
                if len(tokens) >= 4 and tokens[2].lower() == 'no' and tokens[3].lower() == 'data':
                    try:
                        pixel_x = int(float(tokens[0])); pixel_y = int(float(tokens[1]))
                    except ValueError:
                        bad_count += 1
                        continue
                    missing_count += 1
                    if not sampled:
                        rows.append((body_rows, [
                            (pixel_x - origin_x) * pitch_x / 1000.0,
                            (pixel_y - origin_y) * pitch_y / 1000.0,
                            np.nan, pixel_y, pixel_x]))
                    continue
                if len(tokens) < 3:
                    bad_count += 1
                    continue
                try:
                    pixel_x = int(float(tokens[0])); pixel_y = int(float(tokens[1]))
                    z_um = float(tokens[2])
                except ValueError:
                    bad_count += 1
                    continue
                if not np.isfinite(z_um):
                    bad_count += 1
                    continue
                valid_count += 1
                values = [
                    (pixel_x - origin_x) * pitch_x / 1000.0,
                    (pixel_y - origin_y) * pitch_y / 1000.0,
                    z_um, pixel_y, pixel_x]
                item = (body_rows, values)
                if not sampled:
                    rows.append(item)
                elif method == 'file_position':
                    self._reservoir_add(rows, item, valid_count, max_rows, rng)
                else:
                    key = grid_key(pixel_x, pixel_y)
                    state = cells.get(key)
                    if state is None:
                        cells[key] = {'first': item, 'min': item, 'max': item}
                    else:
                        if z_um < state['min'][1][2]: state['min'] = item
                        if z_um > state['max'][1][2]: state['max'] = item
                if progress is not None and body_rows % 100000 == 0:
                    progress(min(78, 25 + int(
                        50 * body_rows / max(1, phase_width * phase_height))),
                        f"正在读取 Zygo 数据: {body_rows:,}/{phase_width * phase_height:,}")

        if not in_body or not body_closed:
            raise ValueError("Zygo 正文边界不完整：必须存在两个独立的 # 分隔行。")
        if valid_count < 3:
            raise ValueError("Zygo 正文有效高度点少于3个。")
        if sampled and method == 'spatial_grid':
            rows = []
            for key in sorted(cells):
                unique = {}
                for item in (cells[key]['first'], cells[key]['min'], cells[key]['max']):
                    unique[item[0]] = item
                rows.extend(unique.values())
        rows.sort(key=lambda item: item[0])
        frame = pd.DataFrame([item[1] for item in rows],
                             columns=['X', 'Y', 'Z', '_matrix_row', '_matrix_col'])
        if not sampled:
            frame['_topology_row'] = frame['_matrix_row'].to_numpy(dtype=int)
            frame['_topology_col'] = frame['_matrix_col'].to_numpy(dtype=int)
        if progress is not None:
            progress(85, "Zygo 已转换为物理坐标和像素拓扑")

        expected = phase_width * phase_height
        note_parts = [
            f"Zygo {phase_width}×{phase_height}",
            f"有效 {valid_count:,}", f"缺测 {missing_count:,}", f"坏行 {bad_count:,}",
            f"Pitch {pitch_x:g}/{pitch_y:g}µm（手动）",
            f"CameraRes {camera_res_um:.4g}µm/点",
        ]
        if pitch_warning:
            note_parts.append("Pitch偏差>1%")
        strategy = 'Zygo XYZ全量读取'
        sample_key = 'full'
        extrema = True
        if sampled and method == 'file_position':
            strategy = 'Zygo XYZ文件位置流式采样'
            sample_key = 'file_position'
            extrema = False
        elif sampled:
            strategy = 'Zygo XYZ空间网格流式采样'
            sample_key = 'spatial_grid'
        metadata = {
            'PhaseOriginX': origin_x, 'PhaseOriginY': origin_y,
            'PhaseWidth': phase_width, 'PhaseHeight': phase_height,
            'CameraRes_m_per_point': camera_res_m,
            'CameraRes_timestamp': camera_tokens[7],
        }
        self.import_info.update({
            'strategy': strategy,
            'source_format': 'Zygo XYZ Data File - Format 1',
            'sampled': sampled,
            'sample_method_key': sample_key,
            'extrema_preserved': extrema,
            'import_rows': len(frame),
            'source_valid_rows': valid_count,
            'source_record_rows': body_rows,
            'valid_rows': valid_count,
            'missing_points': missing_count,
            'bad_rows': bad_count,
            'height_matrix': False,
            'matrix_rows': phase_height,
            'matrix_cols': phase_width,
            'phase_origin_x': origin_x,
            'phase_origin_y': origin_y,
            'sampling_pitch_x_um': pitch_x,
            'sampling_pitch_y_um': pitch_y,
            'sampling_pitch_source': pitch_source,
            'matrix_pitch_x_um': pitch_x,
            'matrix_pitch_y_um': pitch_y,
            'detected_camera_res_um': camera_res_um,
            'z_source_field': 'Z height',
            'z_source_unit': 'µm',
            'metadata': metadata,
            'notes': ' | '.join(note_parts),
        })
        self.last_import_note = '；'.join(note_parts) + ('。' + pitch_warning if pitch_warning else '')
        return frame

    def _read_multi_column_excel(self, path, raw):
        """Read Excel point tables with optional metadata, comments and blank rows."""
        best = None
        run = None
        header_candidate = None
        scan_start = self._configured_start_line(self.input_layout_mode)

        def finish_run():
            nonlocal best, run
            if run is not None and len(run['rows']) >= 3:
                score = len(run['rows']) * run['ncols']
                if best is None or score > best['score']:
                    best = dict(run, score=score)
            run = None

        for row_index in range(scan_start, len(raw)):
            values = raw.iloc[row_index].tolist()
            tokens = self._trim_trailing_empty_tokens(
                ['' if pd.isna(value) else str(value).strip() for value in values])
            if not tokens or not any(tokens):
                finish_run()
                continue
            first = str(tokens[0]).strip()
            is_comment = first.startswith('#')
            if is_comment:
                comment_tokens = list(tokens)
                comment_tokens[0] = first[1:].strip()
                possible = self._header_candidate_info(comment_tokens, 'excel')
                if possible and possible['confidence'] == 'semantic':
                    possible['line_no'] = row_index
                    header_candidate = possible
                finish_run()
                continue
            if self._looks_like_point_record_row(tokens):
                same_run = run is not None and run['ncols'] == len(tokens) and row_index == run['end'] + 1
                if not same_run:
                    finish_run()
                    selected_header = None
                    if header_candidate and len(header_candidate['tokens']) == len(tokens):
                        selected_header = dict(header_candidate)
                    run = {
                        'start': row_index,
                        'end': row_index,
                        'ncols': len(tokens),
                        'rows': [tokens],
                        'header': selected_header,
                    }
                else:
                    run['rows'].append(tokens)
                    run['end'] = row_index
                header_candidate = None
                continue
            finish_run()
            possible = self._header_candidate_info(tokens, 'excel')
            if possible:
                possible['line_no'] = row_index
            header_candidate = possible
        finish_run()

        if best is None:
            raise ValueError("Excel中未找到至少连续3行、每行至少含3个数值字段的XYZ点表数据区。")
        header_info = best.get('header')
        columns = (list(header_info['tokens']) if header_info else
                   [f'Col{i+1}' for i in range(best['ncols'])])
        table_rows = []
        for row_index in range(best['start'], len(raw)):
            values = raw.iloc[row_index].tolist()[:best['ncols']]
            tokens = ['' if pd.isna(value) else str(value).strip() for value in values]
            if not any(tokens):
                continue
            if len(tokens) < best['ncols']:
                tokens.extend([''] * (best['ncols'] - len(tokens)))
            table_rows.append(tokens)
        frame = pd.DataFrame(table_rows or best['rows'], columns=columns)
        header_row = header_info.get('line_no') if header_info else None
        metadata = {}
        for row_index in range(best['start']):
            if header_row is not None and row_index == header_row:
                continue
            values = raw.iloc[row_index].tolist()
            items = [str(value).strip() for value in values if not pd.isna(value) and str(value).strip()]
            if items:
                metadata[f'HeaderLine{row_index + 1}'] = ' | '.join(items)
        self.import_info.update({
            'strategy': 'Excel点表数据区读取',
            'source_format': 'Excel XYZ点表',
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'import_rows': len(frame),
            'header_source_line': (header_row + 1) if header_row is not None else None,
            'header_confidence': header_info['confidence'] if header_info else 'generated',
            'header_source': 'excel_multi_column',
            'header_auto_mapping': dict(header_info.get('mapping', {})) if header_info else {},
            'header_unit_hints': dict(header_info.get('unit_hints', {})) if header_info else {},
            'metadata': metadata,
            'preamble_rows_skipped': int(best['start']),
            'notes': f"Excel数据区第 {best['start'] + 1} 行开始 | 表头 {header_info['confidence'] if header_info else 'generated'}",
        })
        self.last_import_note = (
            f"Excel点表从第 {best['start'] + 1} 行开始；"
            f"表头来源：{('第 ' + str(header_row + 1) + ' 行') if header_row is not None else '自动生成'}。")
        return frame

    @staticmethod
    def _precitec_column_index(columns, expected):
        target = re.sub(r'\s+', ' ', expected.strip()).casefold()
        for index, column in enumerate(columns):
            value = re.sub(r'\s+', ' ', str(column).strip()).casefold()
            if value == target:
                return index
        raise ValueError(f"Precitec字段缺失: {expected}")

    def _read_precitec_fss(self, path, enc, file_size, progress=None, cancel_event=None):
        """Parse Precitec FSS Explorer semicolon point tables and retain all columns."""
        self._check_cancel(cancel_event)
        if progress is not None:
            progress(12, "正在扫描 Precitec 表头和扫描参数")
        header_line_no = None
        columns = None
        preamble = []
        with open(path, 'r', encoding=enc, errors='ignore') as handle:
            for line_no, line in enumerate(handle, start=1):
                self._check_cancel(cancel_event)
                text = line.strip().lstrip('\ufeff')
                if text.startswith('#Encoder V;'):
                    columns = self._trim_trailing_empty_tokens(
                        [token.strip() for token in text[1:].split(';')])
                    header_line_no = line_no
                    break
                if text:
                    preamble.append(text)
                if line_no >= 50000:
                    break
        if not columns or header_line_no is None:
            raise ValueError("已识别 Precitec FSS 文件，但未找到 '#Encoder V;...' 字段行。")
        columns = self._dedupe_header_tokens(columns)
        x_idx = self._precitec_column_index(columns, 'X Pos [mm]')
        y_idx = self._precitec_column_index(columns, 'Y Pos [mm]')
        z_idx = self._precitec_column_index(columns, 'Thickness 1')

        header_text = '\n'.join(preamble)
        points_match = re.search(r'PointsPerLine\s*:\s*(\d+)', header_text, re.I)
        lines_match = re.search(r'NumberOfLines\s*:\s*(\d+)', header_text, re.I)
        points_per_line = int(points_match.group(1)) if points_match else None
        number_of_lines = int(lines_match.group(1)) if lines_match else None
        expected_points = (points_per_line * number_of_lines
                           if points_per_line is not None and number_of_lines is not None else None)

        def iter_records():
            record_index = 0
            with open(path, 'r', encoding=enc, errors='ignore') as handle:
                for line_no, line in enumerate(handle, start=1):
                    self._check_cancel(cancel_event)
                    if line_no <= header_line_no:
                        continue
                    text = line.strip()
                    if not text or text.startswith('#'):
                        continue
                    current_index = record_index
                    record_index += 1
                    if progress is not None and record_index % 100000 == 0:
                        denominator = max(1, expected_points or record_index + 1)
                        progress(min(78, 25 + int(50 * record_index / denominator)),
                                 f"正在读取 Precitec 数据: {record_index:,}")
                    tokens = self._trim_trailing_empty_tokens(
                        [token.strip() for token in text.split(';')])
                    if len(tokens) != len(columns):
                        yield current_index, line_no, None
                        continue
                    try:
                        xyz = (float(tokens[x_idx]), float(tokens[y_idx]), float(tokens[z_idx]))
                    except ValueError:
                        yield current_index, line_no, None
                        continue
                    if not all(np.isfinite(value) for value in xyz):
                        yield current_index, line_no, None
                        continue
                    yield current_index, line_no, tokens

        auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
        sampled = auto_sample and file_size >= self._large_text_threshold_bytes()
        method = str(getattr(self, 'large_file_sample_method', 'file_position'))
        if method == 'stride': method = 'file_position'
        max_rows = self._large_text_import_limit()
        rows = []
        valid_count = bad_count = source_rows = 0
        rng = random.Random(0)
        topology_rows = {}

        def observe_topology(record_index, tokens):
            if points_per_line is None or points_per_line <= 0 or tokens is None:
                return
            row_no, raw_col = divmod(int(record_index), int(points_per_line))
            state = topology_rows.setdefault(row_no, {
                'first': None, 'last': None, 'sum_x': 0.0, 'sum_y': 0.0,
                'count': 0, 'steps': [], 'previous': None,
            })
            x = float(tokens[x_idx]); y = float(tokens[y_idx])
            point = (raw_col, x, y)
            if state['first'] is None or raw_col < state['first'][0]:
                state['first'] = point
            if state['last'] is None or raw_col > state['last'][0]:
                state['last'] = point
            if state['previous'] is not None and raw_col == state['previous'][0] + 1:
                step = float(np.hypot(x - state['previous'][1], y - state['previous'][2]))
                if np.isfinite(step) and step > 0:
                    state['steps'].append(step)
            state['previous'] = point
            state['sum_x'] += x; state['sum_y'] += y; state['count'] += 1

        if sampled and method == 'spatial_grid':
            x_min = y_min = np.inf
            x_max = y_max = -np.inf
            for record_index, _, tokens in iter_records():
                source_rows += 1
                if tokens is None:
                    bad_count += 1
                    continue
                valid_count += 1
                observe_topology(record_index, tokens)
                x = float(tokens[x_idx]); y = float(tokens[y_idx])
                x_min = min(x_min, x); x_max = max(x_max, x)
                y_min = min(y_min, y); y_max = max(y_max, y)
            if valid_count < 3:
                raise ValueError("Precitec有效 X/Y/Thickness 记录少于3条。")
            max_safe_side = self._max_safe_grid_side(max_rows)
            requested = int(getattr(self, 'large_text_grid_count', 0))
            grid_side = min(requested if requested > 0 else self._auto_spatial_grid_side(valid_count, max_rows),
                            max_safe_side)
            x_span = x_max - x_min; y_span = y_max - y_min
            cells = {}
            for record_index, line_no, tokens in iter_records():
                if tokens is None: continue
                x = float(tokens[x_idx]); y = float(tokens[y_idx]); z = float(tokens[z_idx])
                ix = 0 if x_span <= 0 else min(max(int((x - x_min) / x_span * grid_side), 0), grid_side - 1)
                iy = 0 if y_span <= 0 else min(max(int((y - y_min) / y_span * grid_side), 0), grid_side - 1)
                key = iy * grid_side + ix
                item = (record_index, line_no, tokens)
                state = cells.get(key)
                if state is None:
                    cells[key] = {'first': item, 'min': item, 'max': item}
                else:
                    if z < float(state['min'][2][z_idx]): state['min'] = item
                    if z > float(state['max'][2][z_idx]): state['max'] = item
            for key in sorted(cells):
                unique = {}
                for item in (cells[key]['first'], cells[key]['min'], cells[key]['max']):
                    unique[item[0]] = item
                rows.extend(unique.values())
        else:
            for record_index, line_no, tokens in iter_records():
                source_rows += 1
                if tokens is None:
                    bad_count += 1
                    continue
                valid_count += 1
                observe_topology(record_index, tokens)
                item = (record_index, line_no, tokens)
                if not sampled:
                    rows.append(item)
                else:
                    self._reservoir_add(rows, item, valid_count, max_rows, rng)

        if valid_count < 3:
            raise ValueError("Precitec有效 X/Y/Thickness 记录少于3条。")
        rows.sort(key=lambda item: item[0])
        frame = pd.DataFrame([item[2] for item in rows], columns=columns)

        topology_valid = False
        topology_reason = ''
        serpentine_rows = set()
        local_steps = []
        line_centers = []
        reference_vector = None
        if points_per_line and number_of_lines and topology_rows:
            for row_no in sorted(topology_rows):
                state = topology_rows[row_no]
                first, last = state['first'], state['last']
                if first is not None and last is not None and last[0] > first[0]:
                    vector = np.array([last[1] - first[1], last[2] - first[2]], dtype=float)
                    if np.linalg.norm(vector) > 0 and reference_vector is None:
                        reference_vector = vector
                    if reference_vector is not None and np.dot(vector, reference_vector) < 0:
                        serpentine_rows.add(int(row_no))
                local_steps.extend(state['steps'])
                if state['count']:
                    line_centers.append((row_no, state['sum_x'] / state['count'], state['sum_y'] / state['count']))
            reasons = []
            if source_rows != expected_points:
                reasons.append('记录数与表头尺寸不一致')
            if max(topology_rows) >= number_of_lines:
                reasons.append('记录行号超出 NumberOfLines')
            if reference_vector is None:
                reasons.append('无法识别扫描线方向')
            steps = np.asarray(local_steps, dtype=float)
            if steps.size:
                median_step = float(np.median(steps))
                p95_step = float(np.percentile(steps, 95))
                if median_step <= 0 or p95_step > median_step * 12.0:
                    reasons.append('行内连续性异常')
            else:
                median_step = 0.0
                reasons.append('缺少连续行内点')
            if len(line_centers) >= 2:
                centers = np.asarray([[item[1], item[2]] for item in line_centers], dtype=float)
                center_steps = np.linalg.norm(np.diff(centers, axis=0), axis=1)
                center_steps = center_steps[np.isfinite(center_steps) & (center_steps > 0)]
                if center_steps.size == 0:
                    reasons.append('扫描线之间没有物理分离')
            else:
                reasons.append('有效扫描线不足2条')
            topology_valid = not reasons
            topology_reason = '验证通过' if topology_valid else '；'.join(reasons)
            matrix_rows = []
            matrix_cols = []
            topology_rows_out = []
            topology_cols_out = []
            for record_index, _, _ in rows:
                row_no, raw_col = divmod(int(record_index), int(points_per_line))
                col_no = points_per_line - 1 - raw_col if row_no in serpentine_rows else raw_col
                matrix_rows.append(row_no)
                matrix_cols.append(col_no)
                topology_rows_out.append(row_no)
                topology_cols_out.append(col_no)
            frame['_matrix_row'] = matrix_rows
            frame['_matrix_col'] = matrix_cols
            if topology_valid and not sampled:
                frame['_topology_row'] = topology_rows_out
                frame['_topology_col'] = topology_cols_out
        else:
            median_step = 0.0
            topology_reason = '缺少 PointsPerLine/NumberOfLines，回退普通点云拓扑'
        incomplete = expected_points is not None and source_rows != expected_points
        completeness_warning = ''
        if incomplete:
            completeness_warning = f"预期 {expected_points:,} 条，实际数据记录 {source_rows:,} 条"
        strategy = 'Precitec FSS全量读取'
        sample_key = 'full'; extrema = True
        if sampled and method == 'file_position':
            strategy = 'Precitec FSS文件位置流式采样'; sample_key = 'file_position'; extrema = False
        elif sampled:
            strategy = 'Precitec FSS空间网格流式采样'; sample_key = 'spatial_grid'
        metadata = {f'HeaderLine{i + 1}': value for i, value in enumerate(preamble)}
        if points_per_line is not None: metadata['PointsPerLine'] = points_per_line
        if number_of_lines is not None: metadata['NumberOfLines'] = number_of_lines
        notes = [f"Precitec FSS", f"有效 {valid_count:,}", f"坏行 {bad_count:,}"]
        if expected_points is not None:
            notes.append(f"预期/实际 {expected_points:,}/{source_rows:,}")
        if incomplete: notes.append('完整性警告')
        self.import_info.update({
            'strategy': strategy,
            'source_format': 'Precitec FSS Explorer SCAN PATH DATA',
            'sampled': sampled,
            'sample_method_key': sample_key,
            'extrema_preserved': extrema,
            'import_rows': len(frame),
            'source_valid_rows': valid_count,
            'source_record_rows': source_rows,
            'valid_rows': valid_count,
            'missing_points': 0,
            'bad_rows': bad_count,
            'expected_points': expected_points,
            'points_per_line': points_per_line,
            'number_of_lines': number_of_lines,
            'completeness_warning': completeness_warning,
            'mapping_x_col': columns[x_idx],
            'mapping_y_col': columns[y_idx],
            'mapping_z_col': columns[z_idx],
            'z_source_field': columns[z_idx],
            'z_source_unit': 'µm',
            'sampling_pitch_source': '不适用（物理坐标列）',
            'precitec_topology_valid': bool(topology_valid),
            'precitec_topology_usable': bool(topology_valid and not sampled),
            'precitec_topology_reason': topology_reason,
            'precitec_serpentine_rows': len(serpentine_rows),
            'precitec_local_spacing_mm': float(median_step),
            'topology_method': ('matrix8' if topology_valid and not sampled
                                else 'Delaunay/adaptive kNN'),
            'header_source_line': int(header_line_no),
            'header_confidence': 'semantic',
            'header_source': 'precitec',
            'header_auto_mapping': {'x': x_idx, 'y': y_idx, 'z': z_idx},
            'header_unit_hints': {'x': 'mm', 'y': 'mm', 'z': 'µm'},
            'metadata': metadata,
            'notes': ' | '.join(notes),
        })
        notes.append(f"拓扑: {topology_reason}")
        self.import_info['notes'] = ' | '.join(notes)
        self.last_import_note = '；'.join(notes)
        if completeness_warning:
            self.last_import_note += f"。完整性警告：{completeness_warning}。"
        return frame

    def _read_table(self, path, progress=None, cancel_event=None):
        """鲁棒读取表格文件：
        - 文本类(.csv/.txt/.tsv/.dat/.asc/.xyz): 自动尝试 utf-8-sig/gbk/utf-16/latin-1；自动识别分隔符；
          自动跳过#注释行、空行、坏行；识别无表头/普通表头/Zeiss类复杂头。
        - 超过设定阈值的文本文件可在 pandas 全量读入前预抽样，默认使用文件位置均匀采样。
          也可切换到空间网格采样，每格保留代表点、Z最小点和Z最大点。
        - Excel类(.xlsx/.xls/.xlsm): pd.read_excel，不做预抽样。
        """
        self.last_import_note = ""
        self._reset_import_info(path)
        self._check_cancel(cancel_event)
        if progress is not None:
            progress(5, "正在识别文件类型和编码")
        suffix = Path(path).suffix.lower()
        file_size = Path(path).stat().st_size
        layout_mode = getattr(self, 'input_layout_mode', 'point_table')
        if layout_mode not in ('point_table', 'pixel_xy', 'height_matrix', 'zygo_xyz'):
            layout_mode = 'point_table'
        self.import_info['input_layout_mode'] = layout_mode

        signature = None
        signature_encoding = None
        if suffix in self.TEXT_SUFFIXES or suffix == '':
            signature, signature_encoding = self._text_format_signature(path)
        if layout_mode == 'zygo_xyz':
            if suffix not in self.TEXT_SUFFIXES and suffix != '':
                raise ValueError("Zygo XYZ 导入仅支持文本类文件。")
            if signature != 'zygo_xyz_format_1':
                raise ValueError(
                    "当前选择了 Zygo XYZ，但文件首行不是 'Zygo XYZ Data File - Format 1'。\n"
                    "请核对文件，或在导入策略中切换为 XYZ 点表。")
            df = self._read_zygo_xyz(
                path, signature_encoding, file_size, progress=progress,
                cancel_event=cancel_event)
            self.import_info['display_limit'] = self._display_limit()
            self._update_import_status_label()
            return df
        if signature == 'zygo_xyz_format_1' and layout_mode == 'pixel_xy':
            df = self._read_zygo_xyz(
                path, signature_encoding, file_size, progress=progress,
                cancel_event=cancel_event)
            self.import_info['input_layout_mode'] = 'pixel_xy'
            self.import_info['display_limit'] = self._display_limit()
            return df
        if signature == 'zygo_xyz_format_1':
            raise ValueError(
                "检测到 Zygo XYZ Data File - Format 1。\n"
                "为避免错误坐标，请在“导入策略”中选择“Pixel XY / 像素XY”或兼容的“Zygo XYZ”。")
        if signature == 'precitec_fss' and layout_mode != 'point_table':
            raise ValueError(
                "检测到 Precitec FSS Explorer 点表。请在“导入策略”中选择“XYZ 点表”后重新导入。")
        if signature == 'precitec_fss':
            df = self._read_precitec_fss(
                path, signature_encoding, file_size, progress=progress,
                cancel_event=cancel_event)
            self.import_info['display_limit'] = self._display_limit()
            self._update_import_status_label()
            return df

        if suffix in self.EXCEL_SUFFIXES:
            self._check_cancel(cancel_event)
            if progress is not None:
                progress(12, "正在读取 Excel 工作表")
            raw_excel = pd.read_excel(path, header=None, dtype=object)
            nonempty_excel = raw_excel.dropna(axis=1, how='all')
            if layout_mode == 'height_matrix':
                df = self._read_excel_height_matrix(
                    path, raw_excel, progress=progress, cancel_event=cancel_event)
                self.import_info['display_limit'] = self._display_limit()
                if progress is None:
                    self._update_import_status_label()
                return df
            if nonempty_excel.shape[1] == 1:
                df = self._read_packed_single_column_excel(path, raw_excel)
            else:
                df = self._read_multi_column_excel(path, raw_excel)
        elif suffix in self.TEXT_SUFFIXES or suffix == '':
            last_err = None
            df = None
            layout = None

            for enc in self._encoding_candidates():
                try:
                    self._check_cancel(cancel_event)
                    manual_start = self._configured_start_line(layout_mode)
                    matrix_metadata = None
                    if layout_mode == 'height_matrix':
                        matrix_metadata = self._scan_height_matrix_metadata(
                            path, enc, progress=progress, cancel_event=cancel_event)
                        manual_cols = int(getattr(self, 'height_matrix_cols', 0) or 0)
                        if manual_cols > 0:
                            matrix_metadata['expected_cols'] = manual_cols
                            matrix_metadata['detected_fields'].append('手动列数')
                        manual_rows = int(getattr(self, 'height_matrix_rows', 0) or 0)
                        if manual_rows > 0:
                            matrix_metadata['expected_rows'] = manual_rows
                            matrix_metadata['detected_fields'].append('手动行数')
                    layout = self._detect_text_layout(
                        path, enc, start_line_no=manual_start, layout_mode=layout_mode,
                        matrix_metadata=matrix_metadata, progress=progress,
                        cancel_event=cancel_event, forced_sep=self._delimiter_override())
                    if layout is not None:
                        if matrix_metadata is not None:
                            layout['matrix_metadata'] = matrix_metadata
                        break
                except TaskCancelled:
                    raise
                except Exception as e:
                    last_err = e
                    layout = None

            if layout is not None:
                enc = layout['encoding']
                sep = layout['sep']
                ncols = layout['ncols']
                col_names = layout['header_tokens'] if layout['header_tokens'] else [f'Col{i+1}' for i in range(ncols)]
                if layout_mode == 'height_matrix':
                    if not self._looks_like_height_matrix_layout(path, enc, layout):
                        raise ValueError(
                            "当前导入策略选择了Z矩阵，但文件中未找到稳定的二维数值矩阵区。\n"
                            "请检查数据起始行，或在文件导入策略中切换为XYZ点表。")
                    df = self._read_height_matrix_table(
                        path, enc, layout, file_size, progress, cancel_event)
                    self.import_info['display_limit'] = self._display_limit()
                    if progress is None:
                        self._update_import_status_label()
                    return df
                text_metadata = self._extract_text_preamble_metadata(
                    path, enc, layout['data_line_no'], layout.get('header_line_no'))
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    if layout_mode == 'pixel_xy':
                        df = self._sample_large_pixel_text(
                            path, enc, sep, ncols, col_names,
                            layout['data_line_no'], progress, cancel_event)
                    else:
                        df = self._sample_large_text(
                            path, enc, sep, ncols, col_names, progress, cancel_event)
                else:
                    df = self._read_full_delimited_text(
                        path, enc, sep, ncols, col_names, layout['data_line_no'],
                        progress=progress, cancel_event=cancel_event)
                    self.import_info.update({
                        'strategy': '文本全量读取',
                        'source_format': ('通用文本Pixel XY点表'
                                          if layout_mode == 'pixel_xy'
                                          else '通用文本XYZ点表'),
                        'sampled': False,
                        'sample_method_key': 'full',
                        'extrema_preserved': True,
                        'import_rows': len(df),
                        'notes': f"编码 {enc}"
                    })
                self.import_info['metadata'] = text_metadata
                self.import_info['preamble_rows_skipped'] = int(layout['data_line_no'])
                self.import_info['header_source_line'] = (
                    int(layout['header_line_no']) + 1 if layout.get('header_line_no') is not None else None)
                self.import_info['header_confidence'] = layout.get('header_confidence', 'generated')
                self.import_info['header_source'] = 'text'
                self.import_info['header_auto_mapping'] = dict(layout.get('header_mapping', {}))
                self.import_info['header_unit_hints'] = dict(layout.get('header_unit_hints', {}))
                if text_metadata:
                    self.import_info['notes'] += f" | 前置参数 {len(text_metadata)} 项"
            else:
                if layout_mode == 'height_matrix':
                    if isinstance(last_err, ValueError) and "无法唯一确定 Z Matrix 列宽" in str(last_err):
                        raise last_err
                    raise ValueError(
                        "当前导入策略选择了Z矩阵，但前50000行未找到连续二维数值区。\n"
                        "请检查矩阵数据起始行或切换导入类型。")
                # 回退到 pandas 嗅探；不建议用于超大未知格式文件，因此超过阈值时给出明确提示。
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    raise ValueError("文件超过超大文本阈值，但前5000行未识别到有效数值数据行；\n"
                                     "为避免全量读入卡死，已停止导入。请检查Zeiss TXT头部格式，或关闭自动抽样后重试。")
                fallback_seps = ((self._delimiter_override(),)
                                 if self._delimiter_override() is not None
                                 else (None, ',', '\t', ';', '；', '|', r'\s+'))
                for enc in self._encoding_candidates():
                    for sep in fallback_seps:
                        try:
                            df_try = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                                 comment='#', skip_blank_lines=True, on_bad_lines='skip',
                                                 na_values=list(self.MISSING_TEXT_TOKENS),
                                                 keep_default_na=True)
                            if df_try.shape[1] >= 2:
                                df = df_try
                                self.import_info.update({
                                    'strategy': '文本全量读取(回退嗅探)',
                                    'source_format': ('通用文本Pixel XY点表'
                                                      if layout_mode == 'pixel_xy'
                                                      else '通用文本XYZ点表'),
                                    'sampled': False,
                                    'sample_method_key': 'full',
                                    'extrema_preserved': True,
                                    'import_rows': len(df),
                                    'notes': f"编码 {enc}"
                                })
                                break
                        except Exception as e:
                            last_err = e
                    if df is not None:
                        break
                if df is None:
                    raise ValueError(f"文本解析失败（已尝试 utf-8/gbk/utf-16/latin-1 编码与常见分隔符）: {last_err}")
        else:
            raise ValueError(f"不支持的文件格式: {suffix}\n"
                             f"支持: {', '.join(self.TEXT_SUFFIXES + self.EXCEL_SUFFIXES)}")

        # 清理列名: 去 BOM、去首尾空白
        df.columns = self._dedupe_header_tokens(
            [str(c).replace('\ufeff', '').strip() for c in df.columns])

        # 如果列名仍像数字，说明第一行可能是数据；统一改为 Col1..ColN
        def _is_num(s):
            try:
                float(str(s)); return True
            except (TypeError, ValueError):
                return False
        if df.shape[1] >= 2 and all(_is_num(c) for c in df.columns):
            df.columns = [f'Col{i+1}' for i in range(df.shape[1])]

        if df.empty or df.shape[1] < 2:
            raise ValueError("文件内容为空或有效列少于 2 列，请检查文件。")

        self.import_info['import_rows'] = len(df)
        self.import_info['display_limit'] = self._display_limit()
        self.import_info.setdefault(
            'input_semantics',
            {'point_table': 'xyz_physical', 'pixel_xy': 'pixel_xy',
             'height_matrix': 'height_matrix', 'zygo_xyz': 'pixel_xy'}.get(
                 layout_mode, 'xyz_physical'))
        self._check_cancel(cancel_event)
        if progress is not None:
            progress(88, "文件解析完成，正在标准化坐标与列映射")
        self._update_import_status_label()
        return df

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "载入数据", "",
            "Data (*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm);;All Files (*)")
        if not path:
            return False
        return self.load_path(path)

    def load_path(self, path, _parsed_payload=None):
        """Load a known path; used by both the file dialog and platform integration."""
        path = str(Path(path).expanduser().resolve())
        if (_parsed_payload is None
                and self.isVisible() and self._task_thread is None):
            previous_info = copy.deepcopy(getattr(self, 'import_info', {}))
            previous_note = str(getattr(self, 'last_import_note', ''))

            def restore_previous():
                self.import_info = previous_info
                self.last_import_note = previous_note
                self._update_import_status_label()

            def task(progress, cancel_event):
                frame = self._read_table(path, progress, cancel_event)
                return {
                    'frame': frame,
                    'import_info': copy.deepcopy(self.import_info),
                    'last_import_note': str(self.last_import_note),
                }

            def success(payload):
                self.load_path(path, _parsed_payload=payload)
                self._on_task_progress(100, "文件导入、标准化与首次分析完成")

            def failure(message):
                restore_previous()
                QMessageBox.critical(self, "导入失败", message)

            def cancelled():
                restore_previous()
                self._show_status("文件导入已取消，已保留此前有效数据。", 5000)

            return self._run_background_task(
                "文件导入", task, success, failure, on_cancel=cancelled)
        try:
            if _parsed_payload is None:
                self.absolute_raw_df = self._read_table(path)
            else:
                self.absolute_raw_df = _parsed_payload['frame']
                self.import_info = copy.deepcopy(_parsed_payload['import_info'])
                self.last_import_note = str(_parsed_payload['last_import_note'])

            self.current_source_name = Path(path).name
            self.lbl_source.setText(f"当前数据: {self.current_source_name}")

            cols = [str(c) for c in self.absolute_raw_df.columns]
            for cb in [self.cb_x_col, self.cb_y_col, self.cb_z_col]:
                cb.blockSignals(True); cb.clear(); cb.addItems(cols); cb.blockSignals(False)

            mapping_required = False
            manual_indices = tuple(int(getattr(self, name, 0) or 0) - 1
                                   for name in ('import_x_col', 'import_y_col', 'import_z_col'))
            manual_mapping = all(index >= 0 for index in manual_indices)
            if manual_mapping:
                if len(set(manual_indices)) != 3 or max(manual_indices) >= len(cols):
                    raise ValueError("手动 X/Y/Z 列号必须互不重复且位于文件列范围内。")
                self.cb_x_col.setCurrentIndex(manual_indices[0])
                self.cb_y_col.setCurrentIndex(manual_indices[1])
                self.cb_z_col.setCurrentIndex(manual_indices[2])
            elif (self.import_info.get('height_matrix') or
                    self.import_info.get('source_format') == 'Zygo XYZ Data File - Format 1') \
                    and all(c in cols for c in ('X', 'Y', 'Z')):
                self.cb_x_col.setCurrentText('X')
                self.cb_y_col.setCurrentText('Y')
                self.cb_z_col.setCurrentText('Z')
                self.cb_x_unit.setCurrentText('mm')
                self.cb_y_unit.setCurrentText('mm')
                if self.import_info.get('source_format') == 'Zygo XYZ Data File - Format 1':
                    self.cb_z_unit.setCurrentText('µm')
                else:
                    self.cb_z_unit.setCurrentText(self.import_info.get('matrix_z_unit', self.height_matrix_z_unit))
            elif self.import_info.get('source_format') == 'Precitec FSS Explorer SCAN PATH DATA':
                self.cb_x_col.setCurrentText(self.import_info['mapping_x_col'])
                self.cb_y_col.setCurrentText(self.import_info['mapping_y_col'])
                self.cb_z_col.setCurrentText(self.import_info['mapping_z_col'])
                self.cb_x_unit.setCurrentText('mm')
                self.cb_y_unit.setCurrentText('mm')
                self.cb_z_unit.setCurrentText('µm')
            elif (len(cols) == 3 and
                  all(re.fullmatch(r'Col\d+', c) for c in cols)):
                # 仅无表头且恰好3列允许稳定的位置默认。
                self.cb_x_col.setCurrentIndex(0)
                self.cb_y_col.setCurrentIndex(1)
                self.cb_z_col.setCurrentIndex(2)
            else:
                semantic = (self._pixel_header_semantics(cols)
                            if self.input_layout_mode == 'pixel_xy'
                            else self._header_semantics(cols))
                if semantic['unambiguous']:
                    mapping = semantic['mapping']
                    self.cb_x_col.setCurrentIndex(mapping['x'])
                    self.cb_y_col.setCurrentIndex(mapping['y'])
                    self.cb_z_col.setCurrentIndex(mapping['z'])
                    units = semantic.get('unit_hints', {})
                    if units.get('x'): self.cb_x_unit.setCurrentText(units['x'])
                    if units.get('y'): self.cb_y_unit.setCurrentText(units['y'])
                    if units.get('z'): self.cb_z_unit.setCurrentText(units['z'])
                else:
                    # 自定义或无表头多列只保留列名，禁止静默猜前三列。
                    self.cb_x_col.setCurrentIndex(0)
                    self.cb_y_col.setCurrentIndex(0)
                    self.cb_z_col.setCurrentIndex(0)
                    mapping_required = True

            override_units = {
                'x': str(getattr(self, 'import_x_unit', 'auto')),
                'y': str(getattr(self, 'import_y_unit', 'auto')),
                'z': str(getattr(self, 'import_z_unit', 'auto')),
            }
            for axis, combo in (('x', self.cb_x_unit), ('y', self.cb_y_unit),
                                ('z', self.cb_z_unit)):
                if override_units[axis] != 'auto':
                    combo.setCurrentText(override_units[axis])
            self.import_info['auto_mapping_result'] = {
                'x': self.cb_x_col.currentText(),
                'y': self.cb_y_col.currentText(),
                'z': self.cb_z_col.currentText(),
            }
            if mapping_required and self.pending_recipe is None:
                self.import_info['mapping_required'] = True
                self.df_raw = None
                self.manual_mask = None
                self.active_idx = None
                self.last_metrics = None
                self.current_coeffs = None
                self.high_order_models = {}
                self._clear_result_labels()
                self._update_import_status_label()
                self._show_status(
                    "列语义不明确：已保留真实列名，请选择 X/Y/Z 后点击“应用映射”。", 15000)
                QMessageBox.information(
                    self, "需要列映射",
                    "文件已读取，但无法唯一确定 X/Y/Z 列。\n"
                    "为避免错误量测，软件没有猜测前三列。请在数据解析映射区选择后应用。")
            elif self.pending_recipe is not None:
                units = self.pending_recipe.get('units', {}) or {}
                self._safe_set_combo_text(self.cb_x_unit, units.get('x_unit'))
                self._safe_set_combo_text(self.cb_y_unit, units.get('y_unit'))
                self._safe_set_combo_text(self.cb_z_unit, units.get('z_unit'))
                mapping = self.pending_recipe.get('column_mapping', {}) or {}
                self._safe_set_combo_text(self.cb_x_col, mapping.get('x_col'))
                self._safe_set_combo_text(self.cb_y_col, mapping.get('y_col'))
                self._safe_set_combo_text(self.cb_z_col, mapping.get('z_col'))
                self.apply_mapping(preserve_analysis_settings=True)
                self.apply_recipe(self.pending_recipe, path_hint='已随当前文件自动应用', remap_current_data=False)
            else:
                self.apply_mapping()
            self._update_import_status_label()
            self._remember_recent_file(path)
            if self.last_import_note:
                self._show_status(self.last_import_note.replace('\n', ' | '), 15000)
                self.btn_bigfile_settings.setToolTip(
                    "设置XYZ点表/Z矩阵/Zygo XYZ布局、超大文本预抽样、采样间距和显示上限。\n\n"
                    f"最近导入说明：\n{self.last_import_note}")

            # 寄存器保留提示（多层流程需要跨文件保留，故不自动清空）
            if any(s is not None for s in (self.data_stack, self.data_base1, self.data_base2)):
                self._show_status(
                    "提示: 多层寄存器仍保留之前的数据，如属不同物料请到[多层]页点击[清空全部寄存器]。", 10000)
            return True
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return False

    def apply_mapping(self, preserve_analysis_settings=False):
        if self.absolute_raw_df is None:
            self._show_status("当前无原始文件可映射（Gap 结果状态下映射已锁定）", 5000)
            return
        try:
            xc, yc, zc = self.cb_x_col.currentText(), self.cb_y_col.currentText(), self.cb_z_col.currentText()
            for name, col in (("X", xc), ("Y", yc), ("Z", zc)):
                if col not in self.absolute_raw_df.columns:
                    raise ValueError(f"{name}列 '{col}' 不在文件列中，请重新选择列映射。")
            generic_pixel = (
                getattr(self, 'input_layout_mode', 'point_table') == 'pixel_xy'
                and self.import_info.get('source_format') != 'Zygo XYZ Data File - Format 1')
            temp_df = pd.DataFrame()
            temp_df['X'] = pd.to_numeric(self.absolute_raw_df[xc], errors='coerce')
            temp_df['Y'] = pd.to_numeric(self.absolute_raw_df[yc], errors='coerce')
            temp_df['Z'] = pd.to_numeric(self.absolute_raw_df[zc], errors='coerce')
            if '_matrix_row' in self.absolute_raw_df.columns and '_matrix_col' in self.absolute_raw_df.columns:
                temp_df['_matrix_row'] = pd.to_numeric(self.absolute_raw_df['_matrix_row'], errors='coerce')
                temp_df['_matrix_col'] = pd.to_numeric(self.absolute_raw_df['_matrix_col'], errors='coerce')
            if '_topology_row' in self.absolute_raw_df.columns and '_topology_col' in self.absolute_raw_df.columns:
                temp_df['_topology_row'] = pd.to_numeric(self.absolute_raw_df['_topology_row'], errors='coerce')
                temp_df['_topology_col'] = pd.to_numeric(self.absolute_raw_df['_topology_col'], errors='coerce')
            if generic_pixel:
                raw_x = temp_df['X'].to_numpy(dtype=float)
                raw_y = temp_df['Y'].to_numpy(dtype=float)
                raw_z = temp_df['Z'].to_numpy(dtype=float)
                valid_xy = (np.isfinite(raw_x) & np.isfinite(raw_y) &
                            (np.abs(raw_x - np.rint(raw_x)) <= 1e-6) &
                            (np.abs(raw_y - np.rint(raw_y)) <= 1e-6))
                missing_count = int(np.sum(valid_xy & ~np.isfinite(raw_z)))
                if np.any(valid_xy):
                    px = np.rint(raw_x[valid_xy]).astype(np.int64)
                    py = np.rint(raw_y[valid_xy]).astype(np.int64)
                    matrix_cols = int(px.max() - px.min() + 1)
                    matrix_rows = int(py.max() - py.min() + 1)
                    self.import_info.update({
                        'missing_points': missing_count,
                        'matrix_cols': matrix_cols,
                        'matrix_rows': matrix_rows,
                        'source_matrix_positions': matrix_cols * matrix_rows,
                    })
            temp_df = temp_df.dropna(subset=['X', 'Y', 'Z'])

            if len(temp_df) < 3:
                raise ValueError("有效数据点少于 3 个，请检查列映射与单位选择。")
            self.import_info['valid_rows'] = int(len(temp_df))
            self.import_info['original_valid_points'] = int(
                self.import_info.get('original_valid_points') or len(temp_df))
            self.import_info['analysis_points'] = int(len(temp_df))

            unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
            if self.import_info.get('source_format') == 'Zygo XYZ Data File - Format 1':
                # Zygo专用解析器已经生成mm坐标，且标准正文Z固定为µm。
                self.cb_x_unit.setCurrentText('mm')
                self.cb_y_unit.setCurrentText('mm')
                self.cb_z_unit.setCurrentText('µm')
            x_unit = self.cb_x_unit.currentText()
            y_unit = self.cb_y_unit.currentText()
            z_unit = self.cb_z_unit.currentText()
            if generic_pixel:
                pixel_x = temp_df['X'].to_numpy(dtype=float)
                pixel_y = temp_df['Y'].to_numpy(dtype=float)
                rounded_x = np.rint(pixel_x)
                rounded_y = np.rint(pixel_y)
                if (np.max(np.abs(pixel_x - rounded_x)) > 1e-6 or
                        np.max(np.abs(pixel_y - rounded_y)) > 1e-6):
                    raise ValueError("Pixel X/Y 必须为整数像素序号；请检查导入类型或列映射。")
                matrix_col = rounded_x.astype(np.int64)
                matrix_row = rounded_y.astype(np.int64)
                temp_df['_matrix_col'] = matrix_col
                temp_df['_matrix_row'] = matrix_row
                temp_df['X'] = ((pixel_x - float(getattr(self, 'pixel_origin_x', 0.0))) *
                                float(self.height_matrix_pitch_x_um) / 1000.0)
                temp_df['Y'] = ((pixel_y - float(getattr(self, 'pixel_origin_y', 0.0))) *
                                float(self.height_matrix_pitch_y_um) / 1000.0)
                if not self.import_info.get('sampled', False):
                    temp_df['_topology_row'] = matrix_row
                    temp_df['_topology_col'] = matrix_col
                self.import_info.update({
                    'input_semantics': 'pixel_xy',
                    'sampling_pitch_x_um': float(self.height_matrix_pitch_x_um),
                    'sampling_pitch_y_um': float(self.height_matrix_pitch_y_um),
                    'sampling_pitch_source': ('用户手动' if self.pitch_source == 'manual'
                                              else 'Auto/用户默认'),
                    'pixel_origin_x': float(getattr(self, 'pixel_origin_x', 0.0)),
                    'pixel_origin_y': float(getattr(self, 'pixel_origin_y', 0.0)),
                    'topology_method': ('sampled_matrix8'
                                        if '_topology_row' in temp_df.columns and
                                           self.import_info.get('sampled')
                                        else ('matrix8' if '_topology_row' in temp_df.columns
                                              else 'Delaunay/adaptive kNN')),
                })
            else:
                temp_df['X'] = temp_df['X'] * unit_m[x_unit]
                temp_df['Y'] = temp_df['Y'] * unit_m[y_unit]
            temp_df['Z'] = temp_df['Z'] * unit_m[z_unit]

            self.import_info['mapping_x_col'] = xc
            self.import_info['mapping_y_col'] = yc
            self.import_info['mapping_z_col'] = zc
            self.import_info['z_source_field'] = zc
            self.import_info['z_source_unit'] = z_unit

            out_cols = ['Z', 'X', 'Y']
            if '_matrix_row' in temp_df.columns and '_matrix_col' in temp_df.columns:
                temp_df['_matrix_row'] = temp_df['_matrix_row'].astype(int)
                temp_df['_matrix_col'] = temp_df['_matrix_col'].astype(int)
                out_cols += ['_matrix_row', '_matrix_col']
            if '_topology_row' in temp_df.columns and '_topology_col' in temp_df.columns:
                temp_df['_topology_row'] = temp_df['_topology_row'].astype(int)
                temp_df['_topology_col'] = temp_df['_topology_col'].astype(int)
                out_cols += ['_topology_row', '_topology_col']
            self.df_raw = temp_df[out_cols]
            self._update_smart_tolerance_recommendation(self.df_raw['Z'].to_numpy(dtype=float),
                                                        apply_value=not preserve_analysis_settings)
            self.import_info['valid_rows'] = len(self.df_raw)
            if self.import_info.get('height_matrix'):
                self.import_info['analysis_points'] = len(self.df_raw)
                self.import_info['display_points'] = min(len(self.df_raw), self._display_limit())
            self.import_info['display_limit'] = self._display_limit()
            self._update_import_status_label()
            self._df_version += 1
            self._invalidate_smart_roi_runtime_cache(
                topology=True, masks=True, reason='数据或列映射已更新')
            if preserve_analysis_settings:
                self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
                self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
                self.manual_delete_operations = []
                self._manual_delete_mask_history = []
                self.pending_delete_operation = None
                self.current_coeffs = None
                self._trans_cache_key = None
                self._trans_cache_data = None
                self.update_analysis()
            else:
                self.reset_all(confirm=False)
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))
