"""ParallelismMixin extracted from the V3.9.3 application."""

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

from ..plotting import set_surface_box_aspect



class ParallelismMixin:
    def _current_parallel_record(self):
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "暂无数据", "请先在主页面导入并处理数据。")
            return None
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        if len(fz) < 3:
            QMessageBox.warning(self, "点数不足", "参与拟合点少于 3 个，无法写入平行度分析。")
            return None
        metrics = self.compute_plane_metrics(fx, fy, fz)
        quality = self._current_metric_quality()
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
        if hasattr(self, 'lbl_parallel_base_status'):
            self.lbl_parallel_base_status.setText(self._slot_status_text(self.parallel_base))
            self.lbl_parallel_measure_status.setText(self._slot_status_text(self.parallel_measure))
        if hasattr(self, 'parallel_canvas'):
            self.parallel_canvas.plot_records(self.parallel_base, self.parallel_measure)
        self._update_parallel_result_ui()

    def _compute_parallel_result(self):
        b, m = self.parallel_base['metrics'], self.parallel_measure['metrics']
        drx = m['rx'] - b['rx']
        dry = m['ry'] - b['ry']
        ref_x = (float(np.mean(self.parallel_base['x'])) + float(np.mean(self.parallel_measure['x']))) / 2.0
        ref_y = (float(np.mean(self.parallel_base['y'])) + float(np.mean(self.parallel_measure['y']))) / 2.0
        z_base = b['a'] * ref_x + b['b'] * ref_y + b['c']
        z_measure = m['a'] * ref_x + m['b'] * ref_y + m['c']
        step_height = z_measure - z_base
        estimated = bool(self.parallel_base.get('metric_quality', {}).get('estimated', False)
                         or self.parallel_measure.get('metric_quality', {}).get('estimated', False))
        return {
            'drx': float(drx),
            'dry': float(dry),
            'angle': float(np.hypot(drx, dry)),
            'step_height': float(step_height),
            'ref_x': float(ref_x),
            'ref_y': float(ref_y),
            'estimated': estimated,
        }

    def calculate_parallelism(self):
        if self.parallel_base is None or self.parallel_measure is None:
            QMessageBox.warning(self, "数据不完整", "请先分别设置基准面和测量面。")
            return
        self.parallel_result = self._compute_parallel_result()
        self._update_parallel_result_ui()
        self.statusBar().showMessage(
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
        path, _ = QFileDialog.getSaveFileName(self, "导出平行度CSV", "Parallelism_Result.csv", "CSV (*.csv)")
        if not path:
            return
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
                f.write(f"# ===== 平行度分析 {self.APP_VERSION} 导出 =====\n")
                f.write(f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                f.write(f"# 基准面: {self.parallel_base['name']} | 点数: {self.parallel_base['n']}\n")
                f.write(f"# 测量面: {self.parallel_measure['name']} | 点数: {self.parallel_measure['n']}\n")
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
        set_surface_box_aspect(ax, x, y, z, zoom=1.06)
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
        cbar = fig.colorbar(sc, ax=ax, shrink=0.68, aspect=24, pad=0.10)
        cbar.set_label("Z (mm)", fontsize=9)
        cbar.ax.tick_params(labelsize=8)

    def _render_parallel_report_figure(self):
        """生成平行度分析报告图，返回 Figure(Agg 后端)。"""
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        if self.parallel_result is None:
            self.parallel_result = self._compute_parallel_result()

        b_rec, m_rec = self.parallel_base, self.parallel_measure
        b, m = b_rec['metrics'], m_rec['metrics']
        r = self.parallel_result

        fig = Figure(figsize=(17.6, 10.0), constrained_layout=False)
        FigureCanvasAgg(fig)
        gs = fig.add_gridspec(
            1, 3, width_ratios=[1.08, 1.42, 1.42],
            left=0.035, right=0.985, top=0.90, bottom=0.06, wspace=0.16)
        gs_left = gs[0, 0].subgridspec(4, 1, height_ratios=[1.65, 1.45, 3.15, 1.35], hspace=0.32)
        ax_meta = fig.add_subplot(gs_left[0, 0]); ax_meta.axis('off')
        ax_result = fig.add_subplot(gs_left[1, 0]); ax_result.axis('off')
        ax_table = fig.add_subplot(gs_left[2, 0]); ax_table.axis('off')
        ax_note = fig.add_subplot(gs_left[3, 0]); ax_note.axis('off')
        ax_base = fig.add_subplot(gs[0, 1], projection='3d')
        ax_meas = fig.add_subplot(gs[0, 2], projection='3d')

        ax_meta.text(0.02, 0.98, "报告信息", va='top', ha='left',
                     fontsize=12.2, fontweight='bold', color='#1f2937',
                     transform=ax_meta.transAxes)
        meta_lines = [
            f"时间  {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"基准  {self._short_report_text(b_rec['name'], 42)}",
            f"测量  {self._short_report_text(m_rec['name'], 42)}",
            f"点数  基准 {b_rec['n']:,} / 测量 {m_rec['n']:,}",
            f"导入  基准 {b_rec.get('import_strategy', '--')} / 测量 {m_rec.get('import_strategy', '--')}",
            f"抽样  基准 {b_rec.get('sampled', False)} / 测量 {m_rec.get('sampled', False)}",
            f"质量  基准 {b_rec.get('metric_quality', {}).get('label', '全量计算')} / 测量 {m_rec.get('metric_quality', {}).get('label', '全量计算')}",
            f"处理  {self._short_report_text(b_rec.get('pipeline'), 38)}",
        ]
        ax_meta.text(0.02, 0.80, "\n".join(meta_lines), va='top', ha='left',
                     fontsize=9.7, linespacing=1.45, color='#475569',
                     transform=ax_meta.transAxes,
                     bbox=dict(boxstyle='round,pad=0.45', facecolor='#f8fafc',
                               edgecolor='#dbe3ec', linewidth=1.0))

        ax_result.add_patch(FancyBboxPatch(
            (0.0, 0.03), 0.98, 0.90,
            boxstyle='round,pad=0.018,rounding_size=0.035',
            transform=ax_result.transAxes,
            facecolor='#eaf2fb', edgecolor='#2f6db0', linewidth=1.35,
            zorder=0, clip_on=False))
        ax_result.text(0.04, 0.84, "平行度结果", va='center', ha='left',
                       fontsize=11.8, fontweight='bold', color='#11447a',
                       transform=ax_result.transAxes, zorder=2)
        result_prefix = "≈" if r.get('estimated') else ""
        result_rows = [
            ("ΔRx", f"{result_prefix}{r['drx']:.2f}", "µrad"),
            ("ΔRy", f"{result_prefix}{r['dry']:.2f}", "µrad"),
            ("合成夹角", f"{result_prefix}{r['angle']:.2f}", "µrad"),
            ("台阶高度差", f"{result_prefix}{r['step_height']:.5f}", "mm"),
        ]
        y0 = 0.66
        for i, (name, value, unit) in enumerate(result_rows):
            y = y0 - i * 0.17
            ax_result.text(0.04, y, name, va='center', ha='left',
                           fontsize=10.2, color='#64748b', transform=ax_result.transAxes, zorder=2)
            ax_result.text(0.72, y, value, va='center', ha='right',
                           fontsize=17.0, fontweight='bold', color='#11447a',
                           transform=ax_result.transAxes, zorder=2)
            ax_result.text(0.76, y, unit, va='center', ha='left',
                           fontsize=10.2, color='#64748b', transform=ax_result.transAxes, zorder=2)

        base_prefix = "≈" if b_rec.get('metric_quality', {}).get('estimated') else ""
        measure_prefix = "≈" if m_rec.get('metric_quality', {}).get('estimated') else ""
        table_rows = [
            ["Rx (µrad)", f"{base_prefix}{b['rx']:.2f}", f"{measure_prefix}{m['rx']:.2f}", f"{result_prefix}{r['drx']:.2f}"],
            ["Ry (µrad)", f"{base_prefix}{b['ry']:.2f}", f"{measure_prefix}{m['ry']:.2f}", f"{result_prefix}{r['dry']:.2f}"],
            ["RMS (µm)", f"{base_prefix}{b['rms']:.3f}", f"{measure_prefix}{m['rms']:.3f}", "--"],
            ["PV 法向 (µm)", f"{base_prefix}{b['pv']:.3f}", f"{measure_prefix}{m['pv']:.3f}", "--"],
            ["TTV Z极差 (µm)", f"{base_prefix}{b['ttv']:.3f}", f"{measure_prefix}{m['ttv']:.3f}", "--"],
            ["平均 Z (mm)", f"{base_prefix}{b['mean_z']:.5f}", f"{measure_prefix}{m['mean_z']:.5f}", "--"],
        ]
        ax_table.text(0.02, 0.99, "单面拟合指标", va='top', ha='left',
                      fontsize=12.2, fontweight='bold', color='#1f2937',
                      transform=ax_table.transAxes)
        tbl = ax_table.table(
            cellText=table_rows,
            colLabels=["指标", "基准面", "测量面", "差值"],
            bbox=[0.0, 0.02, 1.0, 0.84],
            cellLoc='center',
            colLoc='center',
            colWidths=[0.32, 0.23, 0.23, 0.22],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9.6)
        tbl.scale(1, 1.26)
        for (row, col), cell in tbl.get_celld().items():
            cell.set_edgecolor('#d7dee8')
            cell.set_linewidth(0.8)
            if row == 0:
                cell.set_facecolor('#edf3f9')
                cell.set_text_props(weight='bold', color='#334155')
            elif col == 3 and row in (1, 2):
                cell.set_facecolor('#eaf2fb')
                cell.set_text_props(weight='bold', color='#11447a')
            else:
                cell.set_facecolor('#ffffff')

        ax_note.text(0.02, 0.98, "口径说明", va='top', ha='left',
                     fontsize=11.3, fontweight='bold', color='#1f2937',
                     transform=ax_note.transAxes)
        note = (
            "不做对应点相减；两个文件可为空间不重叠区域。\n"
            "分别拟合 Z = aX + bY + c，再计算测量面 - 基准面的 Rx/Ry 差值。\n"
            f"台阶高度差在两面质心中点处计算，参考点 X={r['ref_x']:.5f}, Y={r['ref_y']:.5f}。\n"
            "Rx/Ry 符号约定需用标准件校准。"
        )
        if r.get('estimated'):
            note = "警告: 本报告含抽样估计值，不可直接用于产线放行。\n" + note
        ax_note.text(0.02, 0.76, note, va='top', ha='left',
                     fontsize=9.3, linespacing=1.45, color='#b42318' if r.get('estimated') else '#6b7280',
                     transform=ax_note.transAxes,
                     bbox=dict(boxstyle='round,pad=0.45', facecolor='#f8fafc',
                               edgecolor='#e2e8f0', linewidth=1.0))

        self._draw_parallel_report_surface(fig, ax_base, b_rec, "基准面 3D 拟合预览", '#2f6db0')
        self._draw_parallel_report_surface(fig, ax_meas, m_rec, "测量面 3D 拟合预览", '#f59e0b')
        fig.suptitle(f"平行度分析报告 ({self.APP_VERSION})", fontsize=17, fontweight='bold', y=0.965)
        return fig

    def export_parallel_report(self):
        if self.parallel_base is None or self.parallel_measure is None:
            QMessageBox.warning(self, "暂无数据", "请先设置基准面和测量面。")
            return
        if self.parallel_result is None:
            self.parallel_result = self._compute_parallel_result()
            self._update_parallel_result_ui()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出平行度报告图", self._parallel_report_default_name(),
            "PNG 图片 (*.png);;All Files (*)")
        if not path:
            return
        try:
            fig = self._render_parallel_report_figure()
            fig.savefig(path, dpi=150)
            self.statusBar().showMessage(f"平行度报告图已导出: {path}", 6000)
            QMessageBox.information(self, "导出成功", f"平行度报告图已导出：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_parallel_preview(self):
        self.export_parallel_report()
