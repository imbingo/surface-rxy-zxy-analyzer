"""DataIOMixin extracted from the V3.9.3 application."""

import sys
import os
import re
import mmap
import json
import tempfile
import hashlib
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

from ..widgets import NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox



class DataIOMixin:
    TEXT_SUFFIXES = ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz')
    EXCEL_SUFFIXES = ('.xlsx', '.xls', '.xlsm')

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
                               display_limit=None, sample_method=None, grid_count=None):
        auto = bool(self.auto_sample_large_text if auto_sample is None else auto_sample)
        threshold = int(self.large_text_threshold_mb if threshold_mb is None else threshold_mb)
        rows = int(self.large_text_import_limit if import_limit is None else import_limit)
        shown = int(self.display_point_limit if display_limit is None else display_limit)
        method = str(self.large_file_sample_method if sample_method is None else sample_method)
        grid = int(self.large_text_grid_count if grid_count is None else grid_count)
        for key, preset in self.BIGFILE_MODE_PRESETS.items():
            if (auto == bool(preset['auto_sample'])
                    and threshold == int(preset['threshold_mb'])
                    and rows == int(preset['import_limit'])
                    and shown == int(preset['display_limit'])
                    and ('file_position' if method == 'stride' else method) == str(preset.get('sample_method', 'file_position'))
                    and grid == int(preset.get('grid_count', 0))):
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
        total = Path(source_path).stat().st_size
        digest = hashlib.sha256()
        read_bytes = 0
        with open(source_path, 'rb') as handle:
            while True:
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                read_bytes += len(chunk)
                if read_bytes % (256 * 1024 * 1024) < len(chunk):
                    self.statusBar().showMessage(
                        f"正在计算源文件SHA-256: {read_bytes / (1024 * 1024):.0f}/{total / (1024 * 1024):.0f} MB",
                        1000)
                    QApplication.processEvents()
        value = digest.hexdigest().lower()
        self.import_info['source_sha256'] = value
        self.statusBar().showMessage(f"源文件SHA-256已计算: {value[:12]}…", 5000)
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
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'import_rows': 0,
            'display_limit': self._display_limit(),
            'large_file_mode': self._bigfile_mode_label(),
            'sample_method': self._sample_method_label(),
            'grid_count': self.large_text_grid_count,
            'stride_n': self.large_text_stride_n,
            'height_matrix': False,
            'matrix_pitch_x_um': self.height_matrix_pitch_x_um,
            'matrix_pitch_y_um': self.height_matrix_pitch_y_um,
            'matrix_z_unit': self.height_matrix_z_unit,
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'notes': ''
        }

    def _update_import_status_label(self):
        info = getattr(self, 'import_info', {}) or {}
        strategy = info.get('strategy', '--')
        file_size_mb = info.get('file_size_mb', 0.0)
        import_rows = info.get('import_rows', 0)
        display_limit = self._display_limit()
        shown = self.last_displayed_points if self.last_displayed_points else min(import_rows or 0, display_limit)
        sampled_text = '抽样' if info.get('sampled') else '全量/未抽样'
        quality = self._metric_quality_from_import(info)
        notes = info.get('notes') or ''
        valid_rows = info.get('valid_rows', None)
        valid_text = f" | 有效 {int(valid_rows):,} 点" if valid_rows is not None else ""
        text = (f"导入状态: {strategy} | {sampled_text} | 文件 {file_size_mb:.1f} MB | "
                f"读入 {int(import_rows):,} 行{valid_text} | 显示 {int(shown):,}/{int(display_limit):,} 点")
        if quality['estimated']:
            text += f" | 结果质量: {quality['label']}"
        if notes:
            text += f" | {notes}"
        if hasattr(self, 'lbl_import_status'):
            self.lbl_import_status.setText(text)
        if hasattr(self, 'btn_bigfile_settings'):
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
            self.statusBar().showMessage(text, 5000)

    def _on_display_limit_changed(self):
        self.import_info['display_limit'] = self._display_limit()
        self._update_import_status_label()
        if self.df_raw is not None and self.active_idx is not None:
            self.update_plots_only()

    def show_bigfile_settings_dialog(self):
        """V3.5.1: 大文件导入/显示策略弹窗。
        正常界面只保留右侧工具条按钮，避免占用左侧主控区。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("大文件导入 / 显示策略")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)

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

        grid.addWidget(QLabel("显示上限(点):"), 7, 0)
        spin_display = NoWheelSpinBox()
        spin_display.setRange(5000, 1000000)
        spin_display.setSingleStep(5000)
        spin_display.setValue(int(self.display_point_limit))
        spin_display.setToolTip("仅限制右侧绘图显示点数，不改变已导入数据和Rx/Ry/PV/TTV计算。")
        grid.addWidget(spin_display, 7, 1)

        grid.addWidget(QLabel("矩阵Pitch X(µm):"), 8, 0)
        spin_pitch_x = NoWheelDoubleSpinBox()
        spin_pitch_x.setDecimals(4)
        spin_pitch_x.setRange(0.0001, 1e6)
        spin_pitch_x.setValue(float(self.height_matrix_pitch_x_um))
        spin_pitch_x.setToolTip("VR/基恩士高度矩阵无表头或表头未写 Pitch 时，用该 X 像素间距生成 X(mm)。")
        grid.addWidget(spin_pitch_x, 8, 1)

        grid.addWidget(QLabel("矩阵Pitch Y(µm):"), 9, 0)
        spin_pitch_y = NoWheelDoubleSpinBox()
        spin_pitch_y.setDecimals(4)
        spin_pitch_y.setRange(0.0001, 1e6)
        spin_pitch_y.setValue(float(self.height_matrix_pitch_y_um))
        spin_pitch_y.setToolTip("VR/基恩士高度矩阵无表头或表头未写 Pitch 时，用该 Y 像素间距生成 Y(mm)。")
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
                                                   cb_sample_method.currentData(), spin_grid.value())
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
                spin_display.setValue(int(preset['display_limit']))
            finally:
                applying_preset['active'] = False
            mode_note.setText(self._bigfile_mode_description(mode_key))

        def update_grid_enabled(*_args):
            spin_grid.setEnabled(cb_sample_method.currentData() == 'spatial_grid')
            sync_mode_from_values()

        cb_mode.currentIndexChanged.connect(apply_preset_from_combo)
        chk_auto.toggled.connect(sync_mode_from_values)
        cb_sample_method.currentIndexChanged.connect(update_grid_enabled)
        spin_grid.valueChanged.connect(sync_mode_from_values)
        spin_mb.valueChanged.connect(sync_mode_from_values)
        spin_import.valueChanged.connect(sync_mode_from_values)
        spin_display.valueChanged.connect(sync_mode_from_values)
        update_grid_enabled()

        note = QLabel("说明：文件位置采样优先保证导入和交互流畅；空间网格采样会先扫描全文件确定 X/Y 范围，再按网格保留代表点、Z最小点和Z最大点。导入抽样会影响参与分析的数据量，显示上限只影响绘图。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        grid.addWidget(note, 13, 0, 1, 2)
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
            self.auto_sample_large_text = chk_auto.isChecked()
            self.large_file_sample_method = str(cb_sample_method.currentData())
            self.large_text_grid_count = int(spin_grid.value())
            self.large_text_threshold_mb = int(spin_mb.value())
            self.large_text_import_limit = int(spin_import.value())
            self.display_point_limit = int(spin_display.value())
            self.height_matrix_pitch_x_um = float(spin_pitch_x.value())
            self.height_matrix_pitch_y_um = float(spin_pitch_y.value())
            self.height_matrix_z_unit = str(cb_matrix_z_unit.currentText())
            self.height_matrix_start_row = int(spin_matrix_start.value())
            self.large_file_mode = self._matching_bigfile_mode()
            self.import_info['display_limit'] = self.display_point_limit
            self.import_info['large_file_mode'] = self._bigfile_mode_label()
            self.import_info['sample_method'] = self._sample_method_label()
            self.import_info['grid_count'] = self.large_text_grid_count
            self.import_info['stride_n'] = self.large_text_stride_n
            self.import_info['matrix_pitch_x_um'] = self.height_matrix_pitch_x_um
            self.import_info['matrix_pitch_y_um'] = self.height_matrix_pitch_y_um
            self.import_info['matrix_z_unit'] = self.height_matrix_z_unit
            self.import_info['matrix_start_row'] = self.height_matrix_start_row
            self._update_import_status_label()
            if old_display_limit != self.display_point_limit and self.df_raw is not None and self.active_idx is not None:
                self.update_plots_only()
            self.statusBar().showMessage("大文件导入/显示策略已更新", 5000)

    @staticmethod
    def _detect_sep_from_line(line):
        if '\t' in line:
            return '\t'
        if ',' in line:
            return ','
        if ';' in line:
            return ';'
        return r'\s+'

    @staticmethod
    def _split_text_line(line, sep):
        if sep == r'\s+':
            return [t for t in re.split(r'\s+', line.strip()) if t]
        return [t.strip() for t in line.strip().split(sep)]

    @staticmethod
    def _trim_trailing_empty_tokens(tokens):
        values = list(tokens)
        while values and not str(values[-1]).strip():
            values.pop()
        return values

    @classmethod
    def _is_missing_token(cls, value):
        token = str(value).strip()
        return not token or token in cls.MISSING_TEXT_TOKENS

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
    def _token_to_float(cls, value):
        if cls._is_missing_token(value):
            return np.nan
        try:
            return float(value)
        except (TypeError, ValueError):
            return np.nan

    @classmethod
    def _detect_text_layout(cls, path, enc, max_scan_lines=50000, start_line_no=0):
        """扫描文本开头，识别第一行有效数值数据、分隔符、列数和可选表头。
        不再命中第一组数值行就立即返回，而是比较多个候选区，避免把设备参数表误认成数据。"""
        candidates = []
        run = None
        min_data_rows = 3
        previous_non_numeric = None

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
            stable_matrix = ncols >= 8 and count >= 8
            return (int(stable_matrix), count * ncols, count, ncols, int(item['data_line_no']))

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
                if line_no < max(0, int(start_line_no)):
                    continue
                if line_no >= max(0, int(start_line_no)) + max_scan_lines:
                    return best_candidate(run)
                stripped = line.strip().lstrip('\ufeff')
                if not stripped or stripped.startswith('#'):
                    finish_run(line_no)
                    previous_non_numeric = None
                    continue
                sep = cls._detect_sep_from_line(stripped)
                tokens = cls._trim_trailing_empty_tokens(cls._split_text_line(stripped, sep))
                is_numeric = cls._looks_like_numeric_text_row(tokens) and len(tokens) >= 3
                if is_numeric:
                    same_run = (
                        run is not None and run['sep'] == sep and run['ncols'] == len(tokens)
                        and line_no == run['last_line_no'] + 1
                    )
                    if not same_run:
                        finish_run(line_no)
                        header_tokens = None
                        header_line_no = None
                        header_sep = None
                        if previous_non_numeric and previous_non_numeric['line_no'] == line_no - 1:
                            prior = previous_non_numeric['tokens']
                            if len(prior) == len(tokens):
                                header_tokens = [str(t).replace('\ufeff', '').strip()
                                                 for t in prior]
                                header_line_no = previous_non_numeric['line_no']
                                header_sep = previous_non_numeric['sep']
                        run = {
                            'encoding': enc,
                            'sep': sep,
                            'ncols': len(tokens),
                            'data_line_no': line_no,
                            'header_tokens': header_tokens,
                            'first_numeric_line': stripped,
                            'header_line_no': header_line_no,
                            'header_sep': header_sep,
                            'count': 1,
                            'last_line_no': line_no,
                        }
                    else:
                        run['count'] += 1
                        run['last_line_no'] = line_no
                    previous_non_numeric = None
                    # 宽矩阵读取单行成本很高；确认稳定后即可停止布局扫描。
                    if ((run['ncols'] >= 8 and run['count'] >= 32)
                            or (run['ncols'] < 8 and run['count'] >= 512)):
                        return best_candidate(run)
                    continue
                finish_run(line_no)
                previous_non_numeric = {'tokens': tokens, 'line_no': line_no, 'sep': sep}
        finish_run(run['last_line_no'] + 1 if run is not None else None)
        return best_candidate()

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
                tokens = self._trim_trailing_empty_tokens(self._split_text_line(stripped, prepared['sep']))
                if len(tokens) != raw_ncols:
                    break
                sample_rows.append((line_no, tokens))

        value_start = 0
        coordinate_header = False
        data_start = original_start
        header = self._trim_trailing_empty_tokens(prepared.get('header_tokens') or [])
        axis_words = (
            'y/x', 'y\\x', 'x', 'x坐标', 'xcoordinate', 'row', 'index',
            '行号', '行', '列坐标', 'y坐标', 'ycoordinate')

        if header and len(header) == raw_ncols:
            first_label = str(header[0]).strip().lower().replace(' ', '')
            rest = [self._token_to_float(value) for value in header[1:]]
            if any(word in first_label for word in axis_words) and len(rest) >= 8:
                value_start = 1
                coordinate_header = True

        if sample_rows:
            first_tokens = sample_rows[0][1]
            rest = [self._token_to_float(value) for value in first_tokens[1:]]
            if (self._is_missing_token(first_tokens[0]) and len(rest) >= 8
                    and self._regular_numeric_sequence(rest)):
                value_start = 1
                coordinate_header = True
                data_start = int(sample_rows[0][0]) + 1

        data_samples = [tokens for line_no, tokens in sample_rows if line_no >= data_start]
        first_column = [self._token_to_float(tokens[0]) for tokens in data_samples if tokens]
        integer_row_index = (
            len(first_column) >= 8 and self._regular_numeric_sequence(first_column)
            and np.allclose(first_column, np.round(first_column), rtol=0.0, atol=1e-9)
            and abs(abs(float(np.median(np.diff(first_column)))) - 1.0) <= 1e-9)
        if value_start == 0 and raw_ncols >= 9 and integer_row_index:
            value_start = 1
            coordinate_header = True

        value_count = raw_ncols - value_start
        prepared.update({
            'raw_ncols': raw_ncols,
            'ncols': value_count,
            'matrix_value_start': value_start,
            'matrix_coordinate_header': coordinate_header,
            'detected_data_line_no': original_start,
            'data_line_no': data_start,
            'header_rows_skipped': data_start,
        })
        return prepared

    def _height_matrix_header_meta(self, path, enc, data_line_no):
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
            # 保持旧版 VR Demo 的 -1000 缺测约定；表头给出标记时只按明确标记剔除。
            arr[arr < -999] = np.nan
        return arr

    def _looks_like_height_matrix_layout(self, path, enc, layout):
        """判断文本数据是否为二维高度矩阵，而不是普通 XYZ 表格。"""
        if not layout or int(layout.get('ncols', 0)) < 8:
            return False
        prepared = self._prepare_height_matrix_layout(path, enc, layout)
        if int(prepared.get('ncols', 0)) < 8:
            return False
        layout.update(prepared)
        header = [str(x).strip().lower() for x in (layout.get('header_tokens') or [])]
        if header:
            cleaned = {re.sub(r'[^a-z0-9]+', '', h) for h in header[:8]}
            has_x = any(h == 'x' or h.startswith('xmm') or h.startswith('xum') for h in cleaned)
            has_y = any(h == 'y' or h.startswith('ymm') or h.startswith('yum') for h in cleaned)
            has_z = any(h == 'z' or h.startswith('zmm') or h.startswith('zum')
                        or h.startswith('height') for h in cleaned)
            if has_x and has_y and has_z:
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
                    tokens = self._trim_trailing_empty_tokens(self._split_text_line(stripped, layout['sep']))
                    scanned += 1
                    values = tokens[value_start:value_start + target_cols]
                    if len(tokens) == raw_cols and self._looks_like_numeric_text_row(values):
                        good_rows += 1
                    if scanned >= 16:
                        break
        except Exception:
            return False
        return good_rows >= 4

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
        })

    def _sample_large_height_matrix_by_stride(self, path, enc, sep, ncols, data_line_no,
                                              pitch_x_um, pitch_y_um, z_unit, meta_source,
                                              data_end_line_no=None, value_start=0, invalid_values=()):
        file_size = Path(path).stat().st_size
        stride = max(1, int(getattr(self, 'large_text_stride_n', 10)))
        max_rows = self._large_text_import_limit()

        def parse_matrix_line(line):
            stripped = line.strip().lstrip('\ufeff')
            if not stripped:
                return None
            tokens = self._trim_trailing_empty_tokens(self._split_text_line(stripped, sep))
            values_tokens = tokens[value_start:value_start + ncols]
            if len(values_tokens) < ncols or not self._looks_like_numeric_text_row(values_tokens):
                return None
            values = np.array([self._token_to_float(t) for t in values_tokens], dtype=float)
            return self._mask_matrix_missing_values(values, invalid_values)

        row_count = 0
        valid_points = 0
        matrix_started = False
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
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
                row_count += 1
                valid_points += int(np.isfinite(values).sum())
                if row_count % 1000 == 0:
                    self.statusBar().showMessage(
                        f"高度矩阵倍率预扫描: {row_count:,} 行 | 有效 {valid_points:,} 点", 1000)
                    QApplication.processEvents()

        if row_count == 0 or valid_points == 0:
            raise ValueError("高度矩阵倍率降采样未识别到有效数据。")

        pitch_x_mm = float(pitch_x_um) / 1000.0
        pitch_y_mm = float(pitch_y_um) / 1000.0
        rows = []
        matrix_row = 0
        matrix_started = False
        z_min = np.inf
        z_max = -np.inf
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
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
                if matrix_row % 1000 == 0:
                    self.statusBar().showMessage(
                        f"高度矩阵倍率降采样: {matrix_row:,}/{row_count:,} 行 | 已取 {len(rows):,} 点 | N={stride}", 1000)
                    QApplication.processEvents()

        if not rows:
            raise ValueError("高度矩阵倍率降采样未得到有效点，请调小降采样倍率 N。")

        df = pd.DataFrame(rows, columns=['X', 'Y', 'Z', '_matrix_row', '_matrix_col'])
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
            'sampled': True,
            'sample_method_key': 'stride',
            'extrema_preserved': False,
            'height_matrix': True,
            'import_rows': len(df),
            'source_valid_rows': valid_points,
            'matrix_rows': row_count,
            'matrix_cols': ncols,
            'matrix_pitch_x_um': float(pitch_x_um),
            'matrix_pitch_y_um': float(pitch_y_um),
            'matrix_z_unit': z_unit,
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
                                    data_end_line_no=None, value_start=0, invalid_values=()):
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()

        def parse_matrix_line(line):
            stripped = line.strip().lstrip('\ufeff')
            if not stripped:
                return None
            tokens = self._trim_trailing_empty_tokens(self._split_text_line(stripped, sep))
            values_tokens = tokens[value_start:value_start + ncols]
            if len(values_tokens) < ncols or not self._looks_like_numeric_text_row(values_tokens):
                return None
            values = np.array([self._token_to_float(t) for t in values_tokens], dtype=float)
            return self._mask_matrix_missing_values(values, invalid_values)

        row_count = 0
        valid_points = 0
        z_min = np.inf
        z_max = -np.inf
        matrix_started = False
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
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
                row_count += 1
                finite = np.isfinite(values)
                if np.any(finite):
                    vals = values[finite]
                    valid_points += int(len(vals))
                    z_min = min(z_min, float(np.min(vals)))
                    z_max = max(z_max, float(np.max(vals)))
                if row_count % 1000 == 0:
                    self.statusBar().showMessage(
                        f"高度矩阵预扫描: {row_count:,} 行 | 有效 {valid_points:,} 点", 1000)
                    QApplication.processEvents()

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
                if matrix_row % 1000 == 0:
                    self.statusBar().showMessage(
                        f"高度矩阵落格: {matrix_row:,}/{row_count:,} 行 | 占用网格 {len(cells):,}", 1000)
                    QApplication.processEvents()

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
            'sampled': True,
            'sample_method_key': 'spatial_grid',
            'extrema_preserved': True,
            'height_matrix': True,
            'import_rows': len(df),
            'source_valid_rows': valid_points,
            'matrix_rows': row_count,
            'matrix_cols': ncols,
            'matrix_pitch_x_um': float(pitch_x_um),
            'matrix_pitch_y_um': float(pitch_y_um),
            'matrix_z_unit': z_unit,
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

    def _read_height_matrix_table(self, path, enc, layout, file_size):
        sep = layout['sep']
        ncols = int(layout['ncols'])
        data_line_no = int(layout['data_line_no'])
        data_end_line_no = layout.get('data_end_line_no')
        value_start = int(layout.get('matrix_value_start', 0))
        pitch_x, pitch_y, z_unit, invalid_values, meta_source = self._height_matrix_header_meta(
            path, enc, data_line_no)
        auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
        if auto_sample and file_size >= self._large_text_threshold_bytes():
            return self._sample_large_height_matrix(
                path, enc, sep, ncols, data_line_no, pitch_x, pitch_y, z_unit, meta_source,
                data_end_line_no, value_start, invalid_values)

        read_kwargs = {}
        if data_end_line_no is not None:
            read_kwargs['nrows'] = max(0, int(data_end_line_no) - data_line_no)
        raw = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                          comment='#', skip_blank_lines=True, on_bad_lines='skip',
                          header=None, skiprows=data_line_no,
                          na_values=list(self.MISSING_TEXT_TOKENS),
                          keep_default_na=True, **read_kwargs)
        raw = raw.iloc[:, value_start:value_start + ncols]
        valid_rows = raw.apply(
            lambda column: column.map(self._is_float_or_missing_token)).all(axis=1)
        numeric_counts = raw.apply(pd.to_numeric, errors='coerce').notna().sum(axis=1)
        valid_rows = (valid_rows & (numeric_counts >= 2)).to_numpy(dtype=bool)
        invalid_positions = np.flatnonzero(~valid_rows)
        if invalid_positions.size:
            raw = raw.iloc[:int(invalid_positions[0])]
        else:
            raw = raw.iloc[:]
        z = raw.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float, copy=True)
        rows_count, cols_count = z.shape
        df = self._height_matrix_dataframe(
            z, rows_count, cols_count, pitch_x, pitch_y, invalid_values)
        coordinate_text = "；已去除顶部/左侧坐标标题" if layout.get('matrix_coordinate_header') else ""
        invalid_text = ', '.join(f'{v:g}' for v in invalid_values) if invalid_values else '< -999（兼容旧格式）'
        start_mode = (f"手动起始行 {self.height_matrix_start_row}"
                      if int(getattr(self, 'height_matrix_start_row', 0)) > 0 else "自动识别")
        self.last_import_note = (
            f"VR/基恩士高度矩阵已全量导入。\n"
            f"识别方式: {start_mode} | 数据起始行: {data_line_no + 1} | 跳过前置说明: {data_line_no} 行{coordinate_text}\n"
            f"矩阵尺寸: {rows_count:,} × {cols_count:,} | 有效点: {len(df):,}\n"
            f"Pitch: X={pitch_x:g}µm, Y={pitch_y:g}µm（{meta_source}）| Z单位: {z_unit}\n"
            f"无效值规则: {invalid_text}。如识别范围不正确，可在“大文件策略”中填写矩阵数据起始行后重新导入。")
        self.import_info.update({
            'strategy': '高度矩阵全量读取',
            'sampled': False,
            'sample_method_key': 'full',
            'extrema_preserved': True,
            'height_matrix': True,
            'import_rows': len(df),
            'matrix_rows': int(rows_count),
            'matrix_cols': int(cols_count),
            'matrix_pitch_x_um': float(pitch_x),
            'matrix_pitch_y_um': float(pitch_y),
            'matrix_z_unit': z_unit,
            'matrix_data_start_row': data_line_no + 1,
            'matrix_header_rows_skipped': data_line_no,
            'matrix_start_row': int(getattr(self, 'height_matrix_start_row', 0)),
            'matrix_coordinate_header': bool(layout.get('matrix_coordinate_header')),
            'matrix_invalid_values': list(invalid_values),
            'layout_candidate_count': int(layout.get('candidate_count', 1)),
            'notes': f"高度矩阵 {rows_count}×{cols_count} | 起始行 {data_line_no + 1} | Pitch {pitch_x:g}/{pitch_y:g}µm"
        })
        return df

    def _infer_xyz_column_indices(self, column_names, ncols):
        """大文件空间采样发生在列映射 UI 之前，只能按列名/常规 XYZ 顺序推断。"""
        if ncols < 3:
            raise ValueError("空间网格采样需要至少 3 列数值数据（X/Y/Z）。")
        names = [str(c).strip().lower() for c in (column_names or [])]

        def find_axis(axis, fallback):
            candidates = []
            for i, name in enumerate(names[:ncols]):
                cleaned = re.sub(r'[^a-z0-9]+', '', name)
                if cleaned == axis or cleaned.startswith(axis):
                    candidates.append(i)
            return candidates[0] if candidates else fallback

        x_idx = find_axis('x', 0)
        y_idx = find_axis('y', 1)
        z_idx = find_axis('z', 2)
        if len({x_idx, y_idx, z_idx}) < 3:
            x_idx, y_idx, z_idx = 0, 1, 2
        return x_idx, y_idx, z_idx

    def _max_safe_grid_side(self, max_rows):
        # 每格最多保留：代表点 + Z最小点 + Z最大点。
        return max(1, int(np.floor(np.sqrt(max(1, int(max_rows)) / 3.0))))

    def _auto_spatial_grid_side(self, valid_rows, max_rows):
        target_rows = max(1, min(int(valid_rows), int(max_rows)))
        target_cells = max(1, int(np.ceil(target_rows / 3.0)))
        return max(1, int(np.ceil(np.sqrt(target_cells))))

    def _sample_large_text(self, path, enc, sep, ncols, column_names=None):
        method = getattr(self, 'large_file_sample_method', 'spatial_grid')
        if method == 'stride':
            method = 'file_position'
        if method == 'file_position':
            return self._sample_large_text_by_position(path, enc, sep, ncols, column_names)
        return self._sample_large_text_by_spatial_grid(path, enc, sep, ncols, column_names)

    def _sample_large_text_by_stride(self, path, enc, sep, ncols, column_names=None):
        """倍率降采样：按有效数值行每 N 行取 1 行。速度快，但不保留局部 min/max。"""
        file_size = Path(path).stat().st_size
        stride = max(1, int(getattr(self, 'large_text_stride_n', 10)))
        max_rows = self._large_text_import_limit()
        rows = []
        valid_rows = 0

        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line in fh:
                stripped = line.strip().lstrip('\ufeff')
                if not stripped or stripped.startswith('#'):
                    continue
                tokens = self._split_text_line(stripped, sep)
                if not self._looks_like_numeric_text_row(tokens):
                    continue
                values = [self._token_to_float(t) for t in tokens[:ncols]]
                if len(values) < ncols:
                    values.extend([np.nan] * (ncols - len(values)))
                valid_rows += 1
                if (valid_rows - 1) % stride == 0:
                    rows.append(values)
                    if len(rows) >= max_rows:
                        break
                if valid_rows % 100000 == 0:
                    self.statusBar().showMessage(
                        f"倍率降采样: 已扫描 {valid_rows:,} 行 | 已取 {len(rows):,} 行 | N={stride}", 1000)
                    QApplication.processEvents()

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

    def _sample_large_text_by_position(self, path, enc, sep, ncols, column_names=None):
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
                    if not self._looks_like_numeric_text_row(tokens):
                        continue
                    values = [self._token_to_float(t) for t in tokens[:ncols]]
                    if len(values) < ncols:
                        values.extend([np.nan] * (ncols - len(values)))
                    rows.append(values)
                    if i % 5000 == 0:
                        self.statusBar().showMessage(
                            f"正在抽样导入超大TXT: {i + 1:,}/{max_rows:,} | 已取有效行 {len(rows):,}", 1000)
                        QApplication.processEvents()
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

    def _sample_large_text_by_spatial_grid(self, path, enc, sep, ncols, column_names=None):
        """V3.8.3: 空间网格均匀采样。

        按 X/Y 分格，每格最多保留三类点：首个代表点、Z最小点、Z最大点。
        这样 TTV 的局部极值更不容易被采样丢掉；PV 仍以导入后的采样点参与拟合。
        """
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()
        x_idx, y_idx, z_idx = self._infer_xyz_column_indices(column_names, ncols)
        cols = column_names if column_names and len(column_names) == ncols else [f'Col{i+1}' for i in range(ncols)]

        x_min = y_min = z_min = np.inf
        x_max = y_max = z_max = -np.inf
        valid_rows = 0

        def parse_numeric_line(line):
            stripped = line.strip().lstrip('\ufeff')
            if not stripped or stripped.startswith('#'):
                return None
            tokens = self._split_text_line(stripped, sep)
            if not self._looks_like_numeric_text_row(tokens):
                return None
            values = [self._token_to_float(t) for t in tokens[:ncols]]
            if len(values) < ncols:
                values.extend([np.nan] * (ncols - len(values)))
            return values

        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh, start=1):
                values = parse_numeric_line(line)
                if values is None:
                    continue
                x, y, z = values[x_idx], values[y_idx], values[z_idx]
                if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
                    continue
                valid_rows += 1
                x_min = min(x_min, x); x_max = max(x_max, x)
                y_min = min(y_min, y); y_max = max(y_max, y)
                z_min = min(z_min, z); z_max = max(z_max, z)
                if valid_rows % 100000 == 0:
                    self.statusBar().showMessage(
                        f"空间网格采样预扫描: 已识别 {valid_rows:,} 个有效 XYZ 点", 1000)
                    QApplication.processEvents()

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
            for line in fh:
                values = parse_numeric_line(line)
                if values is None:
                    continue
                x, y, z = values[x_idx], values[y_idx], values[z_idx]
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
                    self.statusBar().showMessage(
                        f"空间网格采样落格: {scanned:,}/{valid_rows:,} | 已占用网格 {len(cells):,}", 1000)
                    QApplication.processEvents()

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

    def _read_table(self, path):
        """鲁棒读取表格文件：
        - 文本类(.csv/.txt/.tsv/.dat/.asc/.xyz): 自动尝试 utf-8-sig/gbk/utf-16/latin-1；自动识别分隔符；
          自动跳过#注释行、空行、坏行；识别无表头/普通表头/Zeiss类复杂头。
        - 超过设定阈值的文本文件可在 pandas 全量读入前预抽样，默认使用文件位置均匀采样。
          也可切换到空间网格采样，每格保留代表点、Z最小点和Z最大点。
        - Excel类(.xlsx/.xls/.xlsm): pd.read_excel，不做预抽样。
        """
        self.last_import_note = ""
        self._reset_import_info(path)
        suffix = Path(path).suffix.lower()
        file_size = Path(path).stat().st_size

        if suffix in self.EXCEL_SUFFIXES:
            df = pd.read_excel(path)
            self.import_info.update({
                'strategy': 'Excel全量读取',
                'sampled': False,
                'sample_method_key': 'full',
                'extrema_preserved': True,
                'import_rows': len(df),
                'notes': 'Excel不做预抽样'
            })
        elif suffix in self.TEXT_SUFFIXES or suffix == '':
            last_err = None
            df = None
            layout = None

            for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                try:
                    manual_start = max(0, int(getattr(self, 'height_matrix_start_row', 0)) - 1)
                    layout = self._detect_text_layout(path, enc, start_line_no=manual_start)
                    if layout is not None:
                        break
                except Exception as e:
                    last_err = e
                    layout = None

            if layout is not None:
                enc = layout['encoding']
                sep = layout['sep']
                ncols = layout['ncols']
                col_names = layout['header_tokens'] if layout['header_tokens'] else [f'Col{i+1}' for i in range(ncols)]
                if self._looks_like_height_matrix_layout(path, enc, layout):
                    df = self._read_height_matrix_table(path, enc, layout, file_size)
                    self.import_info['display_limit'] = self._display_limit()
                    self._update_import_status_label()
                    return df
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    df = self._sample_large_text(path, enc, sep, ncols, col_names)
                else:
                    df = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                     comment='#', skip_blank_lines=True,
                                     on_bad_lines='skip', header=None,
                                     skiprows=layout['data_line_no'],
                                     na_values=list(self.MISSING_TEXT_TOKENS),
                                     keep_default_na=True)
                    if df.shape[1] >= len(col_names):
                        df = df.iloc[:, :len(col_names)]
                        df.columns = col_names
                    else:
                        df.columns = [f'Col{i+1}' for i in range(df.shape[1])]
                    self.import_info.update({
                        'strategy': '文本全量读取',
                        'sampled': False,
                        'sample_method_key': 'full',
                        'extrema_preserved': True,
                        'import_rows': len(df),
                        'notes': f"编码 {enc}"
                    })
            else:
                # 回退到 pandas 嗅探；不建议用于超大未知格式文件，因此超过阈值时给出明确提示。
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    raise ValueError("文件超过超大文本阈值，但前5000行未识别到有效数值数据行；\n"
                                     "为避免全量读入卡死，已停止导入。请检查Zeiss TXT头部格式，或关闭自动抽样后重试。")
                for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                    for sep in (None, ',', '\t', ';', r'\s+'):
                        try:
                            df_try = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                                 comment='#', skip_blank_lines=True, on_bad_lines='skip',
                                                 na_values=list(self.MISSING_TEXT_TOKENS),
                                                 keep_default_na=True)
                            if df_try.shape[1] >= 2:
                                df = df_try
                                self.import_info.update({
                                    'strategy': '文本全量读取(回退嗅探)',
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
        df.columns = [str(c).replace('\ufeff', '').strip() for c in df.columns]

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
        self._update_import_status_label()
        return df

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "载入数据", "",
            "Data (*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm);;All Files (*)")
        if not path:
            return False
        return self.load_path(path)

    def load_path(self, path):
        """Load a known path; used by both the file dialog and platform integration."""
        path = str(Path(path).expanduser().resolve())
        try:
            self.absolute_raw_df = self._read_table(path)

            self.current_source_name = Path(path).name
            self.lbl_source.setText(f"当前数据: {self.current_source_name}")

            cols = [str(c) for c in self.absolute_raw_df.columns]
            for cb in [self.cb_x_col, self.cb_y_col, self.cb_z_col]:
                cb.blockSignals(True); cb.clear(); cb.addItems(cols); cb.blockSignals(False)

            def guess_index(tc, di):
                for i, col in enumerate(cols):
                    if tc.lower() in col.lower(): return i
                return di if di < len(cols) else 0

            if self.import_info.get('height_matrix') and all(c in cols for c in ('X', 'Y', 'Z')):
                self.cb_x_col.setCurrentText('X')
                self.cb_y_col.setCurrentText('Y')
                self.cb_z_col.setCurrentText('Z')
                self.cb_x_unit.setCurrentText('mm')
                self.cb_y_unit.setCurrentText('mm')
                self.cb_z_unit.setCurrentText(self.import_info.get('matrix_z_unit', self.height_matrix_z_unit))
            elif len(cols) >= 3 and all(re.fullmatch(r'Col\d+', c) for c in cols):
                # 无表头 X/Y/Z 文本默认按 Col1/Col2/Col3 映射，适配 Zeiss/XYZ 常见导出
                self.cb_x_col.setCurrentIndex(0)
                self.cb_y_col.setCurrentIndex(1)
                self.cb_z_col.setCurrentIndex(2)
            else:
                self.cb_x_col.setCurrentIndex(guess_index('x', 1))
                self.cb_y_col.setCurrentIndex(guess_index('y', 2))
                self.cb_z_col.setCurrentIndex(guess_index('z', 0))
            if self.pending_recipe is not None:
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
            if self.last_import_note:
                self.statusBar().showMessage(self.last_import_note.replace('\n', ' | '), 15000)
                QMessageBox.information(self, "超大文件导入说明", self.last_import_note)

            # 寄存器保留提示（多层流程需要跨文件保留，故不自动清空）
            if any(s is not None for s in (self.data_stack, self.data_base1, self.data_base2)):
                self.statusBar().showMessage(
                    "⚠ 提示: 多层寄存器仍保留之前的数据，如属不同物料请到[多层]页点击 [🧹 清空全部寄存器]", 10000)
            return True
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return False

    def apply_mapping(self, preserve_analysis_settings=False):
        if self.absolute_raw_df is None:
            self.statusBar().showMessage("当前无原始文件可映射（Gap 结果状态下映射已锁定）", 5000)
            return
        try:
            xc, yc, zc = self.cb_x_col.currentText(), self.cb_y_col.currentText(), self.cb_z_col.currentText()
            for name, col in (("X", xc), ("Y", yc), ("Z", zc)):
                if col not in self.absolute_raw_df.columns:
                    raise ValueError(f"{name}列 '{col}' 不在文件列中，请重新选择列映射。")
            temp_df = pd.DataFrame()
            temp_df['X'] = pd.to_numeric(self.absolute_raw_df[xc], errors='coerce')
            temp_df['Y'] = pd.to_numeric(self.absolute_raw_df[yc], errors='coerce')
            temp_df['Z'] = pd.to_numeric(self.absolute_raw_df[zc], errors='coerce')
            if '_matrix_row' in self.absolute_raw_df.columns and '_matrix_col' in self.absolute_raw_df.columns:
                temp_df['_matrix_row'] = pd.to_numeric(self.absolute_raw_df['_matrix_row'], errors='coerce')
                temp_df['_matrix_col'] = pd.to_numeric(self.absolute_raw_df['_matrix_col'], errors='coerce')
            temp_df = temp_df.dropna(subset=['X', 'Y', 'Z'])

            if len(temp_df) < 3:
                raise ValueError("有效数据点少于 3 个，请检查列映射与单位选择。")

            unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
            temp_df['X'] = temp_df['X'] * unit_m[self.cb_x_unit.currentText()]
            temp_df['Y'] = temp_df['Y'] * unit_m[self.cb_y_unit.currentText()]
            temp_df['Z'] = temp_df['Z'] * unit_m[self.cb_z_unit.currentText()]

            out_cols = ['Z', 'X', 'Y']
            if '_matrix_row' in temp_df.columns and '_matrix_col' in temp_df.columns:
                temp_df['_matrix_row'] = temp_df['_matrix_row'].astype(int)
                temp_df['_matrix_col'] = temp_df['_matrix_col'].astype(int)
                out_cols += ['_matrix_row', '_matrix_col']
            self.df_raw = temp_df[out_cols]
            self._update_smart_tolerance_recommendation(self.df_raw['Z'].to_numpy(dtype=float),
                                                        apply_value=not preserve_analysis_settings)
            self.import_info['valid_rows'] = len(self.df_raw)
            self.import_info['display_limit'] = self._display_limit()
            self._update_import_status_label()
            self._df_version += 1
            if preserve_analysis_settings:
                self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
                self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
                self.manual_delete_operations = []
                self.pending_delete_operation = None
                self.current_coeffs = None
                self._trans_cache_key = None
                self._trans_cache_data = None
                self.update_analysis()
            else:
                self.reset_all()
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))
