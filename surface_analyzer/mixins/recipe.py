"""RecipeMixin extracted from the V3.9.3 application."""

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



class RecipeMixin:
    def _current_recipe_dict(self):
        """导出当前界面参数，不包含测量数据本身。"""
        return {
            'recipe_type': 'SurfaceRxyZxyAnalyzerRecipe',
            'schema_version': 2,
            'app_version': self.APP_VERSION,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'column_mapping': {
                'x_col': self.cb_x_col.currentText() if hasattr(self, 'cb_x_col') else '',
                'y_col': self.cb_y_col.currentText() if hasattr(self, 'cb_y_col') else '',
                'z_col': self.cb_z_col.currentText() if hasattr(self, 'cb_z_col') else '',
            },
            'units': {'x_unit': self.cb_x_unit.currentText(), 'y_unit': self.cb_y_unit.currentText(), 'z_unit': self.cb_z_unit.currentText()},
            'transform_pipeline': list(self.transform_pipeline),
            'filter': {'mode_index': int(self.cb_filter.currentIndex()), 'mode_text': self.cb_filter.currentText(), 'neighbor_k': int(self.spin_k.value()), 'threshold_um': float(self.spin_thresh.value()), 'sigma_k': float(self.spin_sigma.value()), 'sigma_iters': int(self.spin_sigma_iter.value())},
            'display': {'detrended': bool(self.display_detrended)},
            'roi': {
                'enabled': bool(self.roi_enabled),
                'shapes': [dict(r) for r in self.roi_shapes],
            },
            'manual_deletion': self._manual_deletion_recipe_dict(),
            'large_file': {
                'mode': self._matching_bigfile_mode(),
                'auto_sample': bool(self.auto_sample_large_text),
                'sample_method': str(self.large_file_sample_method),
                'grid_count': int(self.large_text_grid_count),
                'stride_n': int(self.large_text_stride_n),
                'threshold_mb': int(self.large_text_threshold_mb),
                'import_limit': int(self.large_text_import_limit),
                'display_limit': int(self.display_point_limit),
                'matrix_pitch_x_um': float(self.height_matrix_pitch_x_um),
                'matrix_pitch_y_um': float(self.height_matrix_pitch_y_um),
                'matrix_z_unit': str(self.height_matrix_z_unit),
            },
            'gap': {'tolerance_mm': float(self.spin_tol.value()) if hasattr(self, 'spin_tol') else 0.05},
        }

    @staticmethod
    def _safe_set_combo_text(combo, text):
        if combo is None or text in (None, ''):
            return False
        idx = combo.findText(str(text))
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return True
        return False

    def export_recipe(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出Recipe", "Surface_Rxy_ZXY.recipe.json", "Recipe JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._current_recipe_dict(), f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"Recipe已导出: {path}", 5000)
            QMessageBox.information(
                self, "Recipe导出成功",
                "已保存单位、列映射、物料旋转、滤波、ROI、大文件策略、Gap容差和手动删除操作。\n"
                f"{self._manual_deletion_summary()}")
        except Exception as e:
            QMessageBox.critical(self, "Recipe导出失败", str(e))

    def import_recipe(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入Recipe", "", "Recipe JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                recipe = json.load(f)
            if recipe.get('recipe_type') not in (None, 'SurfaceRxyZxyAnalyzerRecipe'):
                ret = QMessageBox.question(self, "Recipe类型不一致", "该JSON不一定是Surface Rxy ZXY Analyzer Recipe，是否仍尝试导入？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
                if ret != QMessageBox.StandardButton.Yes:
                    return
            self.apply_recipe(recipe, path_hint=Path(path).name)
        except Exception as e:
            QMessageBox.critical(self, "Recipe导入失败", str(e))

    def apply_recipe(self, recipe, path_hint='', remap_current_data=True):
        """将Recipe写入UI；若尚未载入数据，列映射名称会暂存，下一次载入文件后自动匹配。"""
        self.pending_recipe = recipe
        units = recipe.get('units', {}) or {}
        self._safe_set_combo_text(self.cb_x_unit, units.get('x_unit'))
        self._safe_set_combo_text(self.cb_y_unit, units.get('y_unit'))
        self._safe_set_combo_text(self.cb_z_unit, units.get('z_unit'))
        mapping = recipe.get('column_mapping', {}) or {}
        applied_cols = []
        if self.cb_x_col.count() > 0 and self._safe_set_combo_text(self.cb_x_col, mapping.get('x_col')):
            applied_cols.append('X')
        if self.cb_y_col.count() > 0 and self._safe_set_combo_text(self.cb_y_col, mapping.get('y_col')):
            applied_cols.append('Y')
        if self.cb_z_col.count() > 0 and self._safe_set_combo_text(self.cb_z_col, mapping.get('z_col')):
            applied_cols.append('Z')
        lf = recipe.get('large_file', {}) or {}
        self.large_file_mode = str(lf.get('mode', self.large_file_mode))
        self.auto_sample_large_text = bool(lf.get('auto_sample', self.auto_sample_large_text))
        self.large_file_sample_method = str(lf.get('sample_method', self.large_file_sample_method))
        if self.large_file_sample_method == 'stride':
            self.large_file_sample_method = 'file_position'
        if self.large_file_sample_method not in ('spatial_grid', 'file_position'):
            self.large_file_sample_method = 'file_position'
        self.large_text_grid_count = int(lf.get('grid_count', self.large_text_grid_count))
        self.large_text_stride_n = int(lf.get('stride_n', self.large_text_stride_n))
        self.large_text_threshold_mb = int(lf.get('threshold_mb', self.large_text_threshold_mb))
        self.large_text_import_limit = int(lf.get('import_limit', self.large_text_import_limit))
        self.display_point_limit = int(lf.get('display_limit', self.display_point_limit))
        self.height_matrix_pitch_x_um = float(lf.get('matrix_pitch_x_um', self.height_matrix_pitch_x_um))
        self.height_matrix_pitch_y_um = float(lf.get('matrix_pitch_y_um', self.height_matrix_pitch_y_um))
        self.height_matrix_z_unit = self._normalize_unit_label(lf.get('matrix_z_unit', self.height_matrix_z_unit), self.height_matrix_z_unit)
        self.large_file_mode = self._matching_bigfile_mode()
        self.import_info['display_limit'] = self.display_point_limit
        self.import_info['sample_method'] = self._sample_method_label()
        self.import_info['grid_count'] = self.large_text_grid_count
        self.import_info['stride_n'] = self.large_text_stride_n
        self.import_info['matrix_pitch_x_um'] = self.height_matrix_pitch_x_um
        self.import_info['matrix_pitch_y_um'] = self.height_matrix_pitch_y_um
        self.import_info['matrix_z_unit'] = self.height_matrix_z_unit
        gap = recipe.get('gap', {}) or {}
        if hasattr(self, 'spin_tol'):
            self.spin_tol.setValue(float(gap.get('tolerance_mm', self.spin_tol.value())))
        if remap_current_data and self.absolute_raw_df is not None:
            self.apply_mapping(preserve_analysis_settings=True)
        valid_actions = {'CW90', 'CCW90', 'ROT180', 'SWAP', 'FLIPX', 'FLIPY', 'ORIGIN(0,0)'}
        self.transform_pipeline = [a for a in (recipe.get('transform_pipeline', []) or []) if a in valid_actions]
        self._update_pipeline_label()
        flt = recipe.get('filter', {}) or {}
        mode_index = max(0, min(int(flt.get('mode_index', 0)), self.cb_filter.count() - 1))
        self.cb_filter.blockSignals(True); self.cb_filter.setCurrentIndex(mode_index); self.cb_filter.blockSignals(False)
        self.spin_k.setValue(int(flt.get('neighbor_k', self.spin_k.value())))
        self.spin_thresh.setValue(float(flt.get('threshold_um', self.spin_thresh.value())))
        self.spin_sigma.setValue(float(flt.get('sigma_k', self.spin_sigma.value())))
        self.spin_sigma_iter.setValue(int(flt.get('sigma_iters', self.spin_sigma_iter.value())))
        self._sync_filter_enabled()
        detrended = bool((recipe.get('display', {}) or {}).get('detrended', False))
        self.chk_detrend_display.blockSignals(True); self.chk_detrend_display.setChecked(detrended); self.chk_detrend_display.blockSignals(False)
        self.display_detrended = detrended
        self.lbl_detrend_info.setText("去倾斜残差 µm" if detrended else "原始Z高度 mm")
        roi = recipe.get('roi', {}) or {}
        self.roi_enabled = bool(roi.get('enabled', self.roi_enabled))
        self.roi_shapes = self._clean_roi_shapes(roi.get('shapes', self.roi_shapes))
        if hasattr(self, 'chk_roi_enable'):
            self.chk_roi_enable.blockSignals(True)
            self.chk_roi_enable.setChecked(self.roi_enabled)
            self.chk_roi_enable.blockSignals(False)
            self._refresh_roi_ui(update=False)
        deletion_result = {'status': 'pending' if self.df_raw is None else 'empty'}
        if self.df_raw is not None:
            deletion_result = self._restore_manual_deletions(
                recipe.get('manual_deletion', {}) or {}, show_message=True)
        self._update_import_status_label()
        if self.df_raw is not None:
            self.update_analysis()
            self.pending_recipe = None
        msg = f"Recipe已导入{f'：{path_hint}' if path_hint else ''}。"
        if self.absolute_raw_df is None:
            msg += " 当前尚未载入数据，列映射将在下一次载入文件后自动尝试匹配。"
        elif applied_cols:
            msg += f" 已匹配列映射: {', '.join(applied_cols)}。"
        if deletion_result.get('status') == 'ok':
            msg += (f" 已重放 {deletion_result['operations']} 次手动删除，"
                    f"共删除 {deletion_result['deleted']:,} 点。")
        elif deletion_result.get('status') not in ('empty', 'pending'):
            msg += " 手动删除操作因校验未通过而未重放。"
        self.statusBar().showMessage(msg, 8000)
        QMessageBox.information(self, "Recipe导入完成", msg)
