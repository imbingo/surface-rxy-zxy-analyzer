"""Multi-layer gap registration, subtraction, and export workflow."""

import re
import json
import copy
from datetime import datetime
from pathlib import Path
from copy import deepcopy

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from scipy.spatial import cKDTree

from ..workers import TaskCancelled
from ..dialog_paths import dialog_initial_path, remember_dialog_path
from .analysis import AnalysisMixin
from ..judgment import judgment_summary


class GapAnalysisMixin:
    GAP_REGISTRATION_MODE_LABELS = {
        'none': '未配准',
        'manual': '人工粗对齐',
        'auto': '自动精对齐',
    }
    GAP_LAYER_COLORS = {
        'stack': '#7b8490',
        'base1': '#2563eb',
        'base2': '#7c3aed',
        'matched': '#ef4444',
    }

    @staticmethod
    def _shifted_xy(rec):
        return (
            np.asarray(rec['x'], dtype=float) + float(rec.get('offset_x', 0.0)),
            np.asarray(rec['y'], dtype=float) + float(rec.get('offset_y', 0.0)),
        )

    @staticmethod
    def _gap_layer_status(rec, empty_text="尚未设置", fixed=False):
        if rec is None:
            return empty_text
        quality = (rec.get('metric_quality') or {}).get('label', '全量计算')
        dx = float(rec.get('offset_x', 0.0))
        dy = float(rec.get('offset_y', 0.0))
        mode = ('固定基准' if fixed else GapAnalysisMixin.GAP_REGISTRATION_MODE_LABELS.get(
            rec.get('registration_mode', 'none'), '未配准'))
        return (f"来源: {rec['name']} ({int(rec['n']):,} 点) | {quality}\n"
                f"XY 配准位移: ΔX {dx:+.4f} mm | ΔY {dy:+.4f} mm | {mode}")

    def _update_gap_slot_labels(self):
        if not hasattr(self, 'lbl_stack_status'):
            return
        self.lbl_stack_status.setText(self._gap_layer_status(self.data_stack, fixed=True))
        self.lbl_base1_status.setText(self._gap_layer_status(self.data_base1))
        self.lbl_base2_status.setText(self._gap_layer_status(self.data_base2, "可选空置"))
        for label, record, color in ((self.lbl_stack_status, self.data_stack, '#475569'),
                                     (self.lbl_base1_status, self.data_base1, '#2563eb'),
                                     (self.lbl_base2_status, self.data_base2, '#7c3aed')):
            label.setStyleSheet(self.SLOT_EMPTY_STYLE if record is None
                                else f"color: {color}; font-weight: 400;")
        if hasattr(self, '_sync_status_widgets'):
            self._sync_status_widgets()

    def _sync_gap_action_state(self):
        self._refresh_composite_save_buttons()
        busy = getattr(self, '_task_thread', None) is not None
        ready = self.data_stack is not None and self.data_base1 is not None
        result_ready = getattr(self, 'gap_result', None) is not None
        active = getattr(self, 'gap_active_layer', None)
        if hasattr(self, 'btn_calc_gap'):
            self.btn_calc_gap.setEnabled(ready and not busy)
        if hasattr(self, 'btn_auto_match_gap'):
            self.btn_auto_match_gap.setEnabled(ready and not busy)
        if hasattr(self, 'btn_gap_select_base1'):
            self.btn_gap_select_base1.setEnabled(self.data_base1 is not None and not busy)
            self.btn_gap_select_base1.setChecked(active == 'base1')
        if hasattr(self, 'btn_gap_select_base2'):
            self.btn_gap_select_base2.setEnabled(self.data_base2 is not None and not busy)
            self.btn_gap_select_base2.setChecked(active == 'base2')
        if hasattr(self, 'btn_export_gap_csv'):
            self.btn_export_gap_csv.setEnabled(result_ready and not busy)
        if hasattr(self, 'btn_export_gap_report'):
            self.btn_export_gap_report.setEnabled(result_ready and not busy)

        self._refresh_gap_result_views()

    def _invalidate_gap_result(self, reason=None):
        old = getattr(self, 'gap_result', None)
        self.gap_result = None
        if old is not None and self.current_source_name == old.get('name'):
            self.import_info['gap_result_stale'] = True
            self.lbl_source.setText(f"当前数据: {self.current_source_name} [配准已变更，旧结果]")
        self._sync_gap_action_state()
        if hasattr(self, '_refresh_judgment'):
            self._refresh_judgment('gap')
        if reason:
            self._show_status(f"胶厚结果已失效：{reason}，请重新计算。", 5000)

    def _current_gap_record(self):
        if self.df_raw is None or self.active_idx is None or len(self.active_idx) < 3:
            raise ValueError('请先完成单层分析后写入数据槽')
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        analysis_z = getattr(self, '_analysis_z_full', None)
        if analysis_z is not None and len(analysis_z) == len(tz):
            tz = np.asarray(analysis_z, dtype=float)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        return {
            'x': fx.copy(), 'y': fy.copy(), 'z': fz.copy(),
            'name': self.current_source_name, 'n': len(fz),
            'metric_quality': copy.deepcopy(self._current_metric_quality()),
            'sampled': bool(self.import_info.get('sampled', False)),
            'import_info': copy.deepcopy(self.import_info),
            'single_recipe': self._slot_single_recipe_snapshot(),
            'recipe_name': str(getattr(self, 'current_recipe_name', '')),
            'offset_x': 0.0, 'offset_y': 0.0, 'registration_mode': 'none',
        }

    def set_memory_slot(self, slot):
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "错误", "主界面尚无数据，请先载入并处理。")
            return
        if not self._confirm_estimated_metrics('写入多层扣减寄存器'):
            return
        try:
            rec = self._current_gap_record()
        except ValueError as exc:
            QMessageBox.warning(self, '单层数据尚不可写入', str(exc))
            return
        setattr(self, f"data_{slot}", rec)
        self._invalidate_gap_result()
        if slot in ('base1', 'base2'):
            self.select_gap_layer(slot)
        else:
            self._refresh_gap_registration(preserve_view=False)
        self._update_gap_slot_labels()
        self._sync_gap_action_state()
        names = {'stack': '堆叠总成（固定基准）', 'base1': '单片 1', 'base2': '单片 2'}
        self._show_status(f"已写入{names[slot]}: {rec['name']} ({rec['n']:,} 点)", 5000)

    def clear_memory_slot(self, slot):
        if slot not in ('base1', 'base2', 'stack'):
            return
        setattr(self, f"data_{slot}", None)
        if getattr(self, 'gap_active_layer', None) == slot:
            self.gap_active_layer = 'base1' if slot != 'base1' and self.data_base1 is not None else None
        self._invalidate_gap_result()
        self._update_gap_slot_labels()
        self._refresh_gap_registration(preserve_view=False)
        self._sync_gap_action_state()

    def clear_all_memory_slots(self):
        self._invalidate_gap_result()
        self.data_stack = self.data_base1 = self.data_base2 = None
        self.gap_active_layer = None
        self.gap_result = None
        if hasattr(self, 'lbl_gap_auto_feedback'):
            self.lbl_gap_auto_feedback.clear()
        self._update_gap_slot_labels()
        self._refresh_gap_registration(preserve_view=False)
        self._sync_gap_action_state()
        self._show_status("已清空当前胶厚配准与计算结果", 3000)

    def select_gap_layer(self, layer):
        rec = getattr(self, f"data_{layer}", None) if layer in ('base1', 'base2') else None
        if rec is None:
            return
        self.gap_active_layer = layer
        self._sync_gap_action_state()
        self._refresh_gap_registration(preserve_view=True)
        self._show_status(f"已选择{'单片 1' if layer == 'base1' else '单片 2'}；在 XY 图内按住左键拖动。", 4000)

    def on_gap_layer_moved(self, layer, offset_x, offset_y, finished=False):
        rec = getattr(self, f"data_{layer}", None) if layer in ('base1', 'base2') else None
        if rec is None:
            return
        changed = (not np.isclose(float(rec.get('offset_x', 0.0)), float(offset_x))
                   or not np.isclose(float(rec.get('offset_y', 0.0)), float(offset_y)))
        rec['offset_x'] = float(offset_x)
        rec['offset_y'] = float(offset_y)
        if changed:
            rec['registration_mode'] = 'manual'
            self._invalidate_gap_result()
        self._update_gap_slot_labels()
        self._refresh_gap_registration(preserve_view=True)
        if finished:
            self._show_status(
                f"粗对齐完成：ΔX {offset_x:+.4f} mm，ΔY {offset_y:+.4f} mm。", 4000)

    def _on_gap_tolerance_changed(self, _value=None):
        self._invalidate_gap_result()
        self._refresh_gap_registration(preserve_view=True)

    @classmethod
    def _registration_diagnostic(cls, stack, base1, base2, tolerance):
        if stack is None:
            return None
        sx = np.asarray(stack['x'], dtype=float)
        sy = np.asarray(stack['y'], dtype=float)
        stack_finite = np.isfinite(sx) & np.isfinite(sy)
        query = np.column_stack([sx, sy])
        layers = {}
        final_valid = stack_finite.copy()
        any_layer = False
        for key, rec in (('base1', base1), ('base2', base2)):
            if rec is None:
                continue
            any_layer = True
            raw_bx = np.asarray(rec['x'], dtype=float)
            raw_by = np.asarray(rec['y'], dtype=float)
            base_finite = np.isfinite(raw_bx) & np.isfinite(raw_by)
            valid = np.zeros(len(sx), dtype=bool)
            dist = np.full(len(sx), np.inf, dtype=float)
            idx = np.full(len(sx), len(raw_bx), dtype=int)
            if np.any(stack_finite) and np.any(base_finite):
                cache_key = (id(rec['x']), id(rec['y']), len(raw_bx))
                cached = rec.get('_gap_xy_tree')
                if cached is None or cached[0] != cache_key:
                    finite_indices = np.flatnonzero(base_finite)
                    tree = cKDTree(np.column_stack([raw_bx[base_finite], raw_by[base_finite]]))
                    rec['_gap_xy_tree'] = (cache_key, finite_indices, tree)
                else:
                    _, finite_indices, tree = cached
                offset = np.array([
                    float(rec.get('offset_x', 0.0)),
                    float(rec.get('offset_y', 0.0)),
                ])
                # Query the fixed source tree in its original coordinate frame;
                # this is equivalent to rebuilding a tree after every drag, but
                # keeps large-point-cloud dragging responsive.
                local_dist, local_idx = tree.query(
                    query[stack_finite] - offset, distance_upper_bound=tolerance)
                mapped_idx = np.full(len(local_idx), len(raw_bx), dtype=int)
                local_valid = local_dist <= tolerance
                mapped_idx[local_valid] = finite_indices[local_idx[local_valid]]
                dist[stack_finite] = local_dist
                idx[stack_finite] = mapped_idx
                valid[stack_finite] = local_valid
            matched_dist = dist[valid]
            layers[key] = {
                'dist': dist, 'idx': idx, 'valid': valid,
                'matched': int(valid.sum()),
                'mean': float(np.mean(matched_dist)) if len(matched_dist) else np.nan,
                'rms': float(np.sqrt(np.mean(matched_dist ** 2))) if len(matched_dist) else np.nan,
                'max': float(np.max(matched_dist)) if len(matched_dist) else np.nan,
            }
            final_valid &= valid
        if not any_layer:
            final_valid[:] = False
        return {
            'layers': layers,
            'final_valid': final_valid,
            'tolerance': float(tolerance),
        }

    def _refresh_gap_registration(self, preserve_view=True, diagnostic=None):
        tolerance = float(self.spin_tol.value()) if hasattr(self, 'spin_tol') else 0.05
        diag = diagnostic if diagnostic is not None else self._registration_diagnostic(
            self.data_stack, self.data_base1, self.data_base2, tolerance)
        if hasattr(self, 'gap_match_canvas'):
            self.gap_match_canvas.plot_registration(
                self.data_stack, self.data_base1, self.data_base2, tolerance,
                getattr(self, 'gap_active_layer', None), diag, preserve_view=preserve_view)
        if hasattr(self, 'lbl_gap_matched'):
            total = int(self.data_stack['n']) if self.data_stack is not None else 0
            matched = int(diag['final_valid'].sum()) if diag is not None else 0
            self.lbl_gap_matched.setText(f"{matched:,}" if self.data_stack is not None else "--")
            self.lbl_gap_unmatched.setText(f"{total - matched:,}" if self.data_stack is not None else "--")
            self.lbl_gap_tolerance.setText(f"{tolerance:.3f}")
            if getattr(self, 'gap_result', None) is not None:
                state = "已计算"
            elif self.data_stack is None:
                state = "等待堆叠基准"
            elif self.data_base1 is None:
                state = "等待单片 1"
            elif matched < 10:
                state = "匹配不足"
            else:
                state = "可计算"
            self.lbl_gap_state.setText(state)
            self._refresh_gap_registration_error(diag)
            self._refresh_judgment('gap')
        return diag

    def _refresh_gap_registration_error(self, diag):
        if not hasattr(self, 'lbl_gap_reg_mean'):
            return
        means, tips = [], []
        for key, tag in (('base1', '单片 1'), ('base2', '单片 2')):
            layer = (diag or {}).get('layers', {}).get(key)
            if not layer or not np.isfinite(layer['mean']):
                continue
            means.append(f"{layer['mean'] * 1000:.2f}")
            tips.append(f"{tag}：均值 {layer['mean'] * 1000:.2f} µm | RMS {layer['rms'] * 1000:.2f} µm | "
                        f"Max {layer['max'] * 1000:.2f} µm（{layer['matched']:,} 个匹配点）")
        self.lbl_gap_reg_mean.setText(" / ".join(means) if means else "--")
        self.gap_reg_mean_card.setToolTip(
            "容差内匹配点到堆叠层的 XY 最近邻距离均值" + ("\n" + "\n".join(tips) if tips else ""))

    def _update_gap_diagnostic(self, diag):
        """Compatibility wrapper retained for callers and older tests."""
        self._refresh_gap_registration()

    @staticmethod
    def _match_report(tag, dist, idx, valid):
        matched_dist = dist[valid]
        if len(matched_dist) == 0:
            return f"[{tag}] 无匹配点"
        mean_um = np.mean(matched_dist) * 1000
        rms_um = np.sqrt(np.mean(matched_dist ** 2)) * 1000
        max_um = np.max(matched_dist) * 1000
        unique_ratio = len(np.unique(idx[valid])) / valid.sum() * 100
        text = (f"[{tag}] 匹配 {int(valid.sum())} / 未匹配 {int((~valid).sum())}\n"
                f"    匹配距离 均值 {mean_um:.2f} µm | RMS {rms_um:.2f} µm | Max {max_um:.2f} µm\n"
                f"    唯一匹配比例 {unique_ratio:.1f}%")
        if unique_ratio < 99.9:
            text += "  存在多对一重复匹配，建议减小容差"
        return text

    @staticmethod
    def _registration_sample(x, y, limit=60000):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        finite = np.isfinite(x) & np.isfinite(y)
        points = np.column_stack([x[finite], y[finite]])
        if limit is not None and len(points) > limit:
            # Deterministic spatial ordering: file row order is not geometry.
            points = points[np.lexsort((points[:, 1], points[:, 0]))]
            pick = np.linspace(0, len(points) - 1, limit, dtype=int)
            points = points[pick]
        return points

    @classmethod
    def _optimize_translation(cls, stack, moving, tolerance, cancel_event=None):
        """Locally refine current translation using robust moving-to-reference overlap."""
        reference = cls._registration_sample(stack['x'], stack['y'], limit=None)
        moving_full = cls._registration_sample(moving['x'], moving['y'], limit=None)
        moving_xy = cls._registration_sample(moving['x'], moving['y'])
        if len(reference) < 3 or len(moving_xy) < 3:
            raise ValueError("自动匹配至少需要每层 3 个有效 XY 点。")
        tree_ref = cKDTree(reference)
        start = np.array([
            float(moving.get('offset_x', 0.0)),
            float(moving.get('offset_y', 0.0)),
        ])
        max_adjustment = max(float(tolerance) * 3.0, 0.01)
        capture_radius = max_adjustment

        # Establish the overlap from the operator's current coarse alignment.
        # A fixed trim percentage is unsafe when the reference contains holes or
        # only part of the moving layer can physically overlap it: non-overlap
        # points then pull ICP away from the visibly aligned region.  The local
        # capture gate keeps those points out while retaining the existing
        # maximum-adjustment safety boundary.
        start_shifted = moving_full + start
        start_dist, _ = tree_ref.query(start_shifted)
        start_local = np.isfinite(start_dist) & (start_dist <= capture_radius)
        min_local_points = max(3, min(20, int(np.ceil(len(moving_xy) * 0.01))))

        def cancelled():
            return cancel_event is not None and cancel_event.is_set()

        def refine():
            offset = start.copy()
            iterations = 0
            for iterations in range(1, 31):
                if cancelled():
                    raise TaskCancelled()
                shifted = moving_xy + offset
                dist, idx = tree_ref.query(shifted)
                keep = np.isfinite(dist) & (dist <= capture_radius)
                if keep.sum() < min_local_points:
                    break
                residual = reference[idx[keep]] - shifted[keep]
                delta = np.mean(residual, axis=0)
                offset += delta
                if float(np.hypot(*(offset - start))) > max_adjustment:
                    break
                if float(np.hypot(delta[0], delta[1])) < 1e-9:
                    break
            return offset, iterations

        def score(offset):
            shifted = moving_full + offset
            tree_moving = cKDTree(shifted)
            d_ref, _ = tree_moving.query(reference)
            d_mov, _ = tree_ref.query(shifted)
            residuals = np.concatenate([d_ref, d_mov])
            # Score the same local candidates seen at the starting position so
            # points entering/leaving the gate cannot improve the denominator.
            overlap_dist = d_mov[start_local]
            matched_dist = d_mov[d_mov <= tolerance]
            return {
                # Keep the historical full symmetric RMS as a diagnostic only.
                # A stack may cover a much larger footprint than a single layer,
                # so its non-overlapping area must not reject a valid local match.
                'rms': float(np.sqrt(np.mean(residuals ** 2))),
                'overlap_rms': (float(np.sqrt(np.mean(overlap_dist ** 2)))
                                if len(overlap_dist) else np.nan),
                'overlap_mean': (float(np.mean(overlap_dist))
                                 if len(overlap_dist) else np.nan),
                'matched_rms': (float(np.sqrt(np.mean(matched_dist ** 2)))
                                if len(matched_dist) else np.nan),
                'matched': int(len(matched_dist)),
                'moving_points': int(len(d_mov)),
                'local_points': int(len(overlap_dist)),
            }

        start_quality = score(start)
        proposed, iterations = refine()
        adjustment = float(np.hypot(*(proposed - start)))

        def result(accepted, reason, offset, quality):
            return {
                'accepted': bool(accepted),
                'reason': reason,
                'offset_x': float(offset[0]),
                'offset_y': float(offset[1]),
                'proposed_offset_x': float(proposed[0]),
                'proposed_offset_y': float(proposed[1]),
                'rms': quality['rms'],
                'overlap_rms': quality['overlap_rms'],
                'overlap_mean': quality['overlap_mean'],
                'matched_rms': quality['matched_rms'],
                'matched': quality['matched'],
                'moving_points': quality['moving_points'],
                'local_points': quality['local_points'],
                'start_rms': start_quality['rms'],
                'start_overlap_rms': start_quality['overlap_rms'],
                'start_matched': start_quality['matched'],
                'iterations': int(iterations),
                'adjustment': adjustment,
                'max_adjustment': max_adjustment,
                'capture_radius': capture_radius,
                'start_offset_x': float(start[0]),
                'start_offset_y': float(start[1]),
            }

        if start_local.sum() < min_local_points:
            return result(False, 'insufficient_local_overlap', start, start_quality)
        if not np.isfinite(proposed).all() or adjustment > max_adjustment:
            return result(False, 'max_adjustment', start, start_quality)
        proposed_quality = score(proposed)
        overlap_improved = (
            proposed_quality['matched'] >= start_quality['matched']
            and proposed_quality['overlap_rms'] <= start_quality['overlap_rms'] + 1e-12
        )
        if not overlap_improved:
            return result(False, 'overlap_not_improved', start, start_quality)
        return result(True, '', proposed, proposed_quality)

    @classmethod
    def _auto_registration_payload(cls, stack, base1, base2, tolerance, progress, cancel_event):
        progress(8, "正在自动匹配单片 1")
        results = {'base1': cls._optimize_translation(stack, base1, tolerance, cancel_event)}
        if base2 is not None:
            progress(52, "正在自动匹配单片 2")
            results['base2'] = cls._optimize_translation(stack, base2, tolerance, cancel_event)
        if cancel_event.is_set():
            raise TaskCancelled()
        progress(100, "自动匹配完成")
        return results

    def auto_match_gap_layers(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.warning(self, "数据不完整", "请先设置堆叠总成和单片 1。")
            return
        if hasattr(self, 'lbl_gap_auto_feedback'):
            self.lbl_gap_auto_feedback.setStyleSheet("color: #2563eb; font-size: 11px;")
            self.lbl_gap_auto_feedback.setText("正在检查当前位置附近的真实重叠点…")
        def snapshot(rec):
            if rec is None:
                return None
            return {
                'x': rec['x'], 'y': rec['y'], 'z': rec['z'],
                'name': rec['name'], 'n': rec['n'],
                'offset_x': float(rec.get('offset_x', 0.0)),
                'offset_y': float(rec.get('offset_y', 0.0)),
            }

        stack, base1, base2 = (
            snapshot(self.data_stack), snapshot(self.data_base1), snapshot(self.data_base2))
        tolerance = float(self.spin_tol.value())
        self._run_background_task(
            "胶厚自动匹配",
            lambda progress, cancel: self._auto_registration_payload(
                stack, base1, base2, tolerance, progress, cancel),
            self._apply_auto_registration,
            self._on_gap_auto_failure,
            on_cancel=self._on_gap_auto_cancelled,
        )

    def _on_gap_auto_failure(self, message):
        if hasattr(self, 'lbl_gap_auto_feedback'):
            self.lbl_gap_auto_feedback.setStyleSheet("color: #b91c1c; font-size: 11px;")
            self.lbl_gap_auto_feedback.setText(f"自动匹配失败：{message}")
        self._show_status("自动精对齐失败；当前人工偏移已保留。", 8000)
        QMessageBox.critical(
            self, "自动匹配失败", f"{message}\n\n未应用任何自动结果，当前人工偏移保持不变。")

    def _on_gap_auto_cancelled(self):
        if hasattr(self, 'lbl_gap_auto_feedback'):
            self.lbl_gap_auto_feedback.setStyleSheet("color: #92400e; font-size: 11px;")
            self.lbl_gap_auto_feedback.setText("自动匹配已取消，当前人工偏移保持不变。")
        self._show_status("自动精对齐已取消；当前人工偏移已保留。", 8000)

    def _apply_auto_registration(self, result):
        applied = False
        details = []
        for key, values in result.items():
            rec = getattr(self, f"data_{key}", None)
            label = '单片 1' if key == 'base1' else '单片 2'
            if rec is None:
                continue
            current = np.array([
                float(rec.get('offset_x', 0.0)), float(rec.get('offset_y', 0.0))])
            start = np.array([
                float(values.get('start_offset_x', current[0])),
                float(values.get('start_offset_y', current[1])),
            ])
            if not np.allclose(current, start, rtol=0.0, atol=1e-12):
                details.append(f"{label}: 数据已变化，保留当前人工偏移")
                continue
            candidate = np.array([
                float(values.get('offset_x', np.nan)),
                float(values.get('offset_y', np.nan)),
            ])
            adjustment = float(values.get('adjustment', np.inf))
            max_adjustment = float(values.get('max_adjustment', -np.inf))
            valid_result = (
                values.get('accepted', False)
                and np.isfinite(candidate).all()
                and np.isfinite(adjustment)
                and np.isfinite(max_adjustment)
                and adjustment <= max_adjustment
            )
            if not valid_result:
                if values.get('reason') == 'max_adjustment' or adjustment > max_adjustment:
                    details.append(
                        f"{label}: 自动精对齐结果偏离当前人工位置过大，已保留人工配准结果")
                elif values.get('reason') == 'overlap_not_improved':
                    details.append(
                        f"{label}: 单片到堆叠的重叠匹配质量未改善，已保留当前人工偏移")
                elif values.get('reason') == 'insufficient_local_overlap':
                    details.append(
                        f"{label}: 当前人工位置附近的重叠候选点不足，请先拖近后重试")
                else:
                    details.append(f"{label}: 自动结果无效，已保留当前人工偏移")
                continue
            rec['offset_x'] = float(candidate[0])
            rec['offset_y'] = float(candidate[1])
            rec['registration_mode'] = 'auto'
            applied = True
            quality_rms = float(values.get('overlap_rms', values['rms']))
            quality_mean = float(values.get('overlap_mean', np.nan))
            mean_text = f"均值 {quality_mean * 1000:.2f} µm / " if np.isfinite(quality_mean) else ""
            matched = int(values.get('matched', 0))
            moving_points = int(values.get('moving_points', rec.get('n', 0)))
            details.append(
                f"{label}: ΔX {values['offset_x']:+.4f} mm，ΔY {values['offset_y']:+.4f} mm，"
                f"单片→堆叠残差 {mean_text}RMS {quality_rms * 1000:.2f} µm，"
                f"容差内 {matched:,}/{moving_points:,} 点")
        if applied:
            self._invalidate_gap_result()
        self._update_gap_slot_labels()
        self._refresh_gap_registration(preserve_view=True)
        prefix = "自动精对齐完成" if applied else "自动精对齐未应用"
        feedback = prefix + " | " + " | ".join(details)
        if hasattr(self, 'lbl_gap_auto_feedback'):
            color = '#166534' if applied else '#92400e'
            self.lbl_gap_auto_feedback.setStyleSheet(f"color: {color}; font-size: 11px;")
            self.lbl_gap_auto_feedback.setText(feedback)
        self._show_status(feedback, 12000)

    @classmethod
    def _compute_gap_payload(cls, stack, base1, base2, tolerance, progress, cancel_event):
        def check_cancel():
            if cancel_event.is_set():
                raise TaskCancelled()

        sx = np.asarray(stack['x'], dtype=float)
        sy = np.asarray(stack['y'], dtype=float)
        sz = np.asarray(stack['z'], dtype=float)
        b1x, b1y = cls._shifted_xy(base1)
        progress(8, "正在建立单片 1 空间索引")
        tree1 = cKDTree(np.column_stack([b1x, b1y]))
        check_cancel()
        progress(30, "正在匹配单片 1")
        dist1, idx1 = tree1.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
        valid1 = dist1 <= tolerance
        reports = [cls._match_report("单片1", dist1, idx1, valid1)]
        valid2 = None
        dist2 = None
        idx2 = None

        if base2 is not None:
            b2x, b2y = cls._shifted_xy(base2)
            check_cancel()
            progress(48, "正在建立单片 2 空间索引")
            tree2 = cKDTree(np.column_stack([b2x, b2y]))
            progress(68, "正在匹配单片 2")
            dist2, idx2 = tree2.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
            valid2 = dist2 <= tolerance
            reports.append(cls._match_report("单片2", dist2, idx2, valid2))
            valid = valid1 & valid2
            gap_z = sz[valid] - base1['z'][idx1[valid]] - base2['z'][idx2[valid]]
        else:
            b2x = b2y = None
            valid = valid1
            gap_z = sz[valid] - base1['z'][idx1[valid]]
        check_cancel()
        if len(gap_z) < 10:
            raise ValueError("容差范围内配对成功的有效点不足。请先粗对齐/自动匹配，或检查误差窗口。")
        progress(92, "正在整理 Gap 结果")
        gap_name = f"GAP({stack['name']} - {base1['name']}"
        if base2 is not None:
            gap_name += f" - {base2['name']}"
        gap_name += ")"
        details = {
            'stack_z': sz[valid].copy(),
            'base1_index': idx1[valid].copy(),
            'base1_x': b1x[idx1[valid]].copy(),
            'base1_y': b1y[idx1[valid]].copy(),
            'base1_z': np.asarray(base1['z'])[idx1[valid]].copy(),
            'base1_distance': dist1[valid].copy(),
        }
        if base2 is not None:
            details.update({
                'base2_index': idx2[valid].copy(),
                'base2_x': b2x[idx2[valid]].copy(),
                'base2_y': b2y[idx2[valid]].copy(),
                'base2_z': np.asarray(base2['z'])[idx2[valid]].copy(),
                'base2_distance': dist2[valid].copy(),
            })
        plane_metrics = AnalysisMixin.compute_plane_metrics(sx[valid], sy[valid], gap_z)
        return {
            'x': sx[valid], 'y': sy[valid], 'z': gap_z, 'name': gap_name,
            'matching_xy': {
                'stack': {'x': sx.copy(), 'y': sy.copy()},
                'base1': {'x': b1x.copy(), 'y': b1y.copy()},
                **({} if base2 is None else {'base2': {'x': b2x.copy(), 'y': b2y.copy()}}),
            },
            'input_traces': {key: {field: deepcopy(rec.get(field)) for field in
                ('name', 'pipeline', 'filter', 'single_recipe', 'metric_quality', 'import_info')}
                for key, rec in (('stack', stack), ('base1', base1), ('base2', base2)) if rec is not None},
            'plane_metrics': plane_metrics, 'plane_metric_count': len(gap_z),
            'details': details,
            'reports': reports, 'tolerance': float(tolerance),
            'sampled': any(rec.get('sampled', False) for rec in (stack, base1, base2) if rec),
            'extrema_preserved': all(rec.get('metric_quality', {}).get('extrema_preserved', True)
                                     for rec in (stack, base1, base2) if rec),
            'layers': {
                'stack': {'name': stack['name'], 'n': int(stack.get('n', len(stack['z']))),
                          'offset_x': 0.0, 'offset_y': 0.0,
                          'registration_mode': 'none'},
                'base1': {'name': base1['name'], 'n': int(base1.get('n', len(base1['z']))),
                          'offset_x': float(base1.get('offset_x', 0.0)),
                          'offset_y': float(base1.get('offset_y', 0.0)),
                          'registration_mode': base1.get('registration_mode', 'none')},
                'base2': None if base2 is None else {
                    'name': base2['name'], 'n': int(base2.get('n', len(base2['z']))),
                    'offset_x': float(base2.get('offset_x', 0.0)),
                    'offset_y': float(base2.get('offset_y', 0.0)),
                    'registration_mode': base2.get('registration_mode', 'none')},
            },
            'diagnostic': {
                'layers': {
                    'base1': {'dist': dist1.copy(), 'idx': idx1.copy(), 'valid': valid1.copy()},
                    **({} if valid2 is None else {
                        'base2': {'dist': dist2.copy(), 'idx': idx2.copy(), 'valid': valid2.copy()}}),
                },
                'final_valid': valid.copy(), 'tolerance': float(tolerance),
            },
        }

    def calculate_gap(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.critical(self, "数据缺失", "执行运算至少需要设置【堆叠总成】和【单片 1】。")
            return
        tolerance = self.spin_tol.value()
        diag = self._registration_diagnostic(
            self.data_stack, self.data_base1, self.data_base2, tolerance)
        matched = int(diag['final_valid'].sum())
        desc = (f"即将执行: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if self.data_base2 else ''}\n\n"
                f"堆叠总成: {self.data_stack['name']} ({self.data_stack['n']:,} 点)\n"
                f"单片 1: {self.data_base1['name']} ({self.data_base1['n']:,} 点)\n")
        if self.data_base2 is not None:
            desc += f"单片 2: {self.data_base2['name']} ({self.data_base2['n']:,} 点)\n"
        desc += (f"\n容差窗口: {tolerance} mm\n"
                 f"当前预计参与扣减: {matched:,} / {self.data_stack['n']:,} 点\n\n请确认以上数据来源无误。")
        if QMessageBox.question(
                self, "确认计算", desc,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        stack, base1, base2 = self.data_stack, self.data_base1, self.data_base2
        self._run_background_task(
            "多层胶厚扣减",
            lambda progress, cancel: self._compute_gap_payload(stack, base1, base2, tolerance, progress, cancel),
            self._apply_gap_payload,
            lambda message: QMessageBox.critical(self, "运算失败", f"点云对齐错误: {message}"),
        )

    def _apply_gap_payload(self, payload):
        self.gap_result = payload
        self.df_raw = pd.DataFrame({'Z': payload['z'], 'X': payload['x'], 'Y': payload['y']})
        self._df_version += 1
        self._invalidate_smart_roi_runtime_cache(
            topology=True, masks=True, reason='Gap结果替换当前数据')
        self.absolute_raw_df = None
        self.current_source_name = payload['name']
        self.lbl_source.setText(f"当前数据: {payload['name']}")
        self.import_info = {
            'file_size_bytes': 0, 'file_size_mb': 0.0, 'source_path': '', 'source_sha256': '',
            'strategy': 'Gap计算结果', 'sampled': payload['sampled'],
            'sample_method_key': 'derived_gap', 'extrema_preserved': payload['extrema_preserved'],
            'import_rows': len(self.df_raw), 'valid_rows': len(self.df_raw),
            'display_limit': self._display_limit(), 'large_file_mode': self._bigfile_mode_label(),
            'notes': '由已保存的多层 XY 配准位移与容差匹配计算生成',
            'gap_result_stale': False,
        }
        self._update_import_status_label()
        self.transform_pipeline = []
        self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
        self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
        self.manual_delete_operations = []
        self._manual_delete_mask_history = []
        self.pending_delete_operation = None
        self.current_coeffs = None
        self.clear_rois(update=False)
        self.update_analysis()
        self._gap_plane_metrics(payload)
        self.tabs.setCurrentIndex(self.math_tab_index)
        self._on_tab_changed(self.math_tab_index)
        self._refresh_gap_registration(preserve_view=True)
        self._sync_gap_action_state()
        self._show_status(f"Gap 计算完成，共 {len(self.df_raw):,} 个有效匹配点。", 8000)
        message = (f"成功配对并算出 Inner Gap\n容差设定: {payload['tolerance']} mm\n"
                   f"成功对齐点数: {len(self.df_raw):,}\n\n匹配质量报告\n" + "\n".join(payload['reports']))
        QMessageBox.information(self, "计算成功", message)

    def _gap_result_frame(self):
        payload = self.gap_result
        data = {
            'X_mm': payload['x'], 'Y_mm': payload['y'], 'Gap_mm': payload['z'],
            'Stack_Z_mm': payload['details']['stack_z'],
            'Base1_Index': payload['details']['base1_index'],
            'Base1_X_Aligned_mm': payload['details']['base1_x'],
            'Base1_Y_Aligned_mm': payload['details']['base1_y'],
            'Base1_Z_mm': payload['details']['base1_z'],
            'Base1_Match_Distance_mm': payload['details']['base1_distance'],
        }
        if payload['layers']['base2'] is not None:
            data.update({
                'Base2_Index': payload['details']['base2_index'],
                'Base2_X_Aligned_mm': payload['details']['base2_x'],
                'Base2_Y_Aligned_mm': payload['details']['base2_y'],
                'Base2_Z_mm': payload['details']['base2_z'],
                'Base2_Match_Distance_mm': payload['details']['base2_distance'],
            })
        return pd.DataFrame(data)

    def _gap_plane_metrics(self, payload=None):
        """Metrics belong to the exported gap snapshot, not mutable main-page state."""
        payload = self.gap_result if payload is None else payload
        if payload is None:
            raise ValueError("暂无有效胶厚结果。")
        metrics = payload.get('plane_metrics')
        if metrics is None:
            metrics = self.compute_plane_metrics(
                np.asarray(payload['x'], dtype=float),
                np.asarray(payload['y'], dtype=float),
                np.asarray(payload['z'], dtype=float))
            payload['plane_metrics'] = metrics
            payload['plane_metric_count'] = int(len(payload['z']))
        return metrics

    def export_gap_csv(self):
        if self.gap_result is None:
            QMessageBox.warning(self, "暂无结果", "请先完成容差匹配并计算胶厚。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出胶厚 CSV", dialog_initial_path('export', "Gap_Result.csv"),
            "CSV (*.csv)")
        if not path:
            return
        remember_dialog_path('export', path)
        try:
            payload = self.gap_result
            metrics = self._gap_plane_metrics(payload)
            with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                handle.write(f"# ===== 多层胶厚扣减 {self.APP_VERSION} 导出 =====\n")
                handle.write(f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                handle.write(f"# 公式: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if payload['layers']['base2'] else ''}\n")
                handle.write("# 坐标系: X/Y 毫米；配准仅做 XY 平移；堆叠总成固定\n")
                handle.write("# 结果口径: 全部有效匹配点的原始胶厚快照；不包含主控后续旋转、ROI或滤波\n")
                handle.write(f"# 匹配规则: 最近邻欧氏距离 <= {payload['tolerance']:.6f} mm\n")
                handle.write(f"# Gap_Rx_urad: {metrics['rx']:.9f}\n")
                handle.write(f"# Gap_Ry_urad: {metrics['ry']:.9f}\n")
                for key, label in (('stack', '堆叠总成'), ('base1', '单片1'), ('base2', '单片2')):
                    rec = payload['layers'].get(key)
                    if rec is not None:
                        handle.write(
                            f"# {label}: {rec['name']} | 点数 {rec['n']} | "
                            f"ΔX {rec['offset_x']:+.6f} mm | ΔY {rec['offset_y']:+.6f} mm | "
                            f"配准方式 {('固定基准' if key == 'stack' else self.GAP_REGISTRATION_MODE_LABELS.get(rec.get('registration_mode', 'none'), '未配准'))}\n")
                handle.write('# 输入处理参数快照: ' + json.dumps(payload.get('input_traces', {}), ensure_ascii=False, default=lambda v: v.tolist() if isinstance(v,np.ndarray) else str(v)) + '\n')
                handle.write(f"# 有效匹配点: {len(payload['z'])}\n")
                self._gap_result_frame().to_csv(handle, index=False)
            self._show_status(f"胶厚 CSV 已导出: {path}", 6000)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _gap_report_default_name(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return re.sub(r'[<>:"/\\|?*\r\n]+', '_', f"Gap_Report_{stamp}.png")

    def _refresh_gap_result_views(self):
        if not hasattr(self, 'gap_result_figure'):
            return
        payload = self.gap_result
        if getattr(self, '_gap_view_payload', object()) is payload:
            return
        self._gap_view_payload = payload
        self.gap_result_figure.clear()
        from ..result_views import draw_four_views
        if payload is None:
            self.gap_result_summary.setText('尚未计算膜厚；配准和数据层操作保持不变')
            self.gap_result_figure.text(.5, .5, '暂无膜厚结果', ha='center', va='center')
        else:
            m = self._gap_plane_metrics(payload)
            self.gap_result_summary.setText(
                f"有效匹配 {len(payload['z']):,} 点 · Mean {m['mean_z']:.6g} mm · "
                f"PV {m['pv']:.5g} µm · TTV {m['ttv']:.5g} µm · "
                f"Rx {m['rx']:.5g} / Ry {m['ry']:.5g} µrad · 原始扣减快照")
            draw_four_views(self.gap_result_figure, payload['x'], payload['y'], payload['z'],
                            quantity='Inner Gap', unit='mm')
        self.gap_result_canvas.draw_idle()

    def _render_gap_report_figure(self):
        if self.gap_result is None:
            raise ValueError('暂无有效胶厚结果。')
        from ..result_views import draw_four_views, snapshot_report
        from textwrap import fill
        payload = self.gap_result
        m = self._gap_plane_metrics(payload)
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        fig = Figure(constrained_layout=True, facecolor='white')
        FigureCanvasAgg(fig)
        left = [f"{key}: {record['name']}" for key, record in payload['layers'].items() if record]
        for key, trace in payload.get('input_traces', {}).items():
            single = trace.get('single_recipe') or {}
            pose = ' → '.join(single.get('transform_pipeline', [])) or '原始状态'
            roi = '启用' if (single.get('roi') or {}).get('enabled', False) else '关闭'
            left.append(f"{key} 处理: 姿态 {pose} / 滤波 {(single.get('filter') or {}).get('mode_text', '关闭')}；ROI {roi}"
                        if single else f'{key} 处理: 未记录可重放单层参数（内存或旧槽）')
            quality = trace.get('metric_quality') or {}
            left.append(f"{key} 质量: {quality.get('label', '')} {quality.get('warning', '')}")
        right = [f"匹配 {len(payload['z']):,} 点 / 堆叠 {payload['layers']['stack']['n']:,} 点 · "
                 f"匹配率 {len(payload['z']) / max(1, payload['layers']['stack']['n']):.2%}",
                 f"容差 {payload['tolerance']:.6g} mm · Mean {m['mean_z']:.6g} mm",
                 f"法向 PV {m['pv']:.6g} µm · RMS {m['rms']:.6g} µm · TTV {m['ttv']:.6g} µm",
                 f"Rx {m['rx']:.6g} / Ry {m['ry']:.6g} µrad",
                 judgment_summary(self.current_judgment('gap')),
                 '全部图表来自同一原始扣减快照，不含主控后续编辑。',
                 f"拟合点 {int(payload.get('plane_metric_count',len(payload['z']))):,}；最小 / 最大 "
                 f"{np.min(payload['z']):.6g} / {np.max(payload['z']):.6g} mm"]
        for key, tag in (('base1','单片 1'),('base2','单片 2')):
            rec = payload['layers'].get(key)
            if rec is not None:
                right.append(f"{tag} 配准 ΔX {rec['offset_x']:+.6g} / ΔY {rec['offset_y']:+.6g} mm；"
                             f"{self.GAP_REGISTRATION_MODE_LABELS.get(rec.get('registration_mode','none'),'未配准')}")
        for key, tag in (('base1_distance', '单片 1'), ('base2_distance', '单片 2')):
            distances = np.asarray(payload.get('details', {}).get(key, []), dtype=float)
            if len(distances):
                right.append(f"{tag} 匹配距离均值 {distances.mean()*1000:.6g} µm / RMS {np.sqrt(np.mean(distances**2))*1000:.6g} µm")
        four, matching = snapshot_report(fig, f'多层胶厚扣减报告 ({self.APP_VERSION})', left, right, fifth=True)
        draw_four_views(fig, payload['x'], payload['y'], payload['z'], spec=four,
                        quantity='Inner Gap', unit='mm')
        ax = fig.add_subplot(matching)
        xy = payload.get('matching_xy', {})
        # Legacy snapshots only contain matched point coordinates. Never read
        # current slots here: they may belong to another measurement now.
        for key, label in (('stack', '堆叠总成'), ('base1', '单片 1'), ('base2', '单片 2')):
            rec = xy.get(key)
            if rec is not None:
                count = len(rec['x']);pick = np.linspace(0, count-1, min(count,35000), dtype=int)
                ax.scatter(np.asarray(rec['x'])[pick], np.asarray(rec['y'])[pick],
                           c=self.GAP_LAYER_COLORS[key], s=8, alpha=.5, label=label)
        pick = np.linspace(0,len(payload['x'])-1,min(len(payload['x']),35000),dtype=int)
        ax.scatter(np.asarray(payload['x'])[pick],np.asarray(payload['y'])[pick],s=18,
                   facecolors='none',edgecolors=self.GAP_LAYER_COLORS['matched'],label='参与扣减点')
        ax.set(title='XY 配准与容差匹配',xlabel='X (mm)',ylabel='Y (mm)',aspect='equal')
        ax.legend(loc='best',fontsize=8);ax.grid(True,alpha=.2)
        return fig

    def export_gap_report(self):
        if self.gap_result is None:
            QMessageBox.warning(self, "暂无结果", "请先完成容差匹配并计算胶厚。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出胶厚报告图",
            dialog_initial_path('export', self._gap_report_default_name()),
            "PNG 图片 (*.png);;All Files (*)")
        if not path:
            return
        remember_dialog_path('export', path)
        try:
            fig = self._render_gap_report_figure()
            fig.savefig(path, dpi=150)
            self._show_status(f"胶厚报告图已导出: {path}", 6000)
            QMessageBox.information(self, "导出成功", f"胶厚报告图已导出：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
