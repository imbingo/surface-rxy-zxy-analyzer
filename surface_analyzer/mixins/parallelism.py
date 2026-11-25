"""ParallelismMixin extracted from the V3.9.3 application."""

import sys
import os
import re
import mmap
import json
import tempfile
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
from ..dialog_paths import dialog_initial_path, remember_dialog_path
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

from ..plotting import set_surface_box_aspect
from ..judgment import judgment_summary



class ParallelismMixin:
    def _current_parallel_record(self):
        if getattr(self,'_task_thread',None) is not None or getattr(self,'_pending_sigma_reanalysis',False):
            QMessageBox.warning(self,'单层处理尚未完成','请等待滤波或后台任务完成后再写入平行度数据槽。')
            return None
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "暂无数据", "请先在主页面导入并处理数据。")
            return None
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        analysis_z = getattr(self, '_analysis_z_full', None)
        if analysis_z is not None and len(analysis_z) == len(tz):
            tz = np.asarray(analysis_z, dtype=float)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        if len(fz) < 3:
            QMessageBox.warning(self, "点数不足", "参与拟合点少于 3 个，无法写入平行度分析。")
            return None
        try:
            metrics = self.compute_plane_metrics(fx, fy, fz)
        except ValueError as exc:
            QMessageBox.warning(self, "平面分析无效", str(exc))
            return None
        quality = self._current_metric_quality()
        import_snapshot = copy.deepcopy(getattr(self, 'import_info', {}) or {})
        return {
            'x': fx.copy(),
            'y': fy.copy(),
            'z': fz.copy(),
            'metrics': dict(metrics),
            'name': self.current_source_name or '--',
            'n': int(len(fz)),
            'filter': self.cb_filter.currentText(),
            'pipeline': " -> ".join(self.transform_pipeline) if self.transform_pipeline else "原始状态",
            'import_strategy': self.import_info.get('strategy', '--'),
            'sampled': bool(self.import_info.get('sampled', False)),
            'metric_quality': dict(quality),
            'import_info': import_snapshot,
            'single_recipe': self._slot_single_recipe_snapshot(),
            'recipe_name': str(getattr(self, 'current_recipe_name', '')),
        }

    @staticmethod
    def _parallel_trace(rec):
        quality = rec.get('metric_quality', {}) or {}
        return {
            'pipeline': str(rec.get('pipeline') or '原始状态'),
            'filter': str(rec.get('filter') or '关闭'),
            'import_strategy': str(rec.get('import_strategy') or '--'),
            'sampled': '是' if bool(rec.get('sampled', False)) else '否',
            'quality': str(quality.get('label') or '全量计算'),
            'n': int(rec.get('n', 0) or 0),
        }

    def set_parallel_surface(self, slot):
        rec = self._current_parallel_record()
        if rec is None:
            return
        if not self._confirm_estimated_metrics('写入平行度分析'):
            return
        if slot == 'base':
            self.parallel_base = rec
        else:
            self.parallel_measure = rec
        self.parallel_result = None
        self._update_parallel_ui()
        self.statusBar().showMessage(f"已写入{'基准面' if slot == 'base' else '测量面'}: {rec['name']} ({rec['n']:,} 点)", 5000)

    def swap_parallel_surfaces(self):
        self.parallel_base, self.parallel_measure = self.parallel_measure, self.parallel_base
        self.parallel_result = None
        self._update_parallel_ui()

    def clear_parallel_surfaces(self):
        self.parallel_base = None
        self.parallel_measure = None
        self.parallel_result = None
        self._update_parallel_ui()

    def _slot_status_text(self, rec):
        if rec is None:
            return "尚未设置"
        quality = rec.get('metric_quality', {})
        quality_text = quality.get('label', '全量计算')
        return (f"来源: {rec['name']}\n"
                f"参与拟合: {rec['n']:,} 点 | 导入: {rec['import_strategy']} | {quality_text}\n"
                f"Rx {rec['metrics']['rx']:.2f} µrad | Ry {rec['metrics']['ry']:.2f} µrad")

    def _update_parallel_ui(self):
        self._refresh_composite_save_buttons()
        self._parallel_revision = int(getattr(self, '_parallel_revision', 0) or 0) + 1
        if hasattr(self, 'adjustment_panel'):
            self.adjustment_panel.invalidate('基准 / 测量面已变化，请完成平行度计算后重新计算装调量。')
        if hasattr(self, 'lbl_parallel_base_status'):
            self.lbl_parallel_base_status.setText(self._slot_status_text(self.parallel_base))
            self.lbl_parallel_measure_status.setText(self._slot_status_text(self.parallel_measure))
            for label, record in ((self.lbl_parallel_base_status, self.parallel_base),
                                  (self.lbl_parallel_measure_status, self.parallel_measure)):
                label.setStyleSheet(self.SLOT_EMPTY_STYLE if record is None
                                    else "color: #475569; font-weight: 400;")
            if hasattr(self, '_sync_status_widgets'):
                self._sync_status_widgets()
        if hasattr(self, 'parallel_canvas'):
            self.parallel_canvas.plot_records(self.parallel_base, self.parallel_measure)
        self._update_parallel_result_ui()

    @staticmethod
    def _compute_parallel_result_from_records(parallel_base, parallel_measure):
        b, m = parallel_base['metrics'], parallel_measure['metrics']
        drx = m['rx'] - b['rx']
        dry = m['ry'] - b['ry']
        ref_x = (float(np.mean(parallel_base['x'])) + float(np.mean(parallel_measure['x']))) / 2.0
        ref_y = (float(np.mean(parallel_base['y'])) + float(np.mean(parallel_measure['y']))) / 2.0
        z_base = b['a'] * ref_x + b['b'] * ref_y + b['c']
        z_measure = m['a'] * ref_x + m['b'] * ref_y + m['c']
        step_height = z_measure - z_base
        estimated = bool(parallel_base.get('metric_quality', {}).get('estimated', False)
                         or parallel_measure.get('metric_quality', {}).get('estimated', False))
        return {
            'drx': float(drx),
            'dry': float(dry),
            'angle': float(np.hypot(drx, dry)),
            'step_height': float(step_height),
            'ref_x': float(ref_x),
            'ref_y': float(ref_y),
            'estimated': estimated,
        }

    def _compute_parallel_result(self):
        return self._compute_parallel_result_from_records(self.parallel_base, self.parallel_measure)

    def calculate_parallelism(self):
        if hasattr(self, 'adjustment_panel'):
            self.adjustment_panel.invalidate('正在更新平行度结果。')
        if self.parallel_base is None or self.parallel_measure is None:
            QMessageBox.warning(self, "数据不完整", "请先分别设置基准面和测量面。")
            return
        base, measure = self.parallel_base, self.parallel_measure
        revision = getattr(self, '_parallel_revision', 0)
        self.parallel_result = None
        self._update_parallel_result_ui()
        if hasattr(self, 'adjustment_panel'):
            self.adjustment_panel.invalidate('正在计算平行度。')
        if str(base.get('pipeline')) != str(measure.get('pipeline')):
            self._show_status("提示：基准面与测量面处理链不同；将按各自快照继续计算。", 8000)
        self._run_background_task(
            "平行度计算",
            lambda progress, cancel: self._parallel_task(base, measure, progress, cancel),
            lambda result: self._apply_parallel_result(result)
            if revision == getattr(self, '_parallel_revision', 0)
            and base is self.parallel_base and measure is self.parallel_measure else None,
        )

    @classmethod
    def _parallel_task(cls, base, measure, progress, cancel_event):
        progress(20, "正在计算两平面相对姿态")
        if cancel_event.is_set():
            from ..workers import TaskCancelled
            raise TaskCancelled()
        result = cls._compute_parallel_result_from_records(base, measure)
        progress(100, "平行度计算完成")
        return result

    def _apply_parallel_result(self, result):
        self.parallel_result = result
        if hasattr(self, 'adjustment_panel'):
            self.adjustment_panel.invalidate('平行度已计算，可计算装调量。')
        self._update_parallel_result_ui()
        self._show_status(
            f"平行度已计算: ΔRx={self.parallel_result['drx']:.2f} µrad, "
            f"ΔRy={self.parallel_result['dry']:.2f} µrad", 6000)

    def _fmt_metric(self, key, value):
        if key == 'mean_z':
            return f"{value:.5f}"
        if key in ('rx', 'ry'):
            return f"{value:.2f}"
        return f"{value:.3f}"

    def _update_parallel_result_ui(self):
        if not hasattr(self, 'parallel_result_labels'):
            return
        b = self.parallel_base['metrics'] if self.parallel_base else None
        m = self.parallel_measure['metrics'] if self.parallel_measure else None
        base_prefix = "≈" if self.parallel_base and self.parallel_base.get('metric_quality', {}).get('estimated') else ""
        measure_prefix = "≈" if self.parallel_measure and self.parallel_measure.get('metric_quality', {}).get('estimated') else ""
        result_prefix = "≈" if self.parallel_result and self.parallel_result.get('estimated') else ""
        for key, labels in self.parallel_result_labels.items():
            base_lab, meas_lab, delta_lab = labels
            base_lab.setText(base_prefix + self._fmt_metric(key, b[key]) if b else "--")
            meas_lab.setText(measure_prefix + self._fmt_metric(key, m[key]) if m else "--")
            if self.parallel_result and key == 'rx':
                delta_lab.setText(f"{result_prefix}{self.parallel_result['drx']:.2f}")
            elif self.parallel_result and key == 'ry':
                delta_lab.setText(f"{result_prefix}{self.parallel_result['dry']:.2f}")
            else:
                delta_lab.setText("--")

        if self.parallel_result:
            self.lbl_par_drx.setText(f"{result_prefix}{self.parallel_result['drx']:.2f}")
            self.lbl_par_dry.setText(f"{result_prefix}{self.parallel_result['dry']:.2f}")
            self.lbl_par_angle.setText(f"{result_prefix}{self.parallel_result['angle']:.2f}")
            self.lbl_par_step.setText(f"{result_prefix}{self.parallel_result['step_height']:.5f}")
            self.lbl_par_state.setText("抽样估计" if self.parallel_result.get('estimated') else "已计算")
        else:
            self.lbl_par_drx.setText("--")
            self.lbl_par_dry.setText("--")
            self.lbl_par_angle.setText("--")
            self.lbl_par_step.setText("--")
            self.lbl_par_state.setText("待计算")
        self._refresh_judgment('parallel')

        if b:
            self.lbl_par_eq_base.setText(f"基准面: Z = {b['a']:.6e}*X + {b['b']:.6e}*Y + {b['c']:.6e}")
        else:
            self.lbl_par_eq_base.setText("基准面: --")
        if m:
            self.lbl_par_eq_measure.setText(f"测量面: Z = {m['a']:.6e}*X + {m['b']:.6e}*Y + {m['c']:.6e}")
        else:
            self.lbl_par_eq_measure.setText("测量面: --")

    def _parallel_result_text(self):
        if self.parallel_base is None or self.parallel_measure is None or self.parallel_result is None:
            return ""
        rows = [
            "平行度分析结果",
            f"基准面: {self.parallel_base['name']} ({self.parallel_base['n']:,} 点)",
            f"测量面: {self.parallel_measure['name']} ({self.parallel_measure['n']:,} 点)",
            f"ΔRx = {self.parallel_result['drx']:.2f} µrad",
            f"ΔRy = {self.parallel_result['dry']:.2f} µrad",
            f"合成夹角 = {self.parallel_result['angle']:.2f} µrad",
            f"台阶高度差 = {self.parallel_result['step_height']:.5f} mm "
            f"(参考点 X={self.parallel_result['ref_x']:.5f}, Y={self.parallel_result['ref_y']:.5f})",
        ]
        if self.parallel_result.get('estimated'):
            rows.insert(1, "结果质量: 抽样估计，不可直接用于产线放行")
        judgment = judgment_summary(self.current_judgment('parallel'))
        if judgment:
            rows.insert(1, judgment)
        base_trace = self._parallel_trace(self.parallel_base)
        measure_trace = self._parallel_trace(self.parallel_measure)
        rows.extend([
            f"基准处理: {base_trace['pipeline']}",
            f"测量处理: {measure_trace['pipeline']}",
            f"基准滤波: {base_trace['filter']}",
            f"测量滤波: {measure_trace['filter']}",
            f"基准导入: {base_trace['import_strategy']} | 抽样 {base_trace['sampled']} | {base_trace['quality']}",
            f"测量导入: {measure_trace['import_strategy']} | 抽样 {measure_trace['sampled']} | {measure_trace['quality']}",
        ])
        for label, rec in (("基准面", self.parallel_base), ("测量面", self.parallel_measure)):
            mm = rec['metrics']
            rows.append(
                f"{label}: Rx={mm['rx']:.2f} µrad, Ry={mm['ry']:.2f} µrad, "
                f"RMS={mm['rms']:.3f} µm, PV={mm['pv']:.3f} µm, "
                f"TTV={mm['ttv']:.3f} µm, 平均Z={mm['mean_z']:.5f} mm")
        return "\n".join(rows)

    def copy_parallel_result(self):
        text = self._parallel_result_text()
        if not text:
            QMessageBox.warning(self, "暂无结果", "请先计算平行度。")
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("平行度结果已复制到剪贴板", 4000)

    def export_parallel_csv(self):
        if self.parallel_base is None or self.parallel_measure is None or self.parallel_result is None:
            QMessageBox.warning(self, "暂无结果", "请先计算平行度。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出平行度CSV", dialog_initial_path('export', "Parallelism_Result.csv"),
            "CSV (*.csv)")
        if not path:
            return
        remember_dialog_path('export', path)
        try:
            b, m = self.parallel_base['metrics'], self.parallel_measure['metrics']
            rows = [
                {'metric': 'Rx_urad', 'base': b['rx'], 'measure': m['rx'], 'delta': self.parallel_result['drx']},
                {'metric': 'Ry_urad', 'base': b['ry'], 'measure': m['ry'], 'delta': self.parallel_result['dry']},
                {'metric': 'Angle_urad', 'base': '', 'measure': '', 'delta': self.parallel_result['angle']},
                {'metric': 'Step_Height_mm', 'base': '', 'measure': '', 'delta': self.parallel_result['step_height']},
                {'metric': 'Step_Reference_X_mm', 'base': '', 'measure': '', 'delta': self.parallel_result['ref_x']},
                {'metric': 'Step_Reference_Y_mm', 'base': '', 'measure': '', 'delta': self.parallel_result['ref_y']},
                {'metric': 'RMS_um', 'base': b['rms'], 'measure': m['rms'], 'delta': ''},
                {'metric': 'PV_um', 'base': b['pv'], 'measure': m['pv'], 'delta': ''},
                {'metric': 'TTV_um', 'base': b['ttv'], 'measure': m['ttv'], 'delta': ''},
                {'metric': 'Mean_Z_mm', 'base': b['mean_z'], 'measure': m['mean_z'], 'delta': ''},
            ]
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                base_trace = self._parallel_trace(self.parallel_base)
                measure_trace = self._parallel_trace(self.parallel_measure)
                f.write(f"# ===== 平行度分析 {self.APP_VERSION} 导出 =====\n")
                f.write(f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                f.write(f"# 基准面: {self.parallel_base['name']} | 点数: {self.parallel_base['n']}\n")
                f.write(f"# 测量面: {self.parallel_measure['name']} | 点数: {self.parallel_measure['n']}\n")
                f.write(f"# 基准面处理: {base_trace['pipeline']}\n")
                f.write(f"# 测量面处理: {measure_trace['pipeline']}\n")
                f.write(f"# 基准面滤波: {base_trace['filter']}\n")
                f.write(f"# 测量面滤波: {measure_trace['filter']}\n")
                f.write(f"# 基准面导入策略: {base_trace['import_strategy']}\n")
                f.write(f"# 测量面导入策略: {measure_trace['import_strategy']}\n")
                f.write(f"# 基准面抽样状态: {base_trace['sampled']}\n")
                f.write(f"# 测量面抽样状态: {measure_trace['sampled']}\n")
                f.write(f"# 基准面参与拟合点数: {base_trace['n']}\n")
                f.write(f"# 测量面参与拟合点数: {measure_trace['n']}\n")
                f.write(f"# 基准面质量: {base_trace['quality']}\n")
                f.write(f"# 测量面质量: {measure_trace['quality']}\n")
                f.write(f"# 结果质量: {'抽样估计，不可直接用于产线放行' if self.parallel_result.get('estimated') else '全量计算'}\n")
                f.write("# 口径: 不做对应点相减；分别拟合平面后计算 测量面 - 基准面 的 Rx/Ry 差值。\n")
                f.write("# 台阶高度差: 在两面质心中点处分别代入拟合平面求Z后相减。\n")
                pd.DataFrame(rows).to_csv(f, index=False)
            self.statusBar().showMessage(f"平行度CSV已导出: {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    @staticmethod
    def _short_report_text(text, max_chars=54):
        s = str(text or "--").replace("\n", " ")
        return s if len(s) <= max_chars else s[:max_chars - 3] + "..."

    def _parallel_report_default_name(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(str(self.parallel_base.get('name') or 'base')).stem or 'base'
        meas = Path(str(self.parallel_measure.get('name') or 'measure')).stem or 'measure'
        name = f"Parallelism_Report_{base}_vs_{meas}_{stamp}.png"
        name = re.sub(r'[<>:"/\\|?*\r\n]+', '_', name)
        return name if len(name) <= 160 else f"Parallelism_Report_{stamp}.png"

    def _draw_parallel_report_surface(self, fig, ax, rec, title, plane_color):
        x = np.asarray(rec['x'])
        y = np.asarray(rec['y'])
        z = np.asarray(rec['z'])
        m = rec['metrics']
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        x, y, z = x[finite], y[finite], z[finite]
        if len(z) == 0:
            ax.set_title(title)
            ax.text2D(0.5, 0.5, "无有效点", transform=ax.transAxes,
                      ha='center', va='center', color='#8a94a3')
            ax.set_axis_off()
            return

        max_points = min(int(self._display_limit()), 60000)
        if len(z) > max_points:
            pick = np.linspace(0, len(z) - 1, max_points, dtype=int)
            sx, sy, sz = x[pick], y[pick], z[pick]
            sample_note = f"绘图抽样 {len(sz):,}/{len(z):,} 点"
        else:
            sx, sy, sz = x, y, z
            sample_note = f"绘图点数 {len(z):,}"

        xmin, xmax = float(np.min(x)), float(np.max(x))
        ymin, ymax = float(np.min(y)), float(np.max(y))
        if np.isclose(xmin, xmax):
            xmin -= 0.5; xmax += 0.5
        if np.isclose(ymin, ymax):
            ymin -= 0.5; ymax += 0.5
        xx, yy = np.meshgrid(np.linspace(xmin, xmax, 24), np.linspace(ymin, ymax, 24))
        zz = m['a'] * xx + m['b'] * yy + m['c']

        ax.plot_surface(xx, yy, zz, color=plane_color, alpha=0.28,
                        edgecolor='none', shade=False)
        point_size = 8 if len(sz) <= 20000 else 5
        sc = ax.scatter(sx, sy, sz, c=sz, cmap='turbo', s=point_size,
                        alpha=0.82, edgecolors='none', depthshade=False,
                        rasterized=True)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_zlabel("")
        ax.set_zticks([])
        ax.tick_params(labelsize=8, colors='#94a3b8')
        ax.view_init(elev=24, azim=-52)
        ax.grid(True, linestyle=':', linewidth=0.7, color='#dce3ea')
        set_surface_box_aspect(ax, x, y, z, zoom=0.90)
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_facecolor('#fbfcfd')
            pane.set_edgecolor('#e6eaee')
            pane.set_alpha(1.0)

        ax.text2D(
            0.02, 0.98,
            f"{sample_note}\nRx {m['rx']:.2f} µrad | Ry {m['ry']:.2f} µrad\n"
            f"RMS {m['rms']:.3f} µm | PV {m['pv']:.3f} µm",
            transform=ax.transAxes, ha='left', va='top', fontsize=9,
            color='#334155',
            bbox=dict(boxstyle='round,pad=0.35', fc='white', ec='#dbe3ec', alpha=0.9)
        )
        cbar = fig.colorbar(sc, ax=ax, shrink=0.68, aspect=24, pad=0.10, panchor=False)
        cbar.set_label("Z (mm)", fontsize=9)
        cbar.ax.tick_params(labelsize=8)

    def _render_parallel_report_figure(self):
        """V5.0.8: reserved regions for sources, metrics, surfaces and notes."""
        import textwrap
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        if self.parallel_result is None:
            self.parallel_result = self._compute_parallel_result()
        r = self.parallel_result
        base, measure = self.parallel_base, self.parallel_measure
        b, m = base['metrics'], measure['metrics']
        traces = [self._parallel_trace(rec) for rec in (base, measure)]
        source_lines = []
        for title, rec, trace in zip(('基准面','测量面'), (base,measure), traces):
            source_lines.extend(textwrap.wrap(f"{title}：{rec['name']}", 35))
            source_lines.extend(textwrap.wrap(f"处理：{trace['pipeline']} / {trace['filter']}",35))
            source_lines.append(f"有效点：{rec['n']:,}；{trace['quality']}")
        source_lines.append('导入：' + ' / '.join(t['import_strategy'] for t in traces))
        # Source height grows with actual wrapped text, never overlaps the table.
        height = max(10.5, 7.8 + .19 * len(source_lines))
        fig = Figure(figsize=(18,height), facecolor='white')
        FigureCanvasAgg(fig)
        fig.text(.035,.958,f'平行度分析报告 ({self.APP_VERSION})',fontsize=18,fontweight='bold',color='#243b50')
        prefix = '≈' if r.get('estimated') else ''
        values = [('ΔRx',f"{prefix}{r['drx']:+.3f} µrad"),('ΔRy',f"{prefix}{r['dry']:+.3f} µrad"),
                  ('合成夹角',f"{prefix}{r['angle']:.3f} µrad"),('台阶高度差',f"{prefix}{r['step_height']:+.6f} mm")]
        for i,(title,value) in enumerate(values):
            ax=fig.add_axes([.035+i*.2325,.853,.224,.075]);ax.set_facecolor('#f1f5f9');ax.set_xticks([]);ax.set_yticks([])
            for spine in ax.spines.values(): spine.set_visible(False)
            ax.text(.05,.74,title,fontsize=9,color='#64748b',transform=ax.transAxes)
            ax.text(.05,.21,value,fontsize=14,color='#365f85',transform=ax.transAxes)
        fig.text(.035,.80,'来源与处理信息',fontsize=11,fontweight='bold',color='#243b50')
        fig.text(.035,.77,'\n'.join(source_lines),va='top',fontsize=9,linespacing=1.45,color='#475569')
        ax=fig.add_axes([.035,.185,.26,.29]);ax.axis('off')
        ax.text(0,1.07,'单面拟合指标',fontsize=11,fontweight='bold',color='#243b50')
        rows=[]
        for title,key,digits in [('Rx (µrad)','rx',3),('Ry (µrad)','ry',3),('RMS (µm)','rms',4),('法向 PV (µm)','pv',4),('TTV (µm)','ttv',3),('平均 Z (mm)','mean_z',6)]:
            rows.append([title, ('≈' if base.get('metric_quality',{}).get('estimated') else '')+f'{b[key]:.{digits}f}',
                         ('≈' if measure.get('metric_quality',{}).get('estimated') else '')+f'{m[key]:.{digits}f}'])
        table=ax.table(cellText=rows,colLabels=['指标','基准面','测量面'],bbox=[0,0,1,.96],cellLoc='center',colWidths=[.4,.3,.3])
        table.auto_set_font_size(False);table.set_fontsize(9)
        for (row,col),cell in table.get_celld().items():
            cell.set_edgecolor('#d7dee8');cell.set_facecolor('#edf3f9' if row==0 else 'white')
        for rec,title,color,pos in [(base,'基准面 3D 拟合预览','#2f6db0',[.33,.29,.26,.44]),
                                    (measure,'测量面 3D 拟合预览','#f59e0b',[.66,.29,.26,.44])]:
            axis=fig.add_axes(pos,projection='3d')
            self._draw_parallel_report_surface(fig,axis,rec,title,color)
            axis.set_position(pos);fig.axes[-1].set_position([pos[0]+pos[2]+.012,.33,.009,.34])
        note = ('分别拟合两面，再计算测量面 − 基准面的 Rx/Ry 差值；不要求逐点对应。\n'
                f"台阶高度差参考点：两面质心中点（X={r['ref_x']:.5f}, Y={r['ref_y']:.5f} mm）。Rx/Ry 符号约定需用标准件校准。")
        if r.get('estimated'):
            note = '警告：含抽样估计值，不可直接用于产线放行。\n' + note
        fig.text(.035,.115,'口径说明',fontsize=11,fontweight='bold',color='#243b50')
        fig.text(.035,.095,note,va='top',fontsize=9,linespacing=1.5,color='#b42318' if r.get('estimated') else '#64748b')
        fig.text(.66,.80,judgment_summary(self.current_judgment('parallel')),fontsize=10,color='#475569')
        return fig

    def export_parallel_report(self):
        if self.parallel_base is None or self.parallel_measure is None:
            QMessageBox.warning(self, "暂无数据", "请先设置基准面和测量面。")
            return
        if self.parallel_result is None:
            self.parallel_result = self._compute_parallel_result()
            self._update_parallel_result_ui()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出平行度报告图",
            dialog_initial_path('export', self._parallel_report_default_name()),
            "PNG 图片 (*.png);;All Files (*)")
        if not path:
            return
        remember_dialog_path('export', path)
        try:
            fig = self._render_parallel_report_figure()
            fig.savefig(path, dpi=150)
            self.statusBar().showMessage(f"平行度报告图已导出: {path}", 6000)
            QMessageBox.information(self, "导出成功", f"平行度报告图已导出：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_parallel_preview(self):
        self.export_parallel_report()
