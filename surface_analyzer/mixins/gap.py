"""Multi-layer gap registration, subtraction, and export workflow."""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from scipy.spatial import cKDTree

from ..workers import TaskCancelled


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
        self.lbl_stack_status.setStyleSheet(
            f"color: {'#475569' if self.data_stack is not None else '#c0392b'}; font-weight: bold;")
        self.lbl_base1_status.setStyleSheet(
            f"color: {'#2563eb' if self.data_base1 is not None else '#c0392b'}; font-weight: bold;")
        self.lbl_base2_status.setStyleSheet(
            f"color: {'#7c3aed' if self.data_base2 is not None else '#7f8c8d'}; font-weight: bold;")

    def _sync_gap_action_state(self):
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

    def _invalidate_gap_result(self, reason=None):
        old = getattr(self, 'gap_result', None)
        self.gap_result = None
        if old is not None and self.current_source_name == old.get('name'):
            self.import_info['gap_result_stale'] = True
            self.lbl_source.setText(f"当前数据: {self.current_source_name} [配准已变更，旧结果]")
        self._sync_gap_action_state()
        if reason:
            self._show_status(f"胶厚结果已失效：{reason}，请重新计算。", 5000)

    def set_memory_slot(self, slot):
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "错误", "主界面尚无数据，请先载入并处理。")
            return
        if not self._confirm_estimated_metrics('写入多层扣减寄存器'):
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        rec = {
            'x': fx.copy(), 'y': fy.copy(), 'z': fz.copy(),
            'name': self.current_source_name, 'n': len(fz),
            'metric_quality': dict(self._current_metric_quality()),
            'sampled': bool(self.import_info.get('sampled', False)),
            'offset_x': 0.0, 'offset_y': 0.0,
            'registration_mode': 'none',
        }
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
        self.data_stack = self.data_base1 = self.data_base2 = None
        self.gap_active_layer = None
        self.gap_result = None
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

    def _refresh_gap_registration(self, preserve_view=True):
        tolerance = float(self.spin_tol.value()) if hasattr(self, 'spin_tol') else 0.05
        diag = self._registration_diagnostic(
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
        return diag

    def _update_gap_diagnostic(self, diag):
        """Compatibility wrapper retained for callers and older tests."""
        self._refresh_gap_registration()

    @staticmethod
    def _match_report(tag, dist, idx, valid):
        matched_dist = dist[valid]
        if len(matched_dist) == 0:
            return f"[{tag}] 无匹配点"
        rms_um = np.sqrt(np.mean(matched_dist ** 2)) * 1000
        max_um = np.max(matched_dist) * 1000
        unique_ratio = len(np.unique(idx[valid])) / valid.sum() * 100
        text = (f"[{tag}] 匹配 {int(valid.sum())} / 未匹配 {int((~valid).sum())}\n"
                f"    匹配距离 RMS {rms_um:.2f} µm | Max {max_um:.2f} µm\n"
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
        if len(points) > limit:
            pick = np.linspace(0, len(points) - 1, limit, dtype=int)
            points = points[pick]
        return points

    @classmethod
    def _optimize_translation(cls, stack, moving, tolerance, cancel_event=None):
        """Locally refine the current translation using symmetric nearest-point residual."""
        reference = cls._registration_sample(stack['x'], stack['y'])
        moving_xy = cls._registration_sample(moving['x'], moving['y'])
        if len(reference) < 3 or len(moving_xy) < 3:
            raise ValueError("自动匹配至少需要每层 3 个有效 XY 点。")
        tree_ref = cKDTree(reference)
        start = np.array([
            float(moving.get('offset_x', 0.0)),
            float(moving.get('offset_y', 0.0)),
        ])
        max_adjustment = max(float(tolerance) * 3.0, 0.01)

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
                finite = np.isfinite(dist)
                if finite.sum() < 3:
                    break
                cutoff = float(np.quantile(dist[finite], 0.90))
                keep = finite & (dist <= max(cutoff, float(tolerance)))
                residual = reference[idx[keep]] - shifted[keep]
                delta = np.mean(residual, axis=0)
                offset += delta
                if float(np.hypot(*(offset - start))) > max_adjustment:
                    break
                if float(np.hypot(delta[0], delta[1])) < 1e-9:
                    break
            return offset, iterations

        def score(offset):
            shifted = moving_xy + offset
            tree_moving = cKDTree(shifted)
            d_ref, _ = tree_moving.query(reference)
            d_mov, _ = tree_ref.query(shifted)
            residuals = np.concatenate([d_ref, d_mov])
            rms = float(np.sqrt(np.mean(residuals ** 2)))
            matched = int(np.sum(d_ref <= tolerance))
            return rms, matched

        start_rms, start_matched = score(start)
        proposed, iterations = refine()
        adjustment = float(np.hypot(*(proposed - start)))
        if not np.isfinite(proposed).all() or adjustment > max_adjustment:
            return {
                'accepted': False,
                'reason': 'max_adjustment',
                'offset_x': float(start[0]),
                'offset_y': float(start[1]),
                'proposed_offset_x': float(proposed[0]),
                'proposed_offset_y': float(proposed[1]),
                'rms': start_rms,
                'matched': start_matched,
                'iterations': int(iterations),
                'adjustment': adjustment,
                'max_adjustment': max_adjustment,
                'start_offset_x': float(start[0]),
                'start_offset_y': float(start[1]),
            }
        rms, matched = score(proposed)
        if rms > start_rms + 1e-12:
            return {
                'accepted': False,
                'reason': 'no_improvement',
                'offset_x': float(start[0]),
                'offset_y': float(start[1]),
                'proposed_offset_x': float(proposed[0]),
                'proposed_offset_y': float(proposed[1]),
                'rms': start_rms,
                'matched': start_matched,
                'iterations': int(iterations),
                'adjustment': adjustment,
                'max_adjustment': max_adjustment,
                'start_offset_x': float(start[0]),
                'start_offset_y': float(start[1]),
            }
        return {
            'accepted': True,
            'reason': '',
            'offset_x': float(proposed[0]),
            'offset_y': float(proposed[1]),
            'rms': rms,
            'matched': matched,
            'iterations': int(iterations),
            'adjustment': adjustment,
            'max_adjustment': max_adjustment,
            'start_offset_x': float(start[0]),
            'start_offset_y': float(start[1]),
        }

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
        self._show_status("自动精对齐失败；当前人工偏移已保留。", 8000)
        QMessageBox.critical(
            self, "自动匹配失败", f"{message}\n\n未应用任何自动结果，当前人工偏移保持不变。")

    def _on_gap_auto_cancelled(self):
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
                else:
                    details.append(f"{label}: 自动结果无效，已保留当前人工偏移")
                continue
            rec['offset_x'] = float(candidate[0])
            rec['offset_y'] = float(candidate[1])
            rec['registration_mode'] = 'auto'
            applied = True
            details.append(
                f"{label}: ΔX {values['offset_x']:+.4f} mm，ΔY {values['offset_y']:+.4f} mm，"
                f"全点残差 RMS {values['rms'] * 1000:.2f} µm")
        if applied:
            self._invalidate_gap_result()
        self._update_gap_slot_labels()
        self._refresh_gap_registration(preserve_view=True)
        prefix = "自动精对齐完成" if applied else "自动精对齐未应用"
        self._show_status(prefix + " | " + " | ".join(details), 12000)

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
        return {
            'x': sx[valid], 'y': sy[valid], 'z': gap_z, 'name': gap_name,
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
        if self.last_metrics is not None:
            payload['plane_metrics'] = dict(self.last_metrics)
            payload['plane_metric_count'] = int(len(self.active_idx))
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
        """Return the main-analysis plane metrics for the final Inner Gap points."""
        payload = self.gap_result if payload is None else payload
        if payload is None:
            raise ValueError("暂无有效胶厚结果。")
        if (payload is self.gap_result
                and self.current_source_name == payload.get('name')
                and self.last_metrics is not None):
            metrics = dict(self.last_metrics)
            payload['plane_metrics'] = metrics
            payload['plane_metric_count'] = int(len(self.active_idx))
            return metrics
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
        path, _ = QFileDialog.getSaveFileName(self, "导出胶厚 CSV", "Gap_Result.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            payload = self.gap_result
            metrics = self._gap_plane_metrics(payload)
            with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                handle.write(f"# ===== 多层胶厚扣减 {self.APP_VERSION} 导出 =====\n")
                handle.write(f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                handle.write(f"# 公式: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if payload['layers']['base2'] else ''}\n")
                handle.write("# 坐标系: X/Y 毫米；配准仅做 XY 平移；堆叠总成固定\n")
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
                handle.write(f"# 有效匹配点: {len(payload['z'])}\n")
                self._gap_result_frame().to_csv(handle, index=False)
            self._show_status(f"胶厚 CSV 已导出: {path}", 6000)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _gap_report_default_name(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return re.sub(r'[<>:"/\\|?*\r\n]+', '_', f"Gap_Report_{stamp}.png")

    def _render_gap_report_figure(self):
        if self.gap_result is None:
            raise ValueError("暂无有效胶厚结果。")
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        payload = self.gap_result
        z = np.asarray(payload['z'], dtype=float)
        metrics = self._gap_plane_metrics(payload)
        fig = Figure(figsize=(16.0, 9.2), constrained_layout=True)
        FigureCanvasAgg(fig)
        grid = fig.add_gridspec(2, 2, width_ratios=[0.82, 1.75], height_ratios=[1, 1])
        ax_info = fig.add_subplot(grid[:, 0]); ax_info.axis('off')
        ax_layers = fig.add_subplot(grid[0, 1])
        ax_gap = fig.add_subplot(grid[1, 1])

        layers = payload['layers']
        lines = [
            "结果摘要",
            f"有效匹配点  {len(z):,}",
            f"平面拟合点  {int(payload.get('plane_metric_count', len(z))):,}",
            f"容差窗口  {payload['tolerance']:.3f} mm",
            f"平均胶厚  {metrics['mean_z']:.6f} mm",
            f"Gap Rx  {metrics['rx']:.2f} µrad",
            f"Gap Ry  {metrics['ry']:.2f} µrad",
            f"平面残差 PV  {metrics['pv']:.3f} µm",
            f"平面残差 RMS  {metrics['rms']:.3f} µm",
            f"TTV  {metrics['ttv']:.3f} µm",
            f"最小 / 最大  {np.min(z):.6f} / {np.max(z):.6f} mm",
            "",
            "XY 配准位移（堆叠固定）",
            f"单片 1  ΔX {layers['base1']['offset_x']:+.5f} mm  ΔY {layers['base1']['offset_y']:+.5f} mm  "
            f"{self.GAP_REGISTRATION_MODE_LABELS.get(layers['base1'].get('registration_mode', 'none'), '未配准')}",
        ]
        if layers['base2'] is not None:
            lines.append(
                f"单片 2  ΔX {layers['base2']['offset_x']:+.5f} mm  ΔY {layers['base2']['offset_y']:+.5f} mm  "
                f"{self.GAP_REGISTRATION_MODE_LABELS.get(layers['base2'].get('registration_mode', 'none'), '未配准')}")
        lines.extend(["", "数据来源", f"堆叠  {Path(str(layers['stack']['name'])).name}",
                      f"单片 1  {Path(str(layers['base1']['name'])).name}"])
        if layers['base2'] is not None:
            lines.append(f"单片 2  {Path(str(layers['base2']['name'])).name}")
        lines.extend(["", "口径", "XY 最近邻欧氏距离不大于容差的点参与扣减。",
                      "Rx/Ry 与主控页共用最终点集的最佳拟合平面定义。",
                      "自动精对齐与手动拖动只改变单片层 XY 平移，不改变 Z。"])
        ax_info.text(0.02, 0.98, "\n".join(lines), va='top', ha='left', fontsize=10.5,
                     linespacing=1.55, color='#334155', transform=ax_info.transAxes,
                     bbox=dict(boxstyle='round,pad=0.7', fc='#f8fafc', ec='#dbe3ec'))

        def sample(x, y, limit=35000):
            x, y = np.asarray(x), np.asarray(y)
            if len(x) > limit:
                pick = np.linspace(0, len(x) - 1, limit, dtype=int)
                return x[pick], y[pick]
            return x, y

        for key, rec, label in (
                ('stack', self.data_stack, '堆叠总成'),
                ('base1', self.data_base1, '单片 1'),
                ('base2', self.data_base2, '单片 2')):
            if rec is None:
                continue
            x, y = self._shifted_xy(rec)
            x, y = sample(x, y)
            ax_layers.scatter(x, y, s=8, alpha=0.58, edgecolors='none',
                              c=self.GAP_LAYER_COLORS[key], label=label, rasterized=True)
        vx, vy = sample(payload['x'], payload['y'])
        ax_layers.scatter(vx, vy, s=24, facecolors='none',
                          edgecolors=self.GAP_LAYER_COLORS['matched'], linewidths=0.8,
                          alpha=0.92, label='全部参与扣减点', rasterized=True)
        ax_layers.set_title("XY 配准与容差匹配")
        ax_layers.set_xlabel("X (mm)"); ax_layers.set_ylabel("Y (mm)")
        ax_layers.grid(True, color='#edf0f3'); ax_layers.set_aspect('equal', adjustable='datalim')
        ax_layers.legend(loc='best', fontsize=8)

        gx, gy, gz = payload['x'], payload['y'], payload['z']
        if len(gz) > 50000:
            pick = np.linspace(0, len(gz) - 1, 50000, dtype=int)
            gx, gy, gz = gx[pick], gy[pick], gz[pick]
        scatter = ax_gap.scatter(gx, gy, c=gz, cmap='turbo', s=12, alpha=0.85,
                                 edgecolors='none', rasterized=True)
        ax_gap.set_title("Inner Gap XY 分布")
        ax_gap.set_xlabel("X (mm)"); ax_gap.set_ylabel("Y (mm)")
        ax_gap.grid(True, color='#edf0f3'); ax_gap.set_aspect('equal', adjustable='datalim')
        colorbar = fig.colorbar(scatter, ax=ax_gap, shrink=0.88, pad=0.02)
        colorbar.set_label("Inner Gap (mm)")
        fig.suptitle(f"多层胶厚扣减报告 ({self.APP_VERSION})", fontsize=17, fontweight='bold')
        return fig

    def export_gap_report(self):
        if self.gap_result is None:
            QMessageBox.warning(self, "暂无结果", "请先完成容差匹配并计算胶厚。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出胶厚报告图", self._gap_report_default_name(),
            "PNG 图片 (*.png);;All Files (*)")
        if not path:
            return
        try:
            fig = self._render_gap_report_figure()
            fig.savefig(path, dpi=150)
            self._show_status(f"胶厚报告图已导出: {path}", 6000)
            QMessageBox.information(self, "导出成功", f"胶厚报告图已导出：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
