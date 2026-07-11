"""GapAnalysisMixin extracted from the V3.9.3 application."""

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



class GapAnalysisMixin:
    def set_memory_slot(self, slot):
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "错误", "主界面尚无数据！请先载入并处理。")
            return
        if not self._confirm_estimated_metrics('写入多层扣减寄存器'):
            return

        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        rec = {'x': fx.copy(), 'y': fy.copy(), 'z': fz.copy(),
               'name': self.current_source_name, 'n': len(fz),
               'metric_quality': dict(self._current_metric_quality()),
               'sampled': bool(self.import_info.get('sampled', False))}

        if slot == 'stack':
            self.data_stack = rec
            self.lbl_stack_status.setText(f"✅ 已存【堆叠总成】\n来源: {rec['name']} (共 {rec['n']} 点)\n{rec['metric_quality']['label']}")
            self.lbl_stack_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base1':
            self.data_base1 = rec
            self.lbl_base1_status.setText(f"✅ 已存【单片 1】\n来源: {rec['name']} (共 {rec['n']} 点)\n{rec['metric_quality']['label']}")
            self.lbl_base1_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base2':
            self.data_base2 = rec
            self.lbl_base2_status.setText(f"✅ 已存【单片 2】\n来源: {rec['name']} (共 {rec['n']} 点)\n{rec['metric_quality']['label']}")
            self.lbl_base2_status.setStyleSheet("color: #2980b9; font-weight: bold;")

    def clear_memory_slot(self, slot):
        if slot == 'base2':
            self.data_base2 = None
            self.lbl_base2_status.setText("⭕ 可选空置")
            self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")

    def clear_all_memory_slots(self):
        self.data_stack = None
        self.data_base1 = None
        self.data_base2 = None
        self.lbl_stack_status.setText("❌ 尚未设置")
        self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setText("❌ 尚未设置")
        self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base2_status.setText("⭕ 可选空置")
        self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self._update_gap_diagnostic(None)
        self.statusBar().showMessage("已清空全部寄存器", 3000)

    def _update_gap_diagnostic(self, diag):
        if not hasattr(self, 'gap_match_canvas'):
            return
        self.gap_match_canvas.plot_diagnostic(diag)
        if diag is None:
            if hasattr(self, 'lbl_gap_matched'):
                self.lbl_gap_matched.setText("--")
                self.lbl_gap_unmatched.setText("--")
                self.lbl_gap_tolerance.setText("--")
                self.lbl_gap_state.setText("待计算")
            return
        final_valid = diag['final_valid']
        total = int(len(final_valid))
        matched = int(np.sum(final_valid))
        if hasattr(self, 'lbl_gap_matched'):
            self.lbl_gap_matched.setText(f"{matched:,}")
            self.lbl_gap_unmatched.setText(f"{total - matched:,}")
            self.lbl_gap_tolerance.setText(f"{diag['tolerance']:.3f}")
            self.lbl_gap_state.setText("已诊断" if matched >= 10 else "匹配不足")

    def calculate_gap(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.critical(self, "数据缺失", "执行运算至少需要设置【堆叠总成】和【单片 1】！")
            return

        tolerance = self.spin_tol.value()

        # 计算前确认各寄存器来源，防止新旧物料混用
        desc = (f"即将执行: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if self.data_base2 else ''}\n\n"
                f"堆叠总成: {self.data_stack['name']}  ({self.data_stack['n']} 点)\n"
                f"单片 1:   {self.data_base1['name']}  ({self.data_base1['n']} 点)\n")
        if self.data_base2 is not None:
            desc += f"单片 2:   {self.data_base2['name']}  ({self.data_base2['n']} 点)\n"
        desc += f"\n容差窗口: {tolerance} mm\n\n请确认以上数据来源无误（避免新旧物料混用）。"
        ret = QMessageBox.question(self, "确认计算", desc,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            sx, sy, sz = self.data_stack['x'], self.data_stack['y'], self.data_stack['z']
            b1x, b1y, b1z = self.data_base1['x'], self.data_base1['y'], self.data_base1['z']

            tree1 = cKDTree(np.column_stack([b1x, b1y]))
            dist1, idx1 = tree1.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
            valid1 = dist1 <= tolerance

            report_parts = []

            def match_report(tag, dist, idx, valid):
                md = dist[valid]
                if len(md) == 0:
                    return f"[{tag}] 无匹配点！"
                rms_um = np.sqrt(np.mean(md ** 2)) * 1000
                max_um = np.max(md) * 1000
                uniq = len(np.unique(idx[valid])) / valid.sum() * 100
                s = (f"[{tag}] 匹配 {int(valid.sum())} / 未匹配 {int((~valid).sum())}\n"
                     f"    匹配距离 RMS {rms_um:.2f} µm | Max {max_um:.2f} µm\n"
                     f"    唯一匹配比例 {uniq:.1f}%")
                if uniq < 99.9:
                    s += "  ⚠ 存在多对一重复匹配，建议减小容差"
                return s

            report_parts.append(match_report("单片1", dist1, idx1, valid1))

            if self.data_base2 is not None:
                b2x, b2y, b2z = self.data_base2['x'], self.data_base2['y'], self.data_base2['z']
                tree2 = cKDTree(np.column_stack([b2x, b2y]))
                dist2, idx2 = tree2.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
                valid2 = dist2 <= tolerance
                report_parts.append(match_report("单片2", dist2, idx2, valid2))

                valid = valid1 & valid2
                final_sx = sx[valid]
                final_sy = sy[valid]
                final_gap_z = sz[valid] - b1z[idx1[valid]] - b2z[idx2[valid]]
            else:
                valid2 = None
                valid = valid1
                final_sx = sx[valid]
                final_sy = sy[valid]
                final_gap_z = sz[valid] - b1z[idx1[valid]]

            self._update_gap_diagnostic({
                'x': sx.copy(),
                'y': sy.copy(),
                'valid1': valid1.copy(),
                'valid2': valid2.copy() if valid2 is not None else None,
                'final_valid': valid.copy(),
                'tolerance': float(tolerance),
                'stack_name': self.data_stack['name'],
                'base1_name': self.data_base1['name'],
                'base2_name': self.data_base2['name'] if self.data_base2 is not None else None,
            })

            if len(final_gap_z) < 10:
                raise ValueError("容差范围内配对成功的有效点不足！\n请尝试增大【误差窗口】数值，或检查各组数据是否都执行了[平移归零]。")

            gap_name = f"GAP({self.data_stack['name']} - {self.data_base1['name']}"
            if self.data_base2 is not None:
                gap_name += f" - {self.data_base2['name']}"
            gap_name += ")"

            self.df_raw = pd.DataFrame({'Z': final_gap_z, 'X': final_sx, 'Y': final_sy})
            self._df_version += 1
            # 防止误点"应用映射"用旧文件覆盖 Gap 结果
            self.absolute_raw_df = None
            self.current_source_name = gap_name
            self.lbl_source.setText(f"当前数据: {gap_name}")
            self.import_info = {
                'file_size_bytes': 0,
                'file_size_mb': 0.0,
                'source_path': '',
                'source_sha256': '',
                'strategy': 'Gap计算结果',
                'sampled': any(rec.get('sampled', False) for rec in
                               (self.data_stack, self.data_base1, self.data_base2) if rec is not None),
                'sample_method_key': 'derived_gap',
                'extrema_preserved': all(rec.get('metric_quality', {}).get('extrema_preserved', True) for rec in
                                         (self.data_stack, self.data_base1, self.data_base2) if rec is not None),
                'import_rows': len(self.df_raw),
                'valid_rows': len(self.df_raw),
                'display_limit': self._display_limit(),
                'large_file_mode': self._bigfile_mode_label(),
                'notes': '由多层点云匹配计算生成'
            }
            self._update_import_status_label()
            self.transform_pipeline = []
            self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
            self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
            self.manual_delete_operations = []
            self.pending_delete_operation = None
            self.current_coeffs = None
            self.clear_rois(update=False)

            self.update_analysis()
            self.tabs.setCurrentIndex(self.math_tab_index)
            self._on_tab_changed(self.math_tab_index)

            msg = (f"成功配对并算出 Inner Gap！\n"
                   f"容差设定: {tolerance} mm\n"
                   f"成功对齐点数: {len(final_gap_z)}\n"
                   f"公式: 堆叠总成 - 单片1{' - 单片2' if self.data_base2 is not None else ''}\n\n"
                   f"—— 匹配质量报告 ——\n" + "\n".join(report_parts) +
                   "\n\n注: Gap 结果已写入主控分析，当前右侧显示匹配诊断；"
                   "如需查看 Gap 面型请切回[单层 / 主控分析]。")
            QMessageBox.information(self, "计算成功", msg)

        except Exception as e:
            QMessageBox.critical(self, "运算失败", f"点云对齐错误: {str(e)}")
