"""ROIMixin extracted from the V3.9.3 application."""

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
    QStackedWidget, QSizeGrip, QMenu,
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen, QCursor
from scipy.spatial import cKDTree
from ..polynomial import evaluate_polynomial_surface
from ..smart_roi import build_adaptive_topology, grow_surface_roi, grow_surface_roi_v2
from ..workers import TaskCancelled



class ROIMixin:
    def _ensure_smart_roi_cache_state(self):
        if not hasattr(self, '_smart_topology_cache'):
            self._smart_topology_cache = {}
        if not hasattr(self, '_smart_roi_mask_cache'):
            self._smart_roi_mask_cache = {}
        if not hasattr(self, '_effective_roi_mask_cache_key'):
            self._effective_roi_mask_cache_key = None
            self._effective_roi_mask_cache = None
        if not hasattr(self, 'smart_roi_performance'):
            self.smart_roi_performance = {}

    def _invalidate_effective_roi_mask_cache(self):
        self._effective_roi_mask_cache_key = None
        self._effective_roi_mask_cache = None

    def _invalidate_smart_roi_runtime_cache(self, topology=True, masks=True, reason=''):
        """Invalidate runtime-only caches without changing Recipe semantics."""
        self._ensure_smart_roi_cache_state()
        if topology:
            self._smart_topology_cache.clear()
        if masks:
            self._smart_roi_mask_cache.clear()
        self._invalidate_effective_roi_mask_cache()
        if reason:
            self.smart_roi_performance['last_invalidation_reason'] = str(reason)

    @staticmethod
    def _array_runtime_identity(values):
        array = np.asarray(values)
        pointer = int(array.__array_interface__['data'][0]) if array.size else 0
        return pointer, tuple(array.shape), str(array.dtype)

    @staticmethod
    def _matrix_runtime_signature(matrix_rc):
        if matrix_rc is None:
            return ('none',)
        rows = np.asarray(matrix_rc[0])
        cols = np.asarray(matrix_rc[1])
        if len(rows) == 0:
            return ('matrix', 0)
        return ('matrix', len(rows), int(rows[0]), int(cols[0]),
                int(rows[-1]), int(cols[-1]))

    def _smart_dataset_cache_key(self, x, y, z, matrix_rc=None):
        """Identify the transformed dataset used by topology and ROI masks."""
        current = getattr(self, '_trans_cache_data', None)
        if (current is not None and len(current) == 3
                and x is current[0] and y is current[1] and z is current[2]):
            geometry = ('current', int(getattr(self, '_df_version', 0)),
                        tuple(getattr(self, 'transform_pipeline', ())), len(x))
        else:
            geometry = ('runtime', int(getattr(self, '_df_version', 0)),
                        self._array_runtime_identity(x), self._array_runtime_identity(y),
                        self._array_runtime_identity(z))
        return geometry + (self._matrix_runtime_signature(matrix_rc),)

    def _smart_topology_cache_key(self, x, y, z, matrix_rc, sensitivity):
        return ('smart-topology-v2', self._smart_dataset_cache_key(x, y, z, matrix_rc),
                str(sensitivity), 150000)

    @staticmethod
    def _smart_roi_parameter_signature(roi):
        return (
            int(roi.get('smart_algorithm_version', 1) or 1),
            float(roi.get('seed_x', 0.0)), float(roi.get('seed_y', 0.0)),
            float(roi.get('seed_z', 0.0)), float(roi.get('z_tolerance_mm', 0.02)),
            str(roi.get('smart_mode', 'plane_residual')),
            str(roi.get('sensitivity', 'legacy')),
            str(roi.get('connectivity', 'auto_xy')),
            float(roi.get('xy_radius_mm', 0.0) or 0.0),
        )

    def _smart_roi_mask_cache_key(self, x, y, z, roi, matrix_rc=None):
        return ('smart-roi-mask', int(roi.get('id', 0) or 0),
                self._smart_dataset_cache_key(x, y, z, matrix_rc),
                self._smart_roi_parameter_signature(roi))

    def _lookup_smart_roi_mask(self, x, y, z, roi, matrix_rc=None):
        self._ensure_smart_roi_cache_state()
        key = self._smart_roi_mask_cache_key(x, y, z, roi, matrix_rc)
        entry = self._smart_roi_mask_cache.get(key)
        if entry is None:
            return None, key
        mask = entry.get('keep_mask')
        if mask is None or len(mask) != len(x):
            self._smart_roi_mask_cache.pop(key, None)
            return None, key
        entry['hits'] = int(entry.get('hits', 0)) + 1
        return mask, key

    def _store_smart_roi_mask(self, x, y, z, roi, keep_mask, matrix_rc=None,
                              topology_key=None, performance=None):
        self._ensure_smart_roi_cache_state()
        mask = np.asarray(keep_mask, dtype=bool).copy()
        key = self._smart_roi_mask_cache_key(x, y, z, roi, matrix_rc)
        self._smart_roi_mask_cache[key] = {
            'roi_id': int(roi.get('id', 0) or 0),
            'data_version': int(getattr(self, '_df_version', 0)),
            'cache_key': key,
            'keep_mask': mask,
            'selected_indices': np.flatnonzero(mask),
            'point_count': int(mask.sum()),
            'topology_key': topology_key,
            'algorithm_version': int(roi.get('smart_algorithm_version', 1) or 1),
            'hits': 0,
            'performance': dict(performance or {}),
        }
        self._invalidate_effective_roi_mask_cache()
        return key

    def _lookup_smart_topology(self, key):
        self._ensure_smart_roi_cache_state()
        entry = self._smart_topology_cache.get(key)
        if entry is not None:
            entry['hits'] = int(entry.get('hits', 0)) + 1
        return entry

    def _store_smart_topology(self, key, topology, finite_idx, build_seconds=0.0):
        self._ensure_smart_roi_cache_state()
        entry = {
            'cache_key': key,
            'topology': topology,
            'finite_idx': np.asarray(finite_idx, dtype=np.int64).copy(),
            'point_count': int(len(finite_idx)),
            'build_seconds': float(build_seconds),
            'hits': 0,
        }
        self._smart_topology_cache[key] = entry
        return entry

    def _get_or_build_smart_topology(self, x, y, z, matrix_rc, sensitivity):
        key = self._smart_topology_cache_key(x, y, z, matrix_rc, sensitivity)
        cached = self._lookup_smart_topology(key)
        if cached is not None:
            return cached, True
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        finite_idx = np.flatnonzero(finite)
        finite_matrix = None
        if matrix_rc is not None:
            finite_matrix = (np.asarray(matrix_rc[0])[finite_idx],
                             np.asarray(matrix_rc[1])[finite_idx])
        started = time.perf_counter()
        topology = build_adaptive_topology(
            np.asarray(x)[finite_idx], np.asarray(y)[finite_idx],
            matrix_rc=finite_matrix, sensitivity=sensitivity, delaunay_limit=150000)
        entry = self._store_smart_topology(
            key, topology, finite_idx, time.perf_counter() - started)
        return entry, False

    def _effective_roi_cache_signature(self, x, y, z, shapes, enabled, matrix_rc):
        shape_signature = tuple(
            (int(shape.get('id', 0) or 0), bool(shape.get('enabled', True)),
             str(shape.get('type', 'rect')),
             self._smart_roi_parameter_signature(shape) if shape.get('type') == 'smart_face'
             else tuple(sorted((key, repr(value)) for key, value in shape.items()
                               if key not in ('name',))))
            for shape in shapes
        )
        return ('effective-roi', bool(enabled),
                self._smart_dataset_cache_key(x, y, z, matrix_rc), shape_signature)

    def _get_effective_roi_mask_cached(self, x, y, z, matrix_rc=None,
                                       roi_shapes=None, roi_enabled=None):
        self._ensure_smart_roi_cache_state()
        shapes = self.roi_shapes if roi_shapes is None else (roi_shapes or [])
        enabled = self.roi_enabled if roi_enabled is None else bool(roi_enabled)
        key = self._effective_roi_cache_signature(x, y, z, shapes, enabled, matrix_rc)
        if key == self._effective_roi_mask_cache_key:
            cached = self._effective_roi_mask_cache
            if cached is not None and len(cached) == len(x):
                return cached
        keep = self._roi_keep_mask_for_arrays(
            x, y, z, shapes, enabled, matrix_rc=matrix_rc)
        self._effective_roi_mask_cache_key = key
        self._effective_roi_mask_cache = np.asarray(keep, dtype=bool)
        return self._effective_roi_mask_cache

    def _roi_is_active(self, roi_enabled=None, roi_shapes=None):
        enabled = self.roi_enabled if roi_enabled is None else bool(roi_enabled)
        shapes = self.roi_shapes if roi_shapes is None else (roi_shapes or [])
        return enabled and any(bool(r.get('enabled', True)) for r in shapes)

    @staticmethod
    def _roi_shape_label(roi):
        name = roi.get('name', 'ROI')
        view = str(roi.get('view', 'XY')).upper()
        if roi.get('type') == 'circle':
            return (f"{name}: {view}圆形 c1={roi.get('cx', 0):.4f}, c2={roi.get('cy', 0):.4f}, "
                    f"r={roi.get('radius', 0):.4f}")
        if roi.get('type') == 'smart_face':
            mode = str(roi.get('smart_mode', 'plane_residual'))
            version = int(roi.get('smart_algorithm_version', 1) or 1)
            mode_text = ({'surface_following': '连续曲面', 'plane_residual': '严格同平面',
                          'connected': '旧版连通'}.get(mode, mode))
            conn = str(roi.get('topology_label') or
                       ("矩阵8邻域" if roi.get('connectivity') == 'matrix8' else "XY自动邻接"))
            radius = float(roi.get('xy_radius_mm', 0.0))
            radius_text = f", 邻接r={radius:.4f}" if radius > 0 else ""
            point_text = f", 点数={int(roi.get('point_count_at_create', 0)):,}"
            fallback_text = (f", 回退={roi.get('topology_fallback_reason')}"
                             if roi.get('topology_fallback_reason') else '')
            return (f"{name}: 智能抓面 seed=({roi.get('seed_x', 0):.4f}, {roi.get('seed_y', 0):.4f}), "
                    f"Z={roi.get('seed_z', 0):.5f}, 容差={roi.get('z_tolerance_mm', 0):.4f}mm, "
                    f"V{version}, {mode_text}, {roi.get('sensitivity', 'legacy')}, {conn}{radius_text}"
                    f"{point_text}{fallback_text}")
        return (f"{name}: {view}矩形 c1={roi.get('cx', 0):.4f}, c2={roi.get('cy', 0):.4f}, "
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
                    algorithm_version = int(raw.get('smart_algorithm_version', 1) or 1)
                    roi.update({
                        'seed_x': float(raw.get('seed_x', raw.get('cx', 0.0))),
                        'seed_y': float(raw.get('seed_y', raw.get('cy', 0.0))),
                        'seed_z': float(raw.get('seed_z', 0.0)),
                        'seed_index': int(raw.get('seed_index', -1) or -1),
                        'seed_view': str(raw.get('seed_view', 'XY')),
                        'z_tolerance_mm': max(float(raw.get('z_tolerance_mm', 0.2)), 1e-9),
                        'smart_mode': str(raw.get('smart_mode', 'plane_residual')),
                        'connectivity': str(raw.get('connectivity', 'auto_xy')),
                        'xy_radius_mm': max(float(raw.get('xy_radius_mm', 0.0)), 0.0),
                        'morph_dilate_iters': 0,
                        'morph_erode_iters': 0,
                        'point_count_at_create': int(raw.get('point_count_at_create', 0) or 0),
                        'smart_algorithm_version': min(max(algorithm_version, 1), 3),
                        'sensitivity': str(raw.get('sensitivity', 'standard')),
                        'topology_label': str(raw.get('topology_label', '')),
                        'topology_method': str(raw.get('topology_method', '')),
                        'topology_fallback_reason': str(raw.get('topology_fallback_reason', '')),
                        'local_spacing_mm': max(float(raw.get('local_spacing_mm', 0.0) or 0.0), 0.0),
                    })
                    for gate_key in ('optional_xy_gate', 'optional_xz_gate', 'optional_yz_gate'):
                        gate = raw.get(gate_key)
                        if not isinstance(gate, dict):
                            continue
                        bounds = gate.get('bounds', {}) or {}
                        try:
                            vals = [float(bounds[k]) for k in ('x_min', 'x_max', 'y_min', 'y_max')]
                        except (KeyError, TypeError, ValueError):
                            continue
                        if not np.isfinite(vals).all():
                            continue
                        cleaned_gate = {
                            'view': str(gate.get('view', gate_key[9:11])).upper(),
                            'bounds': {'x_min': min(vals[0], vals[1]), 'x_max': max(vals[0], vals[1]),
                                       'y_min': min(vals[2], vals[3]), 'y_max': max(vals[2], vals[3])},
                            'display_mode': str(gate.get('display_mode', 'raw_z_mm')),
                            'display_plane_coeffs': gate.get('display_plane_coeffs'),
                            'display_polynomial_model': gate.get('display_polynomial_model'),
                        }
                        roi[gate_key] = cleaned_gate
                    if roi['smart_algorithm_version'] >= 2:
                        if roi['smart_mode'] not in ('surface_following', 'plane_residual'):
                            roi['smart_mode'] = 'surface_following'
                        if roi['sensitivity'] not in ('strict', 'standard', 'loose'):
                            roi['sensitivity'] = 'standard'
                    else:
                        if roi['smart_mode'] not in ('plane_residual', 'connected'):
                            roi['smart_mode'] = 'plane_residual'
                        roi['sensitivity'] = 'legacy'
                    if roi['connectivity'] not in ('matrix8', 'auto_xy'):
                        roi['connectivity'] = 'auto_xy'
                else:
                    view = str(raw.get('view', 'XY')).upper()
                    roi['view'] = view if view in ('XY', 'XZ', 'YZ') else 'XY'
                    roi['cx'] = float(raw.get('cx', 0.0))
                    roi['cy'] = float(raw.get('cy', 0.0))
                    roi['display_mode'] = str(raw.get('display_mode', 'raw_z_mm'))
                    roi['display_plane_coeffs'] = raw.get('display_plane_coeffs')
                    roi['display_polynomial_model'] = raw.get('display_polynomial_model')
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
        info = getattr(self, 'import_info', {}) or {}
        if (info.get('source_format') == 'Precitec FSS Explorer SCAN PATH DATA'
                and not info.get('precitec_topology_usable', False)):
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
        cached_mask, _ = self._lookup_smart_roi_mask(x, y, z, roi, matrix_rc)
        if cached_mask is not None:
            return cached_mask
        if int(roi.get('smart_algorithm_version', 1) or 1) >= 2:
            finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
            finite_idx = np.flatnonzero(finite)
            if len(finite_idx) < 3:
                return np.zeros(len(x), dtype=bool)
            sensitivity = str(roi.get('sensitivity', 'standard'))
            try:
                topology_entry, topology_hit = self._get_or_build_smart_topology(
                    x, y, z, matrix_rc, sensitivity)
                topology = topology_entry['topology']
                finite_idx = topology_entry['finite_idx']
                algorithm_version = int(roi.get('smart_algorithm_version', 2) or 2)
                grow_started = time.perf_counter()
                growth_stats = {}
                seed_index = int(roi.get('seed_index', -1) or -1)
                seed_matches = np.flatnonzero(finite_idx == seed_index)
                local_seed_index = int(seed_matches[0]) if len(seed_matches) else None
                if algorithm_version == 2:
                    local_keep = grow_surface_roi_v2(
                        x[finite_idx], y[finite_idx], z[finite_idx],
                        roi.get('seed_x', 0.0), roi.get('seed_y', 0.0),
                        roi.get('z_tolerance_mm', 0.02), topology,
                        mode=str(roi.get('smart_mode', 'surface_following')),
                        sensitivity=sensitivity)
                    growth_stats['algorithm'] = 'v2_legacy_replay'
                else:
                    local_keep = grow_surface_roi(
                        x[finite_idx], y[finite_idx], z[finite_idx],
                        roi.get('seed_x', 0.0), roi.get('seed_y', 0.0),
                        roi.get('z_tolerance_mm', 0.02), topology,
                        mode=str(roi.get('smart_mode', 'surface_following')),
                        sensitivity=sensitivity, stats=growth_stats,
                        seed_index=local_seed_index)
                roi['topology_label'] = topology['topology']
                roi['topology_method'] = topology['method']
                roi['topology_fallback_reason'] = topology.get('fallback_reason', '')
                roi['local_spacing_mm'] = float(topology.get('local_spacing_mm', 0.0))
                result = np.zeros(len(x), dtype=bool)
                result[finite_idx[local_keep]] = True
                performance = {
                    'topology_cache': 'HIT' if topology_hit else 'MISS',
                    'topology_seconds': 0.0 if topology_hit else float(topology_entry['build_seconds']),
                    'grow_seconds': float(time.perf_counter() - grow_started),
                    'points': int(len(finite_idx)),
                    **growth_stats,
                }
                self.smart_roi_performance = dict(performance)
                if int(roi.get('id', 0) or 0) > 0:
                    self._store_smart_roi_mask(
                        x, y, z, roi, result, matrix_rc,
                        topology_key=topology_entry['cache_key'], performance=performance)
                return result
            except Exception as exc:
                roi['topology_label'] = '拓扑失败'
                roi['topology_method'] = 'failed'
                roi['topology_fallback_reason'] = str(exc)
                result = np.zeros(len(x), dtype=bool)
                seed = int(finite_idx[np.argmin(
                    (x[finite_idx] - float(roi.get('seed_x', 0.0))) ** 2 +
                    (y[finite_idx] - float(roi.get('seed_y', 0.0))) ** 2)])
                result[seed] = True
                return result
        if str(roi.get('smart_mode', 'plane_residual')) == 'plane_residual':
            return self._smart_face_keep_mask_plane_residual(x, y, z, roi, update_radius=update_radius)
        if roi.get('connectivity') == 'matrix8' and matrix_rc is not None:
            try:
                return self._smart_face_keep_mask_matrix(x, y, z, roi, matrix_rc)
            except Exception:
                pass
        return self._smart_face_keep_mask_auto_xy(x, y, z, roi, update_radius=update_radius)

    @staticmethod
    def _gate_plot_z(x, y, z, gate):
        model = gate.get('display_polynomial_model')
        if isinstance(model, dict):
            return (z - evaluate_polynomial_surface(model, x, y)) * 1000.0
        coeffs = gate.get('display_plane_coeffs')
        if gate.get('display_mode') == 'detrended_um' and coeffs is not None and len(coeffs) == 3:
            return (z - (float(coeffs[0]) * x + float(coeffs[1]) * y + float(coeffs[2]))) * 1000.0
        return z

    def _smart_candidate_gate_mask(self, x, y, z, roi):
        keep = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        for key, view in (('optional_xy_gate', 'XY'), ('optional_xz_gate', 'XZ'),
                          ('optional_yz_gate', 'YZ')):
            gate = roi.get(key)
            if not isinstance(gate, dict):
                continue
            b = gate.get('bounds', {})
            plot_z = self._gate_plot_z(x, y, z, gate)
            if view == 'XY':
                inside = ((x >= b['x_min']) & (x <= b['x_max']) &
                          (y >= b['y_min']) & (y <= b['y_max']))
            elif view == 'XZ':
                inside = ((x >= b['x_min']) & (x <= b['x_max']) &
                          (plot_z >= b['y_min']) & (plot_z <= b['y_max']))
            else:
                inside = ((y >= b['x_min']) & (y <= b['x_max']) &
                          (plot_z >= b['y_min']) & (plot_z <= b['y_max']))
            keep &= inside
        return keep

    def _roi_keep_mask_for_arrays(self, x, y, z=None, roi_shapes=None, roi_enabled=None, matrix_rc=None):
        shapes = self.roi_shapes if roi_shapes is None else (roi_shapes or [])
        if not self._roi_is_active(roi_enabled, shapes):
            return np.ones(len(x), dtype=bool)
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        z = np.asarray(z if z is not None else np.zeros(len(x)), dtype=float)
        manual_by_view = {view: [] for view in ('XY', 'XZ', 'YZ')}
        smart_masks = []
        for roi in shapes:
            if not roi.get('enabled', True):
                continue
            if roi.get('type') == 'smart_face':
                smart_masks.append(
                    self._smart_face_keep_mask_for_arrays(x, y, z, roi, matrix_rc=matrix_rc))
                continue
            view = str(roi.get('view', 'XY')).upper()
            if view not in manual_by_view:
                view = 'XY'
            cx = float(roi.get('cx', 0.0))
            cy = float(roi.get('cy', 0.0))
            second_axis = y if view == 'XY' else self._gate_plot_z(x, y, z, roi)
            first_axis = x if view in ('XY', 'XZ') else y
            if roi.get('type') == 'circle':
                r = float(roi.get('radius', 0.0))
                mask = ((first_axis - cx) ** 2 + (second_axis - cy) ** 2) <= (r ** 2)
            else:
                hw = float(roi.get('width', 0.0)) / 2.0
                hh = float(roi.get('height', 0.0)) / 2.0
                mask = ((first_axis >= cx - hw) & (first_axis <= cx + hw)
                        & (second_axis >= cy - hh) & (second_axis <= cy + hh))
            manual_by_view[view].append(mask)

        keep = np.ones(len(x), dtype=bool)
        for view_masks in manual_by_view.values():
            if view_masks:
                keep &= np.logical_or.reduce(view_masks)
        if smart_masks:
            keep &= np.logical_or.reduce(smart_masks)
        return keep

    def _manual_roi_mask_for_arrays(self, x, y, z, roi):
        """Evaluate one manual ROI using its current display-space model."""
        view = str(roi.get('view', 'XY')).upper()
        if view not in ('XY', 'XZ', 'YZ'):
            view = 'XY'
        second_axis = y if view == 'XY' else self._gate_plot_z(x, y, z, roi)
        first_axis = x if view in ('XY', 'XZ') else y
        cx = float(roi.get('cx', 0.0)); cy = float(roi.get('cy', 0.0))
        if roi.get('type') == 'circle':
            radius = float(roi.get('radius', 0.0))
            return ((first_axis - cx) ** 2 + (second_axis - cy) ** 2) <= radius ** 2
        half_width = float(roi.get('width', 0.0)) / 2.0
        half_height = float(roi.get('height', 0.0)) / 2.0
        return ((first_axis >= cx - half_width) & (first_axis <= cx + half_width)
                & (second_axis >= cy - half_height) & (second_axis <= cy + half_height))

    def _sync_roi_input_state(self):
        if not hasattr(self, 'cb_roi_shape'):
            return
        shape_idx = self.cb_roi_shape.currentIndex()
        is_smart = shape_idx == 1
        if hasattr(self, 'roi_advanced_widget'):
            self.roi_advanced_widget.setVisible(self.chk_roi_advanced.isChecked())
        for w in (self.lbl_roi_w, self.spin_roi_w, self.lbl_roi_h, self.spin_roi_h):
            w.setEnabled(not is_smart)
        for w in (self.lbl_smart_mode, self.cb_smart_mode,
                  self.lbl_smart_sensitivity, self.cb_smart_sensitivity, self.lbl_smart_tol,
                  self.spin_smart_tol, self.lbl_smart_tol_hint):
            w.setEnabled(is_smart)
        for w in (self.lbl_smart_dilate, self.spin_smart_dilate, self.lbl_smart_erode, self.spin_smart_erode):
            w.setEnabled(False)
        self.btn_roi_add_input.setEnabled(not is_smart)
        if self.btn_roi_mouse.isChecked():
            self.btn_roi_mouse.setText("退出智能抓面" if is_smart else "退出框选 ROI")
        else:
            self.btn_roi_mouse.setText("开始智能抓面" if is_smart else "开始框选 ROI")
        if self.selection_mode in ('roi_rect', 'roi_smart'):
            self.selection_mode = 'roi_smart' if is_smart else 'roi_rect'
            self.statusBar().showMessage(
                "智能抓面模式：在 XY 图点击种子点；默认跟随连续Bow/Warpage，不跨孔洞且不自动补洞。"
                if is_smart else
                "ROI 连续框选模式: 矩形。可在 XY/XZ/YZ 图中继续拖拽添加区域。", 5000)

    def _on_roi_changed(self):
        if hasattr(self, 'chk_roi_enable'):
            self.roi_enabled = self.chk_roi_enable.isChecked()
        self._refresh_roi_ui(update=False)
        if self.df_raw is not None:
            self.update_analysis()

    def _refresh_roi_ui(self, update=False, effective_roi_mask=None):
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
                if roi.get('type') == 'smart_face':
                    cached, _ = self._lookup_smart_roi_mask(
                        tx, ty, tz, roi, self._matrix_rc_for_current_data())
                    count = (int(cached.sum()) if cached is not None else
                             int(roi.get('point_count_at_create', 0) or 0))
                else:
                    count_roi = dict(roi)
                    count_roi['enabled'] = True
                    count = int(self._roi_keep_mask_for_arrays(
                        tx, ty, tz, [count_roi], True,
                        self._matrix_rc_for_current_data()).sum())
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
        if effective_roi_mask is not None:
            self.last_roi_keep_count = int(np.asarray(effective_roi_mask, dtype=bool).sum())
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

    def _add_roi_shape(self, roi, keep_roi_mode=False, precomputed_mask=None,
                       cache_context=None, topology_key=None, performance=None):
        roi['id'] = int(self.roi_next_id)
        roi['name'] = f"ROI {self.roi_next_id}"
        roi['enabled'] = True
        self.roi_next_id += 1
        self.roi_shapes.append(roi)
        if precomputed_mask is not None and cache_context is not None:
            x, y, z, matrix_rc = cache_context
            self._store_smart_roi_mask(
                x, y, z, roi, precomputed_mask, matrix_rc,
                topology_key=topology_key, performance=performance)
        else:
            self._invalidate_effective_roi_mask_cache()
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
        if self.cb_roi_shape.currentIndex() == 1:
            self.statusBar().showMessage("智能抓面需要在 XY 图点击种子点生成 ROI。", 5000)
            return
        cx = float(self.spin_roi_cx.value())
        cy = float(self.spin_roi_cy.value())
        self._add_roi_shape({
            'type': 'rect', 'view': 'XY', 'cx': cx, 'cy': cy,
            'width': float(self.spin_roi_w.value()), 'height': float(self.spin_roi_h.value())
        })

    def start_mouse_roi(self, checked=None):
        checked = self.btn_roi_mouse.isChecked() if checked is None else bool(checked)
        if not checked:
            self.set_delete_selection_mode(show_message=True)
            return
        self.selection_mode = 'roi_smart' if self.cb_roi_shape.currentIndex() == 1 else 'roi_rect'
        self.btn_roi_mouse.setText("退出智能抓面" if self.selection_mode == 'roi_smart' else "退出框选 ROI")
        if self.temp_selected_mask is not None:
            self.temp_selected_mask.fill(False)
            self.update_plots_only()
        if self.selection_mode == 'roi_smart':
            self.statusBar().showMessage("智能抓面模式已开启：请在 XY 视图点击种子点，可连续添加多个 ROI。", 8000)
        else:
            self.statusBar().showMessage("ROI 连续框选模式已开启：可在 XY/XZ/YZ 视图中拖拽，可连续添加多个 ROI。", 8000)

    def toggle_smart_gate_mode(self, checked):
        if checked:
            self.selection_mode = 'roi_smart_gate'
            self.statusBar().showMessage(
                "候选范围模式：可在XY/XZ/YZ拖框，多个视图取交集；完成后关闭本按钮并点击种子。", 10000)
        else:
            self.selection_mode = 'roi_smart'
            self.statusBar().showMessage("候选范围已保留，请在XY/XZ/YZ任一视图点击种子。", 7000)

    def clear_pending_smart_gates(self):
        self._pending_smart_gates = {}
        self.statusBar().showMessage("已清除待创建智能ROI的候选范围。", 5000)
        if self.df_raw is not None:
            self.update_plots_only()

    def set_delete_selection_mode(self, show_message=True):
        self.selection_mode = 'delete'
        if hasattr(self, 'btn_smart_gate'):
            self.btn_smart_gate.blockSignals(True)
            self.btn_smart_gate.setChecked(False)
            self.btn_smart_gate.blockSignals(False)
        self.pending_delete_operation = None
        if hasattr(self, 'btn_roi_mouse'):
            self.btn_roi_mouse.blockSignals(True)
            self.btn_roi_mouse.setChecked(False)
            self.btn_roi_mouse.setText("开始智能抓面" if self.cb_roi_shape.currentIndex() == 1 else "开始框选 ROI")
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
        self._invalidate_effective_roi_mask_cache()
        self._refresh_roi_ui(update=True)

    def delete_selected_roi(self, *_args):
        idx = self._selected_roi_index()
        if idx is None:
            return
        deleted = self.roi_shapes.pop(idx)
        roi_id = int(deleted.get('id', 0) or 0)
        if roi_id:
            self._smart_roi_mask_cache = {
                key: value for key, value in self._smart_roi_mask_cache.items()
                if int(value.get('roi_id', 0) or 0) != roi_id
            }
        self._invalidate_effective_roi_mask_cache()
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
        self._smart_roi_mask_cache.clear()
        self._invalidate_effective_roi_mask_cache()
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

    def _draw_roi_overlays(self, ax, roi_shapes=None, roi_enabled=None, report=False,
                           view='XY'):
        shapes = roi_shapes if roi_shapes is not None else self.roi_shapes
        if not shapes:
            return
        active = self._roi_is_active(roi_enabled, shapes)
        if not active and report:
            return
        for roi in shapes:
            typ = roi.get('type')
            roi_view = 'XY' if typ == 'smart_face' else str(roi.get('view', 'XY')).upper()
            if roi_view != str(view).upper():
                continue
            enabled = bool(roi.get('enabled', True))
            if report and not enabled:
                continue
            edge = '#2f6db0' if enabled else '#94a3b8'
            style = '-' if enabled else '--'
            alpha = 0.95 if enabled else 0.55
            cx = float(roi.get('cx', 0.0))
            cy = float(roi.get('cy', 0.0))
            if typ == 'circle':
                patch = MplCircle((cx, cy), float(roi.get('radius', 0.0)), fill=False,
                                  edgecolor=edge, linewidth=1.8, linestyle=style, alpha=alpha)
            elif typ == 'smart_face':
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

    def _draw_smart_gate_overlays(self, ax, view):
        key = f"optional_{str(view).lower()}_gate"
        gates = []
        pending = (getattr(self, '_pending_smart_gates', {}) or {}).get(key)
        if pending is not None:
            gates.append((pending, '#ef8b2c'))
        for roi in self.roi_shapes:
            if roi.get('type') == 'smart_face' and roi.get(key) is not None:
                gates.append((roi[key], '#2f6db0'))
        for gate, color in gates:
            b = gate.get('bounds', {})
            try:
                patch = MplRectangle(
                    (float(b['x_min']), float(b['y_min'])),
                    float(b['x_max']) - float(b['x_min']),
                    float(b['y_max']) - float(b['y_min']),
                    fill=False, edgecolor=color, linewidth=1.5, linestyle='--', alpha=0.9)
            except (KeyError, TypeError, ValueError):
                continue
            patch.set_zorder(5)
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
        surface_mode = str(getattr(self, 'display_surface_mode', 'raw'))
        polynomial_model = None
        if surface_mode == 'residual_1' and self.current_coeffs is not None:
            coeffs = [float(v) for v in self.current_coeffs]
        if surface_mode.startswith('residual_'):
            try:
                order = int(surface_mode.rsplit('_', 1)[1])
            except (ValueError, IndexError):
                order = 1
            model = getattr(self, 'high_order_models', {}).get(order)
            if model is not None:
                polynomial_model = {
                    'order': order,
                    'powers': [list(power) for power in model['powers']],
                    'coefficients': [float(value) for value in model['coefficients']],
                    'x_center': float(model['x_center']), 'y_center': float(model['y_center']),
                    'x_scale': float(model['x_scale']), 'y_scale': float(model['y_scale']),
                }
        return {
            'schema_version': 2,
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
                          ('mm/µm' if surface_mode != 'raw' else 'mm/mm'),
            'transform_pipeline': list(self.transform_pipeline),
            'display_mode': ('detrended_um' if surface_mode == 'residual_1' else
                             f"{surface_mode}_um" if surface_mode != 'raw' else 'raw_z_mm'),
            'display_plane_coeffs': coeffs,
            'display_polynomial_model': polynomial_model,
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
            display_mode = str(raw.get('display_mode', 'raw_z_mm'))
            if display_mode not in ('raw_z_mm', 'detrended_um', 'residual_2_um', 'residual_3_um'):
                display_mode = 'raw_z_mm'
            polynomial_model = raw.get('display_polynomial_model')
            if not isinstance(polynomial_model, dict):
                polynomial_model = None
            elif display_mode != 'raw_z_mm':
                try:
                    polynomial_model = {
                        'order': int(polynomial_model['order']),
                        'powers': [list(int(v) for v in power) for power in polynomial_model['powers']],
                        'coefficients': [float(value) for value in polynomial_model['coefficients']],
                        'x_center': float(polynomial_model['x_center']),
                        'y_center': float(polynomial_model['y_center']),
                        'x_scale': float(polynomial_model['x_scale']),
                        'y_scale': float(polynomial_model['y_scale']),
                    }
                except (KeyError, TypeError, ValueError):
                    polynomial_model = None
            flt = raw.get('filter', {}) or {}
            roi = raw.get('roi', {}) or {}
            cleaned.append({
                'schema_version': 2,
                'operation_id': int(raw.get('operation_id', len(cleaned) + 1)),
                'created_at': str(raw.get('created_at', '')),
                'view': view,
                'bounds': {'x_min': min(x_min, x_max), 'x_max': max(x_min, x_max),
                           'y_min': min(y_min, y_max), 'y_max': max(y_min, y_max)},
                'axis_units': str(raw.get('axis_units', 'mm/mm')),
                'transform_pipeline': [a for a in (raw.get('transform_pipeline', []) or []) if a in valid_actions],
                'display_mode': display_mode,
                'display_plane_coeffs': coeffs,
                'display_polynomial_model': polynomial_model,
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
        if op.get('display_polynomial_model') is not None:
            fitted = evaluate_polynomial_surface(op['display_polynomial_model'], tx, ty)
            plot_z = (tz - fitted) * 1000.0
        elif op['display_mode'] == 'detrended_um' and op['display_plane_coeffs'] is not None:
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
        self._manual_delete_mask_history = []
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
            self._manual_delete_mask_history.append(replay_mask.copy())
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
        button = getattr(getattr(event, 'button', None), 'value', getattr(event, 'button', None))
        if button == 3:
            if self.temp_selected_mask is not None and np.any(self.temp_selected_mask):
                self._show_selection_context_menu()
            return
        if self.selection_mode != 'roi_smart' or self.df_raw is None:
            return
        if event.inaxes is not self.canvas.ax_xy or event.xdata is None or event.ydata is None:
            return
        if button != 1:
            return
        view_type = 'XY'
        candidates = np.asarray(
            (getattr(self, '_smart_seed_view_indices', {}) or {}).get(view_type, []),
            dtype=int)
        if len(candidates) == 0:
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        points = np.column_stack([tx[candidates], ty[candidates]])
        finite = np.isfinite(points).all(axis=1)
        if not np.any(finite):
            return
        visible = candidates[finite]
        screen = event.inaxes.transData.transform(points[finite])
        click = np.array([float(event.x), float(event.y)])
        seed_idx = int(visible[int(np.argmin(np.sum((screen - click) ** 2, axis=1)))])
        self.add_smart_face_roi_from_seed(
            float(tx[seed_idx]), float(ty[seed_idx]), seed_index=seed_idx,
            seed_view=view_type)

    def on_canvas_scroll(self, event):
        """Zoom only the current view; this must not touch analysis state."""
        ax = getattr(event, 'inaxes', None)
        if ax not in (self.canvas.ax_xy, self.canvas.ax_xz,
                      self.canvas.ax_yz, self.canvas.ax3d):
            return
        button = getattr(event, 'button', None)
        step = float(getattr(event, 'step', 0.0) or 0.0)
        if button == 'up' or step > 0:
            scale = 0.85
        elif button == 'down' or step < 0:
            scale = 1.0 / 0.85
        else:
            return

        def scaled_limits(limits, center):
            low, high = map(float, limits)
            return (center + (low - center) * scale,
                    center + (high - center) * scale)

        if ax is self.canvas.ax3d:
            for getter, setter in ((ax.get_xlim3d, ax.set_xlim3d),
                                   (ax.get_ylim3d, ax.set_ylim3d),
                                   (ax.get_zlim3d, ax.set_zlim3d)):
                limits = getter()
                center = (float(limits[0]) + float(limits[1])) / 2.0
                setter(scaled_limits(limits, center))
        else:
            if event.xdata is None or event.ydata is None:
                return
            if not np.isfinite(event.xdata) or not np.isfinite(event.ydata):
                return
            ax.set_xlim(scaled_limits(ax.get_xlim(), float(event.xdata)))
            ax.set_ylim(scaled_limits(ax.get_ylim(), float(event.ydata)))
        event.canvas.draw_idle()

    def add_smart_face_roi_from_seed(self, px, py, seed_index=None, seed_view='XY'):
        if self.df_raw is None:
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        base_mask = self.manual_mask if self.manual_mask is not None else np.ones(len(tz), dtype=bool)
        seed_candidates = np.where(base_mask & np.isfinite(tx) & np.isfinite(ty) & np.isfinite(tz))[0]
        finite_idx = np.where(np.isfinite(tx) & np.isfinite(ty) & np.isfinite(tz))[0]
        if len(seed_candidates) < 3 or len(finite_idx) < 3:
            self.statusBar().showMessage("有效点不足，无法智能抓面。", 6000)
            return
        if seed_index is None or int(seed_index) not in set(seed_candidates.tolist()):
            dist2 = (tx[seed_candidates] - px) ** 2 + (ty[seed_candidates] - py) ** 2
            seed_idx = int(seed_candidates[int(np.argmin(dist2))])
        else:
            seed_idx = int(seed_index)
        connectivity = 'matrix8' if self._matrix_rc_for_current_data() is not None else 'auto_xy'
        roi = {
            'type': 'smart_face',
            'seed_x': float(tx[seed_idx]),
            'seed_y': float(ty[seed_idx]),
            'seed_z': float(tz[seed_idx]),
            'z_tolerance_mm': float(self.spin_smart_tol.value()),
            'smart_algorithm_version': 3,
            'seed_index': seed_idx,
            'seed_view': str(seed_view),
            'smart_mode': str(self.cb_smart_mode.currentData()) if hasattr(self, 'cb_smart_mode') else 'surface_following',
            'sensitivity': (str(self.cb_smart_sensitivity.currentData())
                            if hasattr(self, 'cb_smart_sensitivity') else 'standard'),
            'connectivity': connectivity,
            'xy_radius_mm': 0.0,
            'morph_dilate_iters': 0,
            'morph_erode_iters': 0,
        }
        matrix_rc = self._matrix_rc_for_current_data()
        topology_key = self._smart_topology_cache_key(
            tx, ty, tz, matrix_rc, roi['sensitivity'])
        topology_entry = self._lookup_smart_topology(topology_key)
        if hasattr(self, '_run_background_task'):
            snapshot_version = int(getattr(self, '_df_version', 0))
            snapshot_pipeline = tuple(self.transform_pipeline)
            finite_idx_snapshot = (topology_entry['finite_idx'].copy()
                                   if topology_entry is not None else finite_idx.copy())
            finite_x = np.asarray(tx[finite_idx_snapshot], dtype=float).copy()
            finite_y = np.asarray(ty[finite_idx_snapshot], dtype=float).copy()
            finite_z = np.asarray(tz[finite_idx_snapshot], dtype=float).copy()
            finite_matrix = None
            if matrix_rc is not None:
                finite_matrix = (np.asarray(matrix_rc[0])[finite_idx_snapshot].copy(),
                                 np.asarray(matrix_rc[1])[finite_idx_snapshot].copy())
            cached_topology = topology_entry['topology'] if topology_entry is not None else None
            seed_matches = np.flatnonzero(finite_idx_snapshot == seed_idx)
            local_seed_index = int(seed_matches[0]) if len(seed_matches) else None
            def work(progress, cancel_event):
                topology_started = time.perf_counter()
                if cached_topology is None:
                    progress(5, '正在建立智能抓面拓扑')
                    topology = build_adaptive_topology(
                        finite_x, finite_y, matrix_rc=finite_matrix,
                        sensitivity=roi['sensitivity'], delaunay_limit=150000)
                    topology_seconds = time.perf_counter() - topology_started
                    topology_hit = False
                else:
                    progress(25, '已复用当前数据的智能抓面拓扑')
                    topology = cached_topology
                    topology_seconds = 0.0
                    topology_hit = True
                if cancel_event.is_set():
                    raise TaskCancelled()
                progress(55, f"正在按{topology['topology']}生长连续曲面")
                grow_started = time.perf_counter()
                growth_stats = {}

                def grow_progress(value, processed, total):
                    overall = 55 + int(max(0, min(100, value)) * 0.35)
                    progress(overall, f"正在跟踪连续曲面，已处理 {processed:,} / {total:,} 点")

                local_keep = grow_surface_roi(
                    finite_x, finite_y, finite_z, roi['seed_x'], roi['seed_y'],
                    roi['z_tolerance_mm'], topology, mode=roi['smart_mode'],
                    sensitivity=roi['sensitivity'], progress=grow_progress,
                    cancel_event=cancel_event, stats=growth_stats,
                    seed_index=local_seed_index)
                if cancel_event.is_set():
                    raise TaskCancelled()
                progress(100, '智能抓面完成')
                return {'local_keep': local_keep, 'topology': topology,
                        'df_version': snapshot_version, 'pipeline': snapshot_pipeline,
                        'topology_hit': topology_hit,
                        'topology_seconds': float(topology_seconds),
                        'grow_seconds': float(time.perf_counter() - grow_started),
                        'finite_idx': finite_idx_snapshot,
                        'growth_stats': growth_stats}

            def complete(result):
                if (int(getattr(self, '_df_version', 0)) != result['df_version']
                        or tuple(self.transform_pipeline) != result['pipeline']
                        or self.df_raw is None or len(self.df_raw) != len(tx)):
                    self._show_status('数据或姿态在抓面期间发生变化，本次结果已丢弃，请重新点击种子。', 8000)
                    return
                topology = result['topology']
                if not result['topology_hit']:
                    self._store_smart_topology(
                        topology_key, topology, result['finite_idx'], result['topology_seconds'])
                roi['topology_label'] = topology['topology']
                roi['topology_method'] = topology['method']
                roi['topology_fallback_reason'] = topology.get('fallback_reason', '')
                roi['local_spacing_mm'] = float(topology.get('local_spacing_mm', 0.0))
                keep = np.zeros(len(tx), dtype=bool)
                keep[result['finite_idx'][np.asarray(result['local_keep'], dtype=bool)]] = True
                performance = {
                    'topology_cache': 'HIT' if result['topology_hit'] else 'MISS',
                    'topology_seconds': result['topology_seconds'],
                    'grow_seconds': result['grow_seconds'],
                    'points': int(len(result['finite_idx'])),
                    **dict(result.get('growth_stats', {})),
                }
                self.smart_roi_performance = dict(performance)
                self._complete_smart_face_roi(
                    roi, keep, tx, ty, tz, matrix_rc,
                    topology_key=topology_key, performance=performance)

            self._run_background_task('智能抓面', work, complete)
            return
        keep = self._smart_face_keep_mask_for_arrays(
            tx, ty, tz, roi, matrix_rc=matrix_rc, update_radius=True)
        self._complete_smart_face_roi(
            roi, keep, tx, ty, tz, matrix_rc, topology_key=topology_key,
            performance=getattr(self, 'smart_roi_performance', {}))

    def _complete_smart_face_roi(self, roi, keep, tx=None, ty=None, tz=None, matrix_rc=None,
                                 topology_key=None, performance=None):
        count = int(np.sum(keep))
        if count < 3:
            self.statusBar().showMessage(
                f"智能抓面只得到 {count} 点，未添加。请调大抓面容差或点击面内更稳定的位置。", 8000)
            return
        roi['point_count_at_create'] = count
        self._pending_smart_gates = {}
        cache_context = ((tx, ty, tz, matrix_rc)
                         if tx is not None and ty is not None and tz is not None else None)
        self._add_roi_shape(
            roi, keep_roi_mode=True, precomputed_mask=keep,
            cache_context=cache_context, topology_key=topology_key,
            performance=performance)
        mode_text = "严格同平面" if roi.get('smart_mode') == 'plane_residual' else "连续曲面"
        conn_text = roi.get('topology_label') or ("矩阵8邻域" if roi['connectivity'] == 'matrix8' else "自适应点云")
        fallback_text = (f" | 回退原因: {roi['topology_fallback_reason']}"
                         if roi.get('topology_fallback_reason') else '')
        self.statusBar().showMessage(
            f"已添加智能抓面 ROI: {count:,} 点 | 容差 {roi['z_tolerance_mm']:.4f} mm | "
            f"{mode_text} | {roi.get('sensitivity', 'standard')} | {conn_text} | "
            f"局部点距 {roi.get('local_spacing_mm', 0.0):.5f} mm{fallback_text}",
            8000)

    @staticmethod
    def _manual_roi_from_operation(operation):
        bounds = dict((operation or {}).get('bounds', {}) or {})
        x1, x2 = float(bounds['x_min']), float(bounds['x_max'])
        y1, y2 = float(bounds['y_min']), float(bounds['y_max'])
        width, height = abs(x2 - x1), abs(y2 - y1)
        roi = {
            'type': 'rect',
            'view': str((operation or {}).get('view', 'XY')).upper(),
            'cx': (x1 + x2) / 2.0,
            'cy': (y1 + y2) / 2.0,
            'display_mode': str((operation or {}).get('display_mode', 'raw_z_mm')),
            'display_plane_coeffs': (operation or {}).get('display_plane_coeffs'),
            'display_polynomial_model': (operation or {}).get('display_polynomial_model'),
        }
        roi['width'] = width
        roi['height'] = height
        return roi

    def cancel_temp_selection(self):
        if self.temp_selected_mask is not None:
            self.temp_selected_mask.fill(False)
        self.pending_delete_operation = None
        self.update_plots_only()

    def set_temp_selection_as_roi(self):
        if (self.temp_selected_mask is None or not np.any(self.temp_selected_mask)
                or not self.pending_delete_operation):
            return
        try:
            roi = self._manual_roi_from_operation(self.pending_delete_operation)
        except (KeyError, TypeError, ValueError):
            self.statusBar().showMessage("当前框选范围无效，未创建 ROI。", 5000)
            return
        size = min(float(roi.get('width', 0.0)), float(roi.get('height', 0.0)))
        if size <= 0:
            return
        self.temp_selected_mask.fill(False)
        self.pending_delete_operation = None
        self._add_roi_shape(roi)

    def _show_selection_context_menu(self):
        menu = QMenu(self)
        delete_action = menu.addAction("删除选中")
        rect_action = menu.addAction("设为矩形 ROI")
        cancel_action = menu.addAction("取消选中")
        chosen = menu.exec(QCursor.pos())
        if chosen is delete_action:
            self.apply_manual_deletion()
        elif chosen is rect_action:
            self.set_temp_selection_as_roi()
        elif chosen is cancel_action:
            self.cancel_temp_selection()

    def on_select(self, eclick, erelease, view_type):
        if self.df_raw is None or self.active_idx is None: return
        x1, y1, x2, y2 = eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata
        if None in (x1, y1, x2, y2): return

        if self.selection_mode == 'roi_smart':
            return

        if self.selection_mode == 'roi_rect':
            operation = self._build_manual_delete_operation(view_type, x1, y1, x2, y2)
            roi = self._manual_roi_from_operation(operation)
            if roi['width'] <= 0 or roi['height'] <= 0:
                return
            self._add_roi_shape(roi, keep_roi_mode=True)
            return

        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        plot_z_all, _, _ = self._get_plot_z(tx, ty, tz)
        if str(view_type).upper() == 'XY' and self.manual_mask is not None:
            selection_idx = np.flatnonzero(self.manual_mask)
        else:
            selection_idx = np.asarray(self.active_idx, dtype=int)
        ax, ay, az = tx[selection_idx], ty[selection_idx], plot_z_all[selection_idx]
        if view_type == 'XY': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (ay >= min(y1, y2)) & (ay <= max(y1, y2))
        elif view_type == 'XZ': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        elif view_type == 'YZ': in_box = (ay >= min(x1, x2)) & (ay <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        else: return
        self.temp_selected_mask.fill(False)
        self.temp_selected_mask[selection_idx[in_box]] = True
        self.pending_delete_operation = self._build_manual_delete_operation(view_type, x1, y1, x2, y2)
        self.pending_delete_operation['selected_count'] = int(in_box.sum())
        selected_count = int(self.temp_selected_mask.sum())
        roi_inside_count = selected_count
        if self._roi_is_active():
            cached = getattr(self, '_effective_roi_mask_cache', None)
            if cached is not None and len(cached) == len(self.temp_selected_mask):
                roi_inside_count = int(np.sum(self.temp_selected_mask & cached))
            else:
                active_mask = np.zeros(len(self.temp_selected_mask), dtype=bool)
                active_mask[np.asarray(self.active_idx, dtype=int)] = True
                roi_inside_count = int(np.sum(self.temp_selected_mask & active_mask))
        self.statusBar().showMessage(
            f"已选择 {selected_count:,} 点，其中当前 ROI 内 {roi_inside_count:,} 点", 8000)
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
        if not hasattr(self, '_manual_delete_mask_history'):
            self._manual_delete_mask_history = []
        self._manual_delete_mask_history.append(self.manual_mask.copy())
        self.manual_mask &= (~self.temp_selected_mask)
        if operation:
            self.manual_delete_operations.append(operation)
        self.pending_delete_operation = None
        self.temp_selected_mask.fill(False)
        self.update_analysis()
        self.statusBar().showMessage(
            f"已记录第 {len(self.manual_delete_operations)} 次手动删除；{self._manual_deletion_summary()}", 8000)

    def undo_manual_deletion(self):
        """Undo only the most recently confirmed manual deletion operation."""
        if self.df_raw is None or not self.manual_delete_operations:
            self.statusBar().showMessage("当前没有可撤销的手动删点操作。", 4000)
            return
        removed = self.manual_delete_operations.pop()
        history = getattr(self, '_manual_delete_mask_history', [])
        if history:
            self.manual_mask = np.asarray(history.pop(), dtype=bool).copy()
        else:
            # Compatibility fallback for runtime state created before V4.5.1.
            replay_mask = np.ones(len(self.df_raw), dtype=bool)
            replayed = []
            for operation in self._clean_manual_delete_operations(self.manual_delete_operations):
                selected = self._manual_delete_mask_for_operation(operation, replay_mask)
                replay_mask &= ~selected
                operation['selected_count'] = int(np.sum(selected))
                replayed.append(operation)
            self.manual_delete_operations = replayed
            self.manual_mask = replay_mask
        self.pending_delete_operation = None
        if self.temp_selected_mask is None or len(self.temp_selected_mask) != len(self.df_raw):
            self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
        else:
            self.temp_selected_mask.fill(False)
        self.update_analysis()
        self.statusBar().showMessage(
            f"已撤销最近一次删点（原删除 {int(removed.get('selected_count', 0)):,} 点）；"
            f"剩余 {len(self.manual_delete_operations)} 次删除记录。", 8000)
