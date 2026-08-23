"""Multi-layer gap registration and subtraction workflow."""

import pandas as pd
import numpy as np
from PyQt6.QtWidgets import QMessageBox
from scipy.spatial import cKDTree

from ..workers import TaskCancelled


class GapAnalysisMixin:
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
        }
        labels = {
            'stack': ("堆叠总成", self.lbl_stack_status, '#27ae60'),
            'base1': ("单片 1", self.lbl_base1_status, '#27ae60'),
            'base2': ("单片 2", self.lbl_base2_status, '#2980b9'),
        }
        setattr(self, f"data_{slot}", rec)
        name, label, color = labels[slot]
        label.setText(f"已存【{name}】\n来源: {rec['name']} (共 {rec['n']} 点)\n{rec['metric_quality']['label']}")
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def clear_memory_slot(self, slot):
        if slot == 'base2':
            self.data_base2 = None
            self.lbl_base2_status.setText("可选空置")
            self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")

    def clear_all_memory_slots(self):
        self.data_stack = self.data_base1 = self.data_base2 = None
        self.lbl_stack_status.setText("尚未设置")
        self.lbl_base1_status.setText("尚未设置")
        self.lbl_base2_status.setText("可选空置")
        self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self._update_gap_diagnostic(None)
        self._show_status("已清空全部寄存器", 3000)

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
        total = int(len(diag['final_valid']))
        matched = int(np.sum(diag['final_valid']))
        if hasattr(self, 'lbl_gap_matched'):
            self.lbl_gap_matched.setText(f"{matched:,}")
            self.lbl_gap_unmatched.setText(f"{total - matched:,}")
            self.lbl_gap_tolerance.setText(f"{diag['tolerance']:.3f}")
            self.lbl_gap_state.setText("已诊断" if matched >= 10 else "匹配不足")

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

    @classmethod
    def _compute_gap_payload(cls, stack, base1, base2, tolerance, progress, cancel_event):
        def check_cancel():
            if cancel_event.is_set():
                raise TaskCancelled()

        sx, sy, sz = stack['x'], stack['y'], stack['z']
        progress(8, "正在建立单片 1 空间索引")
        tree1 = cKDTree(np.column_stack([base1['x'], base1['y']]))
        check_cancel()
        progress(30, "正在匹配单片 1")
        dist1, idx1 = tree1.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
        valid1 = dist1 <= tolerance
        reports = [cls._match_report("单片1", dist1, idx1, valid1)]
        valid2 = None

        if base2 is not None:
            check_cancel()
            progress(48, "正在建立单片 2 空间索引")
            tree2 = cKDTree(np.column_stack([base2['x'], base2['y']]))
            progress(68, "正在匹配单片 2")
            dist2, idx2 = tree2.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
            valid2 = dist2 <= tolerance
            reports.append(cls._match_report("单片2", dist2, idx2, valid2))
            valid = valid1 & valid2
            gap_z = sz[valid] - base1['z'][idx1[valid]] - base2['z'][idx2[valid]]
        else:
            valid = valid1
            gap_z = sz[valid] - base1['z'][idx1[valid]]
        check_cancel()
        if len(gap_z) < 10:
            raise ValueError("容差范围内配对成功的有效点不足。请增大误差窗口，或检查各组数据是否已平移归零。")
        progress(92, "正在整理 Gap 结果")
        gap_name = f"GAP({stack['name']} - {base1['name']}"
        if base2 is not None:
            gap_name += f" - {base2['name']}"
        gap_name += ")"
        return {
            'x': sx[valid], 'y': sy[valid], 'z': gap_z, 'name': gap_name,
            'reports': reports, 'tolerance': float(tolerance),
            'sampled': any(rec.get('sampled', False) for rec in (stack, base1, base2) if rec),
            'extrema_preserved': all(rec.get('metric_quality', {}).get('extrema_preserved', True)
                                     for rec in (stack, base1, base2) if rec),
            'diagnostic': {
                'x': sx.copy(), 'y': sy.copy(), 'valid1': valid1.copy(),
                'valid2': valid2.copy() if valid2 is not None else None,
                'final_valid': valid.copy(), 'tolerance': float(tolerance),
                'stack_name': stack['name'], 'base1_name': base1['name'],
                'base2_name': base2['name'] if base2 is not None else None,
            },
        }

    def calculate_gap(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.critical(self, "数据缺失", "执行运算至少需要设置【堆叠总成】和【单片 1】。")
            return
        tolerance = self.spin_tol.value()
        desc = (f"即将执行: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if self.data_base2 else ''}\n\n"
                f"堆叠总成: {self.data_stack['name']} ({self.data_stack['n']} 点)\n"
                f"单片 1: {self.data_base1['name']} ({self.data_base1['n']} 点)\n")
        if self.data_base2 is not None:
            desc += f"单片 2: {self.data_base2['name']} ({self.data_base2['n']} 点)\n"
        desc += f"\n容差窗口: {tolerance} mm\n\n请确认以上数据来源无误。"
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
        self._update_gap_diagnostic(payload['diagnostic'])
        self.df_raw = pd.DataFrame({'Z': payload['z'], 'X': payload['x'], 'Y': payload['y']})
        self._df_version += 1
        self.absolute_raw_df = None
        self.current_source_name = payload['name']
        self.lbl_source.setText(f"当前数据: {payload['name']}")
        self.import_info = {
            'file_size_bytes': 0, 'file_size_mb': 0.0, 'source_path': '', 'source_sha256': '',
            'strategy': 'Gap计算结果', 'sampled': payload['sampled'],
            'sample_method_key': 'derived_gap', 'extrema_preserved': payload['extrema_preserved'],
            'import_rows': len(self.df_raw), 'valid_rows': len(self.df_raw),
            'display_limit': self._display_limit(), 'large_file_mode': self._bigfile_mode_label(),
            'notes': '由多层点云匹配计算生成',
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
        self._show_status(f"Gap 计算完成，共 {len(self.df_raw):,} 个有效匹配点。", 8000)
        message = (f"成功配对并算出 Inner Gap\n容差设定: {payload['tolerance']} mm\n"
                   f"成功对齐点数: {len(self.df_raw)}\n\n匹配质量报告\n" + "\n".join(payload['reports']))
        QMessageBox.information(self, "计算成功", message)
