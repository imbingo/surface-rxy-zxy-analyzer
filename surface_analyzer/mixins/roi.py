"""ROIMixin extracted from the V3.9.3 application."""

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



class ROIMixin:
    def _roi_is_active(self, roi_enabled=None, roi_shapes=None):
        enabled = self.roi_enabled if roi_enabled is None else bool(roi_enabled)
        shapes = self.roi_shapes if roi_shapes is None else (roi_shapes or [])
        return enabled and any(bool(r.get('enabled', True)) for r in shapes)

    @staticmethod
    def _roi_shape_label(roi):
        name = roi.get('name', 'ROI')
        if roi.get('type') == 'circle':
            return (f"{name}: 圆形 cx={roi.get('cx', 0):.4f}, cy={roi.get('cy', 0):.4f}, "
                    f"r={roi.get('radius', 0):.4f}")
        if roi.get('type') == 'smart_face':
            mode = str(roi.get('smart_mode', 'plane_residual'))
            mode_text = "同平面" if mode == 'plane_residual' else "连通"
            conn = "矩阵8邻域" if roi.get('connectivity') == 'matrix8' else "XY自动邻接"
            radius = float(roi.get('xy_radius_mm', 0.0))
            radius_text = f", 邻接r={radius:.4f}" if radius > 0 else ""
            return (f"{name}: 智能抓面 seed=({roi.get('seed_x', 0):.4f}, {roi.get('seed_y', 0):.4f}), "
                    f"Z={roi.get('seed_z', 0):.5f}, 容差={roi.get('z_tolerance_mm', 0):.4f}mm, {mode_text}, {conn}{radius_text}")
        return (f"{name}: 矩形 cx={roi.get('cx', 0):.4f}, cy={roi.get('cy', 0):.4f}, "
                f"w={roi.get('width', 0):.4f}, h={roi.get('height', 0):.4f}")

    def _clean_roi_shapes(self, shapes):
        cleaned = []
        max_id = 0
        for i, raw in enumerate(shapes or [], start=1):
            try:
                typ = str(raw.get('type', 'rect'))
                if typ not in ('rect', 'circle', 'smart_face'):
                    continue
                roi = {
                    'id': int(raw.get('id', i)),
                    'name': str(raw.get('name') or f"ROI {i}"),
                    'type': typ,
                    'enabled': bool(raw.get('enabled', True)),
                }
                if typ == 'smart_face':
                    roi.update({
                        'seed_x': float(raw.get('seed_x', raw.get('cx', 0.0))),
                        'seed_y': float(raw.get('seed_y', raw.get('cy', 0.0))),
                        'seed_z': float(raw.get('seed_z', 0.0)),
                        'z_tolerance_mm': max(float(raw.get('z_tolerance_mm', 0.2)), 1e-9),
                        'smart_mode': str(raw.get('smart_mode', 'plane_residual')),
                        'connectivity': str(raw.get('connectivity', 'auto_xy')),
                        'xy_radius_mm': max(float(raw.get('xy_radius_mm', 0.0)), 0.0),
                        'morph_dilate_iters': 0,
                        'morph_erode_iters': 0,
                        'point_count_at_create': int(raw.get('point_count_at_create', 0) or 0),
                    })
                    if roi['smart_mode'] not in ('plane_residual', 'connected'):
                        roi['smart_mode'] = 'plane_residual'
                    if roi['connectivity'] not in ('matrix8', 'auto_xy'):
                        roi['connectivity'] = 'auto_xy'
                else:
                    roi['cx'] = float(raw.get('cx', 0.0))
                    roi['cy'] = float(raw.get('cy', 0.0))
                if typ == 'circle':
                    roi['radius'] = max(float(raw.get('radius', 0.0)), 0.0)
                    if roi['radius'] <= 0:
                        continue
                elif typ == 'rect':
                    roi['width'] = max(float(raw.get('width', 0.0)), 0.0)
                    roi['height'] = max(float(raw.get('height', 0.0)), 0.0)
                    if roi['width'] <= 0 or roi['height'] <= 0:
                        continue
                max_id = max(max_id, roi['id'])
                cleaned.append(roi)
            except Exception:
                continue
        self.roi_next_id = max(self.roi_next_id, max_id + 1)
        return cleaned

    @staticmethod
    def _estimate_xy_neighbor_radius(x, y):
        xy = np.column_stack([np.asarray(x, dtype=float), np.asarray(y, dtype=float)])
        finite = np.isfinite(xy).all(axis=1)
        xy = xy[finite]
        if len(xy) < 2:
            return 0.0
        max_sample = 50000
        if len(xy) > max_sample:
            pick = np.linspace(0, len(xy) - 1, max_sample, dtype=int)
            xy = xy[pick]
        tree = cKDTree(xy)
        dist, _ = tree.query(xy, k=2)
        nn = dist[:, 1]
        nn = nn[np.isfinite(nn) & (nn > 0)]
        if len(nn) == 0:
            return 0.0
        return float(np.median(nn) * 1.8)

    def _matrix_rc_for_current_data(self):
        if self.df_raw is None or '_matrix_row' not in self.df_raw.columns or '_matrix_col' not in self.df_raw.columns:
            return None
        try:
            return (self.df_raw['_matrix_row'].to_numpy(dtype=int),
                    self.df_raw['_matrix_col'].to_numpy(dtype=int))
        except Exception:
            return None

    @staticmethod
    def _recommend_smart_tolerance_mm(z):
        values = np.asarray(z, dtype=float)
        values = values[np.isfinite(values)]
        if len(values) == 0:
            return 0.02
        if len(values) > 100000:
            pick = np.linspace(0, len(values) - 1, 100000, dtype=int)
            values = values[pick]
        p05, p95 = np.percentile(values, [5, 95])
        p01, p99 = np.percentile(values, [1, 99])
        span = max(float(p95 - p05), float(p99 - p01) * 0.35, 1e-6)
        return float(np.clip(span * 0.03, 0.002, 0.05))

    def _update_smart_tolerance_recommendation(self, z=None, apply_value=False):
        if z is None:
            if self.df_raw is None or 'Z' not in self.df_raw.columns:
                return
            z = self.df_raw['Z'].to_numpy(dtype=float)
        tol = self._recommend_smart_tolerance_mm(z)
        if hasattr(self, 'lbl_smart_tol_hint'):
            self.lbl_smart_tol_hint.setText(f"推荐: {tol:.4f} mm（按当前文件 Z 分布估算）")
        if apply_value and hasattr(self, 'spin_smart_tol'):
            self.spin_smart_tol.blockSignals(True)
            self.spin_smart_tol.setValue(tol)
            self.spin_smart_tol.blockSignals(False)

    def _smart_face_keep_mask_matrix(self, x, y, z, roi, matrix_rc):
        row_arr, col_arr = matrix_rc
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        valid_idx = np.where(finite)[0]
        if len(valid_idx) == 0:
            return np.zeros(len(z), dtype=bool)
        tol = float(roi.get('z_tolerance_mm', 0.2))
        seed_z = float(roi.get('seed_z', 0.0))
        seed_dist = (x[valid_idx] - float(roi.get('seed_x', 0.0))) ** 2 + (y[valid_idx] - float(roi.get('seed_y', 0.0))) ** 2
        seed_idx = int(valid_idx[int(np.argmin(seed_dist))])
        cell_to_idx = {(int(row_arr[i]), int(col_arr[i])): int(i) for i in valid_idx}
        start = (int(row_arr[seed_idx]), int(col_arr[seed_idx]))
        visited = set([start])
        queue = deque([start])
        while queue:
            rr, cc = queue.popleft()
            cur_idx = cell_to_idx[(rr, cc)]
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nb = (rr + dr, cc + dc)
                    if nb in visited or nb not in cell_to_idx:
                        continue
                    nb_idx = cell_to_idx[nb]
                    if abs(float(z[nb_idx]) - float(z[cur_idx])) > tol and abs(float(z[nb_idx]) - seed_z) > tol:
                        continue
                    visited.add(nb)
                    queue.append(nb)
        keep = np.zeros(len(z), dtype=bool)
        if visited:
            keep[[cell_to_idx[cell] for cell in visited]] = True

        return keep

    def _smart_face_keep_mask_auto_xy(self, x, y, z, roi, update_radius=False):
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        finite_idx = np.where(finite)[0]
        if len(finite_idx) == 0:
            return np.zeros(len(z), dtype=bool)
        tol = float(roi.get('z_tolerance_mm', 0.2))
        seed_z = float(roi.get('seed_z', 0.0))
        radius = float(roi.get('xy_radius_mm', 0.0) or 0.0)
        if radius <= 0:
            radius = self._estimate_xy_neighbor_radius(x[finite], y[finite])
            if update_radius:
                roi['xy_radius_mm'] = float(radius)
        if radius <= 0:
            seed_dist = (x[finite_idx] - float(roi.get('seed_x', 0.0))) ** 2 + (y[finite_idx] - float(roi.get('seed_y', 0.0))) ** 2
            keep = np.zeros(len(z), dtype=bool)
            keep[int(finite_idx[int(np.argmin(seed_dist))])] = True
            return keep

        xy = np.column_stack([x[finite_idx], y[finite_idx]])
        tree = cKDTree(xy)
        seed_xy = np.array([[float(roi.get('seed_x', 0.0)), float(roi.get('seed_y', 0.0))]])
        _, seed_local = tree.query(seed_xy, k=1)
        seed_local = int(np.ravel(seed_local)[0])
        visited = np.zeros(len(finite_idx), dtype=bool)
        visited[seed_local] = True
        queue = deque([seed_local])
        while queue:
            loc = queue.popleft()
            for nb in tree.query_ball_point(xy[loc], r=radius):
                if visited[nb]:
                    continue
                cur_idx = finite_idx[loc]
                nb_idx = finite_idx[nb]
                if abs(float(z[nb_idx]) - float(z[cur_idx])) > tol and abs(float(z[nb_idx]) - seed_z) > tol:
                    continue
                visited[nb] = True
                queue.append(int(nb))
        keep = np.zeros(len(z), dtype=bool)
        keep[finite_idx[visited]] = True

        return keep

    def _smart_face_keep_mask_plane_residual(self, x, y, z, roi, update_radius=False):
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        finite_idx = np.where(finite)[0]
        if len(finite_idx) < 3:
            return np.zeros(len(z), dtype=bool)
        tol = float(roi.get('z_tolerance_mm', 0.02))
        radius = float(roi.get('xy_radius_mm', 0.0) or 0.0)
        if radius <= 0:
            radius = self._estimate_xy_neighbor_radius(x[finite], y[finite])
            if update_radius:
                roi['xy_radius_mm'] = float(radius)

        xy = np.column_stack([x[finite_idx], y[finite_idx]])
        tree = cKDTree(xy)
        seed_xy = np.array([[float(roi.get('seed_x', 0.0)), float(roi.get('seed_y', 0.0))]])
        _, seed_local = tree.query(seed_xy, k=1)
        seed_local = int(np.ravel(seed_local)[0])

        k = min(len(finite_idx), max(30, min(300, int(np.sqrt(len(finite_idx))) * 2)))
        if radius > 0:
            local = tree.query_ball_point(xy[seed_local], r=radius * 8.0)
            if len(local) < 12:
                _, local = tree.query(xy[seed_local], k=k)
        else:
            _, local = tree.query(xy[seed_local], k=k)
        local = np.asarray(local, dtype=int).ravel()
        local_idx = finite_idx[local]
        if len(local_idx) < 3:
            return np.zeros(len(z), dtype=bool)

        try:
            coeffs = self.fit_plane(x[local_idx], y[local_idx], z[local_idx])
        except Exception:
            return np.zeros(len(z), dtype=bool)
        residual = z - (coeffs[0] * x + coeffs[1] * y + coeffs[2])
        candidate = finite & (np.abs(residual) <= tol)
        candidate_idx = np.flatnonzero(candidate)
        if len(candidate_idx) == 0 or radius <= 0:
            return np.zeros(len(z), dtype=bool)

        # 平面残差只能判断“像不像同一平面”，不能判断 XY 上是否属于同一片区域。
        # 再取种子所在的连通分量，防止跨越狭缝选中远处共面孤岛。
        candidate_xy = np.column_stack([x[candidate_idx], y[candidate_idx]])
        candidate_tree = cKDTree(candidate_xy)
        _, start_local = candidate_tree.query(seed_xy, k=1)
        start_local = int(np.ravel(start_local)[0])
        visited = np.zeros(len(candidate_idx), dtype=bool)
        visited[start_local] = True
        queue = deque([start_local])
        while queue:
            loc = queue.popleft()
            for nb in candidate_tree.query_ball_point(candidate_xy[loc], r=radius):
                if visited[nb]:
                    continue
                visited[nb] = True
                queue.append(int(nb))
        keep = np.zeros(len(z), dtype=bool)
        keep[candidate_idx[visited]] = True
        return keep

    def _smart_face_keep_mask_for_arrays(self, x, y, z, roi, matrix_rc=None, update_radius=False):
        if z is None:
            return np.zeros(len(x), dtype=bool)
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        z = np.asarray(z, dtype=float)
        if str(roi.get('smart_mode', 'plane_residual')) == 'plane_residual':
            return self._smart_face_keep_mask_plane_residual(x, y, z, roi, update_radius=update_radius)
        if roi.get('connectivity') == 'matrix8' and matrix_rc is not None:
            try:
                return self._smart_face_keep_mask_matrix(x, y, z, roi, matrix_rc)
            except Exception:
                pass
        return self._smart_face_keep_mask_auto_xy(x, y, z, roi, update_radius=update_radius)

    def _roi_keep_mask_for_arrays(self, x, y, z=None, roi_shapes=None, roi_enabled=None, matrix_rc=None):
        shapes = self.roi_shapes if roi_shapes is None else (roi_shapes or [])
        if not self._roi_is_active(roi_enabled, shapes):
            return np.ones(len(x), dtype=bool)
        keep = np.zeros(len(x), dtype=bool)
        for roi in shapes:
            if not roi.get('enabled', True):
                continue
            cx = float(roi.get('cx', 0.0))
            cy = float(roi.get('cy', 0.0))
            if roi.get('type') == 'circle':
                r = float(roi.get('radius', 0.0))
                keep |= ((x - cx) ** 2 + (y - cy) ** 2) <= (r ** 2)
            elif roi.get('type') == 'smart_face':
                keep |= self._smart_face_keep_mask_for_arrays(x, y, z, roi, matrix_rc=matrix_rc)
            else:
                hw = float(roi.get('width', 0.0)) / 2.0
                hh = float(roi.get('height', 0.0)) / 2.0
                keep |= (x >= cx - hw) & (x <= cx + hw) & (y >= cy - hh) & (y <= cy + hh)
        return keep

    def _sync_roi_input_state(self):
        if not hasattr(self, 'cb_roi_shape'):
            return
        shape_idx = self.cb_roi_shape.currentIndex()
        is_circle = shape_idx == 1
        is_smart = shape_idx == 2
        if hasattr(self, 'roi_advanced_widget'):
            self.roi_advanced_widget.setVisible(self.chk_roi_advanced.isChecked())
        for w in (self.lbl_roi_w, self.spin_roi_w, self.lbl_roi_h, self.spin_roi_h):
            w.setEnabled((not is_circle) and (not is_smart))
        for w in (self.lbl_roi_r, self.spin_roi_r):
            w.setEnabled(is_circle and not is_smart)
        for w in (self.lbl_smart_mode, self.cb_smart_mode, self.lbl_smart_tol,
                  self.spin_smart_tol, self.lbl_smart_tol_hint):
            w.setEnabled(is_smart)
        for w in (self.lbl_smart_dilate, self.spin_smart_dilate, self.lbl_smart_erode, self.spin_smart_erode):
            w.setEnabled(False)
        self.btn_roi_add_input.setEnabled(not is_smart)
        if self.btn_roi_mouse.isChecked():
            self.btn_roi_mouse.setText("退出智能抓面" if is_smart else "退出框选 ROI")
        else:
            self.btn_roi_mouse.setText("开始智能抓面" if is_smart else "开始框选 ROI")
        if self.selection_mode in ('roi_rect', 'roi_circle', 'roi_smart'):
            self.selection_mode = 'roi_smart' if is_smart else 'roi_circle' if is_circle else 'roi_rect'
            self.statusBar().showMessage(
                "智能抓面模式：在 XY 图点击种子点；默认按同平面残差抓取，不自动补洞。"
                if is_smart else
                f"ROI 连续框选模式: {'圆形' if is_circle else '矩形'}。在 XY 图中继续拖拽添加区域。", 5000)

    def _on_roi_changed(self):
        if hasattr(self, 'chk_roi_enable'):
            self.roi_enabled = self.chk_roi_enable.isChecked()
        self._refresh_roi_ui(update=False)
        if self.df_raw is not None:
            self.update_analysis()

    def _refresh_roi_ui(self, update=False):
        if not hasattr(self, 'lbl_roi_info'):
            return
        current = self.cb_roi_select.currentData()
        self.cb_roi_select.blockSignals(True)
        self.cb_roi_select.clear()
        tx = ty = tz = None
        if self.df_raw is not None:
            try:
                tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            except Exception:
                tx = ty = tz = None
        enabled_count = 0
        for roi in self.roi_shapes:
            if roi.get('enabled', True):
                enabled_count += 1
            count_text = ""
            if tx is not None and ty is not None:
                count_roi = dict(roi)
                count_roi['enabled'] = True
                count = int(self._roi_keep_mask_for_arrays(
                    tx, ty, tz, [count_roi], True, self._matrix_rc_for_current_data()).sum())
                count_text = f" | {count:,}点"
            state = "启用" if roi.get('enabled', True) else "禁用"
            label = f"{state} {self._roi_shape_label(roi)}{count_text}"
            self.cb_roi_select.addItem(label, roi.get('id'))
        if current is not None:
            idx = self.cb_roi_select.findData(current)
            if idx >= 0:
                self.cb_roi_select.setCurrentIndex(idx)
        self.cb_roi_select.blockSignals(False)
        active = self._roi_is_active()
        if active and self.last_roi_keep_count is not None:
            head = f"ROI: 开启 | {enabled_count}/{len(self.roi_shapes)} 个启用 | 合并保留 {self.last_roi_keep_count:,} 点"
        elif active:
            head = f"ROI: 开启 | {enabled_count}/{len(self.roi_shapes)} 个启用"
        elif self.roi_enabled:
            head = "ROI: 开启 | 尚无启用区域"
        else:
            head = "ROI: 关闭"
        self.lbl_roi_info.setText(head + (" | 未定义" if not self.roi_shapes else ""))
        if update and self.df_raw is not None:
            self.update_analysis()

    def _add_roi_shape(self, roi, keep_roi_mode=False):
        roi['id'] = int(self.roi_next_id)
        roi['name'] = f"ROI {self.roi_next_id}"
        roi['enabled'] = True
        self.roi_next_id += 1
        self.roi_shapes.append(roi)
        self.roi_enabled = True
        if hasattr(self, 'chk_roi_enable'):
            self.chk_roi_enable.blockSignals(True)
            self.chk_roi_enable.setChecked(True)
            self.chk_roi_enable.blockSignals(False)
        if not keep_roi_mode:
            self.set_delete_selection_mode(show_message=False)
        self._refresh_roi_ui(update=True)
        self.statusBar().showMessage(f"已添加 {self._roi_shape_label(roi)}", 5000)

    def add_roi_from_inputs(self):
        if self.cb_roi_shape.currentIndex() == 2:
            self.statusBar().showMessage("智能抓面需要在 XY 图点击种子点生成 ROI。", 5000)
            return
        cx = float(self.spin_roi_cx.value())
        cy = float(self.spin_roi_cy.value())
        if self.cb_roi_shape.currentIndex() == 1:
            self._add_roi_shape({'type': 'circle', 'cx': cx, 'cy': cy, 'radius': float(self.spin_roi_r.value())})
        else:
            self._add_roi_shape({
                'type': 'rect', 'cx': cx, 'cy': cy,
                'width': float(self.spin_roi_w.value()), 'height': float(self.spin_roi_h.value())
            })

    def start_mouse_roi(self, checked=None):
        checked = self.btn_roi_mouse.isChecked() if checked is None else bool(checked)
        if not checked:
            self.set_delete_selection_mode(show_message=True)
            return
        self.selection_mode = 'roi_smart' if self.cb_roi_shape.currentIndex() == 2 else 'roi_circle' if self.cb_roi_shape.currentIndex() == 1 else 'roi_rect'
        self.btn_roi_mouse.setText("退出智能抓面" if self.selection_mode == 'roi_smart' else "退出框选 ROI")
        if self.temp_selected_mask is not None:
            self.temp_selected_mask.fill(False)
            self.update_plots_only()
        if self.selection_mode == 'roi_smart':
            self.statusBar().showMessage("智能抓面模式已开启：请在 XY 俯视图中点击种子点，可连续添加多个 ROI。", 8000)
        else:
            self.statusBar().showMessage("ROI 连续框选模式已开启：请在 XY 俯视图中拖拽，可连续添加多个 ROI。", 8000)

    def set_delete_selection_mode(self, show_message=True):
        self.selection_mode = 'delete'
        self.pending_delete_operation = None
        if hasattr(self, 'btn_roi_mouse'):
            self.btn_roi_mouse.blockSignals(True)
            self.btn_roi_mouse.setChecked(False)
            self.btn_roi_mouse.setText("开始智能抓面" if self.cb_roi_shape.currentIndex() == 2 else "开始框选 ROI")
            self.btn_roi_mouse.blockSignals(False)
        if show_message:
            self.statusBar().showMessage("已退出 ROI 框选，恢复为删除点框选模式。", 4000)

    def _selected_roi_index(self):
        if not hasattr(self, 'cb_roi_select') or self.cb_roi_select.currentIndex() < 0:
            return None
        roi_id = self.cb_roi_select.currentData()
        for i, roi in enumerate(self.roi_shapes):
            if roi.get('id') == roi_id:
                return i
        return None

    def toggle_selected_roi(self):
        idx = self._selected_roi_index()
        if idx is None:
            return
        self.roi_shapes[idx]['enabled'] = not self.roi_shapes[idx].get('enabled', True)
        self._refresh_roi_ui(update=True)

    def delete_selected_roi(self, *_args):
        idx = self._selected_roi_index()
        if idx is None:
            return
        del self.roi_shapes[idx]
        if not self.roi_shapes:
            self.roi_enabled = False
            self.last_roi_keep_count = None
            if hasattr(self, 'chk_roi_enable'):
                self.chk_roi_enable.blockSignals(True)
                self.chk_roi_enable.setChecked(False)
                self.chk_roi_enable.blockSignals(False)
        self._refresh_roi_ui(update=True)

    def clear_rois(self, checked=None, update=True):
        self.roi_shapes = []
        self.roi_enabled = False
        self.last_roi_keep_count = None
        self.set_delete_selection_mode(show_message=False)
        if hasattr(self, 'chk_roi_enable'):
            self.chk_roi_enable.blockSignals(True)
            self.chk_roi_enable.setChecked(False)
            self.chk_roi_enable.blockSignals(False)
        self._refresh_roi_ui(update=update)

    def _roi_report_info(self, tx=None, ty=None, tz=None, roi_enabled=None, roi_shapes=None, matrix_rc=None):
        shapes = [dict(r) for r in (roi_shapes if roi_shapes is not None else self.roi_shapes)]
        enabled = self.roi_enabled if roi_enabled is None else bool(roi_enabled)
        active = self._roi_is_active(enabled, shapes)
        keep_count = None
        if active and tx is not None and ty is not None:
            keep_count = int(self._roi_keep_mask_for_arrays(tx, ty, tz, shapes, enabled, matrix_rc).sum())
        summary = "关闭" if not active else f"开启 | 启用 {sum(bool(r.get('enabled', True)) for r in shapes)}/{len(shapes)} 个"
        if keep_count is not None:
            summary += f" | 合并保留 {keep_count:,} 点"
        shape_lines = [self._roi_shape_label(r) for r in shapes if r.get('enabled', True)]
        return {
            'enabled': active,
            'summary': summary,
            'shape_lines': shape_lines,
            'keep_count': keep_count,
            'shapes': shapes,
            'roi_enabled': enabled,
        }

    def _draw_roi_overlays(self, ax, roi_shapes=None, roi_enabled=None, report=False):
        shapes = roi_shapes if roi_shapes is not None else self.roi_shapes
        if not shapes:
            return
        active = self._roi_is_active(roi_enabled, shapes)
        if not active and report:
            return
        for roi in shapes:
            enabled = bool(roi.get('enabled', True))
            if report and not enabled:
                continue
            edge = '#2f6db0' if enabled else '#94a3b8'
            style = '-' if enabled else '--'
            alpha = 0.95 if enabled else 0.55
            cx = float(roi.get('cx', 0.0))
            cy = float(roi.get('cy', 0.0))
            if roi.get('type') == 'circle':
                patch = MplCircle((cx, cy), float(roi.get('radius', 0.0)), fill=False,
                                  edgecolor=edge, linewidth=1.8, linestyle=style, alpha=alpha)
            elif roi.get('type') == 'smart_face':
                sx = float(roi.get('seed_x', 0.0))
                sy = float(roi.get('seed_y', 0.0))
                ax.scatter([sx], [sy], marker='x', s=80, c=edge, linewidths=2.0,
                           alpha=alpha, zorder=4)
                radius = float(roi.get('xy_radius_mm', 0.0) or 0.0)
                patch = MplCircle((sx, sy), max(radius, 0.02), fill=False,
                                  edgecolor=edge, linewidth=1.4, linestyle=':',
                                  alpha=min(alpha, 0.75))
            else:
                w = float(roi.get('width', 0.0))
                h = float(roi.get('height', 0.0))
                patch = MplRectangle((cx - w / 2.0, cy - h / 2.0), w, h, fill=False,
                                     edgecolor=edge, linewidth=1.8, linestyle=style, alpha=alpha)
            patch.set_zorder(3)
            ax.add_patch(patch)

    def _manual_delete_sample_signature(self):
        info = getattr(self, 'import_info', {}) or {}
        return {
            'file_size_bytes': int(info.get('file_size_bytes', 0) or 0),
            'import_rows': int(info.get('import_rows', 0) or 0),
            'valid_rows': int(info.get('valid_rows', len(self.df_raw) if self.df_raw is not None else 0) or 0),
            'sampled': bool(info.get('sampled', False)),
            'sample_method_key': str(info.get('sample_method_key', 'full')),
            'grid_count': int(info.get('grid_count', 0) or 0),
            'stride_n': int(info.get('stride_n', 0) or 0),
        }

    def _build_manual_delete_operation(self, view_type, x1, y1, x2, y2):
        coeffs = None
        if self.display_detrended and self.current_coeffs is not None:
            coeffs = [float(v) for v in self.current_coeffs]
        return {
            'schema_version': 1,
            'operation_id': len(getattr(self, 'manual_delete_operations', [])) + 1,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'view': str(view_type).upper(),
            'bounds': {
                'x_min': float(min(x1, x2)),
                'x_max': float(max(x1, x2)),
                'y_min': float(min(y1, y2)),
                'y_max': float(max(y1, y2)),
            },
            'axis_units': 'mm/mm' if str(view_type).upper() == 'XY' else
                          ('mm/µm' if self.display_detrended else 'mm/mm'),
            'transform_pipeline': list(self.transform_pipeline),
            'display_mode': 'detrended_um' if self.display_detrended else 'raw_z_mm',
            'display_plane_coeffs': coeffs,
            'filter': {
                'mode_index': int(self.cb_filter.currentIndex()),
                'neighbor_k': int(self.spin_k.value()),
                'threshold_um': float(self.spin_thresh.value()),
                'sigma_k': float(self.spin_sigma.value()),
                'sigma_iters': int(self.spin_sigma_iter.value()),
            },
            'roi': {
                'enabled': bool(self.roi_enabled),
                'shapes': [dict(r) for r in self.roi_shapes],
            },
            'sample_signature': self._manual_delete_sample_signature(),
            'source_name': str(self.current_source_name or ''),
            'source_sha256': '',
            'selected_count': 0,
        }

    def _clean_manual_delete_operations(self, operations):
        cleaned = []
        valid_actions = {'CW90', 'CCW90', 'ROT180', 'SWAP', 'FLIPX', 'FLIPY', 'ORIGIN(0,0)'}
        for raw in operations or []:
            if not isinstance(raw, dict):
                continue
            view = str(raw.get('view', '')).upper()
            if view not in ('XY', 'XZ', 'YZ'):
                continue
            bounds = raw.get('bounds', {}) or {}
            try:
                x_min = float(bounds['x_min']); x_max = float(bounds['x_max'])
                y_min = float(bounds['y_min']); y_max = float(bounds['y_max'])
            except (KeyError, TypeError, ValueError):
                continue
            if not np.isfinite([x_min, x_max, y_min, y_max]).all():
                continue
            coeffs = raw.get('display_plane_coeffs')
            if coeffs is not None:
                try:
                    coeffs = [float(v) for v in coeffs]
                except (TypeError, ValueError):
                    coeffs = None
                if coeffs is not None and (len(coeffs) != 3 or not np.isfinite(coeffs).all()):
                    coeffs = None
            flt = raw.get('filter', {}) or {}
            roi = raw.get('roi', {}) or {}
            cleaned.append({
                'schema_version': 1,
                'operation_id': int(raw.get('operation_id', len(cleaned) + 1)),
                'created_at': str(raw.get('created_at', '')),
                'view': view,
                'bounds': {'x_min': min(x_min, x_max), 'x_max': max(x_min, x_max),
                           'y_min': min(y_min, y_max), 'y_max': max(y_min, y_max)},
                'axis_units': str(raw.get('axis_units', 'mm/mm')),
                'transform_pipeline': [a for a in (raw.get('transform_pipeline', []) or []) if a in valid_actions],
                'display_mode': 'detrended_um' if raw.get('display_mode') == 'detrended_um' else 'raw_z_mm',
                'display_plane_coeffs': coeffs,
                'filter': {
                    'mode_index': max(0, min(3, int(flt.get('mode_index', 0)))),
                    'neighbor_k': max(3, int(flt.get('neighbor_k', 12))),
                    'threshold_um': max(0.0, float(flt.get('threshold_um', 5.0))),
                    'sigma_k': max(0.1, float(flt.get('sigma_k', 3.0))),
                    'sigma_iters': max(1, int(flt.get('sigma_iters', 5))),
                },
                'roi': {
                    'enabled': bool(roi.get('enabled', False)),
                    'shapes': self._clean_roi_shapes(roi.get('shapes', [])),
                },
                'sample_signature': dict(raw.get('sample_signature', {}) or {}),
                'source_name': str(raw.get('source_name', '')),
                'source_sha256': str(raw.get('source_sha256', '')).lower(),
                'selected_count': max(0, int(raw.get('selected_count', 0) or 0)),
            })
        return cleaned

    def _manual_delete_mask_for_operation(self, operation, current_manual_mask=None):
        if self.df_raw is None:
            return np.array([], dtype=bool)
        op = self._clean_manual_delete_operations([operation])
        if not op:
            return np.zeros(len(self.df_raw), dtype=bool)
        op = op[0]
        x = self.df_raw['X'].to_numpy(dtype=float)
        y = self.df_raw['Y'].to_numpy(dtype=float)
        z = self.df_raw['Z'].to_numpy(dtype=float)
        tx, ty, tz = self._apply_transform_pipeline(x, y, z, op['transform_pipeline'])
        scope = np.asarray(current_manual_mask if current_manual_mask is not None else
                           np.ones(len(z), dtype=bool), dtype=bool).copy()
        scope &= np.isfinite(tx) & np.isfinite(ty) & np.isfinite(tz)
        roi = op['roi']
        if self._roi_is_active(roi.get('enabled'), roi.get('shapes')):
            scope &= self._roi_keep_mask_for_arrays(
                tx, ty, tz, roi.get('shapes'), roi.get('enabled'), self._matrix_rc_for_current_data())
        idx = np.flatnonzero(scope)
        if len(idx) == 0:
            return np.zeros(len(z), dtype=bool)
        flt = op['filter']
        keep = self.filter_keep_mask(
            tx[idx], ty[idx], tz[idx], flt['mode_index'],
            k=flt['neighbor_k'], threshold_mm=flt['threshold_um'] * 1e-3,
            sigma_k=flt['sigma_k'], sigma_iters=flt['sigma_iters'])
        filtered_scope = np.zeros(len(z), dtype=bool)
        filtered_scope[idx[keep]] = True

        plot_z = tz
        if op['display_mode'] == 'detrended_um' and op['display_plane_coeffs'] is not None:
            c = op['display_plane_coeffs']
            plot_z = (tz - (c[0] * tx + c[1] * ty + c[2])) * 1000.0
        b = op['bounds']
        if op['view'] == 'XY':
            in_box = (tx >= b['x_min']) & (tx <= b['x_max']) & (ty >= b['y_min']) & (ty <= b['y_max'])
        elif op['view'] == 'XZ':
            in_box = (tx >= b['x_min']) & (tx <= b['x_max']) & (plot_z >= b['y_min']) & (plot_z <= b['y_max'])
        else:
            in_box = (ty >= b['x_min']) & (ty <= b['x_max']) & (plot_z >= b['y_min']) & (plot_z <= b['y_max'])
        return filtered_scope & in_box

    def _manual_deletion_recipe_dict(self):
        operations = self._clean_manual_delete_operations(getattr(self, 'manual_delete_operations', []))
        source_hash = self._ensure_source_sha256() if operations else str(
            (getattr(self, 'import_info', {}) or {}).get('source_sha256') or '')
        for operation in operations:
            operation['source_sha256'] = source_hash
        return {
            'schema_version': 1,
            'source_name': str(self.current_source_name or ''),
            'source_sha256': source_hash,
            'source_size_bytes': int((getattr(self, 'import_info', {}) or {}).get('file_size_bytes', 0) or 0),
            'sample_signature': self._manual_delete_sample_signature(),
            'operations': operations,
        }

    def _manual_deletion_summary(self):
        operations = getattr(self, 'manual_delete_operations', []) or []
        deleted = int((~self.manual_mask).sum()) if self.manual_mask is not None else 0
        source_hash = str((getattr(self, 'import_info', {}) or {}).get('source_sha256') or '')
        return f"{len(operations)} 次操作 | 删除 {deleted:,} 点 | SHA-256 {source_hash[:12] + '…' if source_hash else '未记录'}"

    def _restore_manual_deletions(self, block, show_message=True):
        operations = self._clean_manual_delete_operations((block or {}).get('operations', []))
        self.manual_delete_operations = []
        self.pending_delete_operation = None
        if self.df_raw is None or not operations:
            return {'status': 'empty', 'operations': 0, 'deleted': 0}
        expected_hash = str((block or {}).get('source_sha256') or operations[0].get('source_sha256') or '').lower()
        actual_hash = self._ensure_source_sha256()
        if not expected_hash or not actual_hash or expected_hash != actual_hash:
            self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
            msg = ("Recipe中的手动删除未重放：源文件SHA-256不一致或缺失。\n"
                   f"Recipe: {expected_hash[:12] or '--'}\n当前: {actual_hash[:12] or '--'}")
            if show_message:
                QMessageBox.warning(self, '手动删除未重放', msg)
            return {'status': 'hash_mismatch', 'operations': 0, 'deleted': 0, 'message': msg}

        expected_signature = dict((block or {}).get('sample_signature', {}) or {})
        current_signature = self._manual_delete_sample_signature()
        signature_keys = ('file_size_bytes', 'import_rows', 'valid_rows', 'sampled',
                          'sample_method_key', 'grid_count', 'stride_n')
        mismatch = [key for key in signature_keys if key in expected_signature and
                    expected_signature.get(key) != current_signature.get(key)]
        if mismatch:
            self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
            msg = f"Recipe中的手动删除未重放：导入/抽样签名不同（{', '.join(mismatch)}）。"
            if show_message:
                QMessageBox.warning(self, '手动删除未重放', msg)
            return {'status': 'sample_mismatch', 'operations': 0, 'deleted': 0, 'message': msg}

        replay_mask = np.ones(len(self.df_raw), dtype=bool)
        replayed = []
        for operation in operations:
            if operation.get('source_sha256') and operation['source_sha256'] != actual_hash:
                replay_mask[:] = True
                return {'status': 'operation_hash_mismatch', 'operations': 0, 'deleted': 0}
            selected = self._manual_delete_mask_for_operation(operation, replay_mask)
            count = int(selected.sum())
            expected_count = int(operation.get('selected_count', 0) or 0)
            if expected_count and count != expected_count:
                self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
                msg = (f"Recipe中的手动删除未重放：第 {operation['operation_id']} 次操作点数不一致，"
                       f"原记录 {expected_count}，当前 {count}。")
                if show_message:
                    QMessageBox.warning(self, '手动删除未重放', msg)
                return {'status': 'count_mismatch', 'operations': 0, 'deleted': 0, 'message': msg}
            if int((replay_mask & ~selected).sum()) < 3:
                self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
                return {'status': 'too_few_points', 'operations': 0, 'deleted': 0}
            replay_mask &= ~selected
            operation['selected_count'] = count
            operation['source_sha256'] = actual_hash
            replayed.append(operation)
        self.manual_mask = replay_mask
        self.manual_delete_operations = replayed
        deleted = int((~replay_mask).sum())
        self.statusBar().showMessage(f"Recipe已重放 {len(replayed)} 次手动删除，共删除 {deleted:,} 点", 10000)
        return {'status': 'ok', 'operations': len(replayed), 'deleted': deleted}

    def on_canvas_click(self, event):
        if self.selection_mode != 'roi_smart' or self.df_raw is None:
            return
        if event.inaxes != self.canvas.ax_xy or event.xdata is None or event.ydata is None:
            return
        if getattr(event, 'button', 1) != 1:
            return
        self.add_smart_face_roi_from_seed(float(event.xdata), float(event.ydata))

    def add_smart_face_roi_from_seed(self, px, py):
        if self.df_raw is None:
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        base_mask = self.manual_mask if self.manual_mask is not None else np.ones(len(tz), dtype=bool)
        finite_idx = np.where(base_mask & np.isfinite(tx) & np.isfinite(ty) & np.isfinite(tz))[0]
        if len(finite_idx) < 3:
            self.statusBar().showMessage("有效点不足，无法智能抓面。", 6000)
            return
        dist2 = (tx[finite_idx] - px) ** 2 + (ty[finite_idx] - py) ** 2
        seed_idx = int(finite_idx[int(np.argmin(dist2))])
        connectivity = 'matrix8' if self._matrix_rc_for_current_data() is not None else 'auto_xy'
        roi = {
            'type': 'smart_face',
            'seed_x': float(tx[seed_idx]),
            'seed_y': float(ty[seed_idx]),
            'seed_z': float(tz[seed_idx]),
            'z_tolerance_mm': float(self.spin_smart_tol.value()),
            'smart_mode': str(self.cb_smart_mode.currentData()) if hasattr(self, 'cb_smart_mode') else 'plane_residual',
            'connectivity': connectivity,
            'xy_radius_mm': 0.0,
            'morph_dilate_iters': 0,
            'morph_erode_iters': 0,
        }
        matrix_rc = self._matrix_rc_for_current_data()
        keep = self._smart_face_keep_mask_for_arrays(
            tx, ty, tz, roi, matrix_rc=matrix_rc, update_radius=True) & base_mask
        count = int(np.sum(keep))
        if count < 3:
            self.statusBar().showMessage(
                f"智能抓面只得到 {count} 点，未添加。请调大抓面容差或点击面内更稳定的位置。", 8000)
            return
        roi['point_count_at_create'] = count
        self._add_roi_shape(roi, keep_roi_mode=True)
        mode_text = "同平面残差" if roi.get('smart_mode') == 'plane_residual' else "连通抓取"
        conn_text = "矩阵8邻域" if roi['connectivity'] == 'matrix8' else f"XY邻接r={roi.get('xy_radius_mm', 0):.4f}mm"
        self.statusBar().showMessage(
            f"已添加智能抓面 ROI: {count:,} 点 | 容差 {roi['z_tolerance_mm']:.4f} mm | "
            f"{mode_text} | {conn_text}",
            8000)

    def on_select(self, eclick, erelease, view_type):
        if self.df_raw is None or self.active_idx is None: return
        x1, y1, x2, y2 = eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata
        if None in (x1, y1, x2, y2): return

        if self.selection_mode == 'roi_smart':
            return

        if self.selection_mode in ('roi_rect', 'roi_circle'):
            if view_type != 'XY':
                self.statusBar().showMessage("ROI 只能在 XY 俯视图中框选；XZ/YZ 保留给删除点框选。", 5000)
                return
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            w = abs(x2 - x1)
            h = abs(y2 - y1)
            if self.selection_mode == 'roi_circle':
                r = max(w, h) / 2.0
                if r <= 0:
                    return
                self._add_roi_shape({'type': 'circle', 'cx': cx, 'cy': cy, 'radius': r}, keep_roi_mode=True)
            else:
                if w <= 0 or h <= 0:
                    return
                self._add_roi_shape({'type': 'rect', 'cx': cx, 'cy': cy, 'width': w, 'height': h}, keep_roi_mode=True)
            return

        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        plot_z_all, _, _ = self._get_plot_z(tx, ty, tz)
        ax, ay, az = tx[self.active_idx], ty[self.active_idx], plot_z_all[self.active_idx]
        if view_type == 'XY': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (ay >= min(y1, y2)) & (ay <= max(y1, y2))
        elif view_type == 'XZ': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        elif view_type == 'YZ': in_box = (ay >= min(x1, x2)) & (ay <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        else: return
        self.temp_selected_mask.fill(False)
        self.temp_selected_mask[self.active_idx[in_box]] = True
        self.pending_delete_operation = self._build_manual_delete_operation(view_type, x1, y1, x2, y2)
        self.pending_delete_operation['selected_count'] = int(in_box.sum())
        self.update_plots_only()

    def setup_selectors(self):
        # 断开旧选择器回调，避免重复触发/内存累积
        for sel in self.selectors:
            try:
                sel.disconnect_events()
            except Exception:
                pass
        self.selectors = []
        for ax, vt in zip([self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz], ['XY', 'XZ', 'YZ']):
            sel = RectangleSelector(ax, lambda e, r, v=vt: self.on_select(e, r, v),
                                    useblit=True, button=[1],
                                    props=dict(facecolor='red', alpha=0.15, edgecolor='red'))
            self.selectors.append(sel)

    def apply_manual_deletion(self):
        if self.temp_selected_mask is None or self.temp_selected_mask.sum() == 0: return
        if (self.manual_mask & ~self.temp_selected_mask).sum() < 3:
            QMessageBox.warning(self, "无法删除", "删除后有效点将少于 3 个，无法拟合平面。已取消本次删除。")
            return
        operation = dict(self.pending_delete_operation or {})
        source_path = str((getattr(self, 'import_info', {}) or {}).get('source_path') or '')
        if source_path:
            try:
                source_hash = self._ensure_source_sha256()
            except Exception as exc:
                QMessageBox.critical(self, '无法记录删除操作', f"源文件SHA-256计算失败：{exc}")
                return
            if not source_hash:
                QMessageBox.critical(self, '无法记录删除操作', '未能取得源文件SHA-256，已取消删除。')
                return
            operation['source_sha256'] = source_hash
        operation['selected_count'] = int(self.temp_selected_mask.sum())
        self.manual_mask &= (~self.temp_selected_mask)
        if operation:
            self.manual_delete_operations.append(operation)
        self.pending_delete_operation = None
        self.temp_selected_mask.fill(False)
        self.update_analysis()
        self.statusBar().showMessage(
            f"已记录第 {len(self.manual_delete_operations)} 次手动删除；{self._manual_deletion_summary()}", 8000)
