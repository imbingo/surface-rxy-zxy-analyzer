"""ReportingMixin extracted from the V3.9.3 application."""

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



class ReportingMixin:
    def export_report_image(self):
        """导出当前测量的报告图（与批量处理同款：主页面全部信息 + 四视图）。
        默认命名 Result_<导入文件名>_<时间>.png。"""
        if self.df_raw is None or self.active_idx is None or self.last_metrics is None:
            QMessageBox.warning(self, "暂无数据", "请先载入并解析数据，再导出测量报告图。")
            return
        if not self._confirm_estimated_metrics('导出测量报告图'):
            return
        src = self.current_source_name if self.current_source_name not in (None, '', '--') else 'report'
        stem = Path(src).stem or 'report'
        default_name = f"Result_{stem}_{datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, "导出测量报告图", default_name, "PNG 图片 (*.png);;All Files (*)")
        if not path:
            return
        try:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
            metrics = self.compute_plane_metrics(fx, fy, fz)
            pipeline_text = " -> ".join(self.transform_pipeline) if self.transform_pipeline else "原始状态"
            filter_text = self.cb_filter.currentText()
            if self.cb_filter.currentIndex() == 2:
                filter_text += f" (k={self.spin_k.value()}, 阈值={self.spin_thresh.value()}µm)"
            elif self.cb_filter.currentIndex() == 3:
                filter_text += f" (σ={self.spin_sigma.value()}, 迭代上限={self.spin_sigma_iter.value()})"
            fig = self._render_report_figure(
                self.current_source_name, tx, ty, tz, self.active_idx, metrics,
                self.n_filtered, pipeline_text, filter_text, self.import_info, self.display_detrended,
                roi_info=self._roi_report_info(tx, ty, tz, matrix_rc=self._matrix_rc_for_current_data()))
            fig.savefig(path, dpi=150)
            self.statusBar().showMessage(f"已导出报告图: {path}", 6000)
            QMessageBox.information(self, "导出成功", f"测量报告图已导出：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def save_file(self):
        if self.df_raw is None or self.active_idx is None: return
        if not self._confirm_estimated_metrics('导出CSV'):
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出", "Result_Data.csv", "CSV (*.csv)")
        if not path: return
        try:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]

            df_out = pd.DataFrame({
                'X_mm': fx,
                'Y_mm': fy,
                'Z_um': fz * 1000.0,   # 内部 mm -> 导出 µm 必须乘 1000
            })
            if self.current_coeffs is not None:
                c = self.current_coeffs
                df_out['Resid_um'] = (fz - (c[0] * fx + c[1] * fy + c[2])) * 1000.0

            pipeline_text = " -> ".join(self.transform_pipeline) if self.transform_pipeline else "原始状态"
            filter_text = self.cb_filter.currentText()
            if self.cb_filter.currentIndex() == 2:
                filter_text += f" (k={self.spin_k.value()}, 局部阈值={self.spin_thresh.value()}µm, 全局兜底阈值={self.spin_thresh.value()}µm)"
            elif self.cb_filter.currentIndex() == 3:
                filter_text += f" (σ={self.spin_sigma.value()}, 迭代上限={self.spin_sigma_iter.value()})"
            roi_info = self._roi_report_info(tx, ty, tz, matrix_rc=self._matrix_rc_for_current_data())
            quality = self._current_metric_quality()
            meta = [
                f"# ===== 面型及Rxy分析工具 {self.APP_VERSION} 导出 =====",
                f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"# 数据来源: {self.current_source_name}",
                f"# 变换路径: {pipeline_text}",
                f"# 滤波模式: {filter_text}",
                f"# ROI: {roi_info['summary']}",
                f"# 滤波剔除点数: {self.n_filtered} | 手动删除点数: {int((~self.manual_mask).sum())} | 导出点数: {len(fz)}",
                f"# 当前显示模式: {'去倾斜残差显示(仅显示/框选)' if self.display_detrended else '原始Z高度显示'}",
                f"# 导入方式: {self.import_info.get('strategy', '--')} | 是否抽样: {self.import_info.get('sampled', False)}",
                f"# 结果质量: {quality['label']}",
                f"# 源文件大小: {self.import_info.get('file_size_mb', 0.0):.1f} MB | 读入行数: {self.import_info.get('import_rows', 0)} | 有效点数: {self.import_info.get('valid_rows', len(self.df_raw) if self.df_raw is not None else 0)}",
                f"# 显示上限: {self._display_limit()} 点 | 最近一次绘图显示: {self.last_displayed_points} 点",
            ]
            if quality['warning']:
                meta.append(f"# 警告: {quality['warning']}")
            if self.last_metrics is not None:
                m = self.last_metrics
                meta += [
                    f"# 拟合平面: Z = {m['a']:.6e}*X + {m['b']:.6e}*Y + {m['c']:.6e}  (单位 mm)",
                    f"# Rx = {m['rx']:.2f} µrad | Ry = {m['ry']:.2f} µrad (符号约定需标准件校准)",
                    f"# PV(BF平面法向) = {m['pv']:.3f} µm | TTV(原始Z极差) = {m['ttv']:.3f} µm | 平均Z = {m['mean_z']:.5f} mm",
                ]
            if roi_info['shape_lines']:
                meta.append("# ROI形状: " + "；".join(roi_info['shape_lines'][:8]))
                if len(roi_info['shape_lines']) > 8:
                    meta.append(f"# ROI形状: 另有 {len(roi_info['shape_lines']) - 8} 个未列出")
            meta.append("# 提示: 用 pandas.read_csv(file, comment='#') 可自动跳过本说明头")

            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write("\n".join(meta) + "\n")
                df_out.to_csv(f, index=False)
            self.statusBar().showMessage(f"已导出 {len(fz)} 点到 {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _capture_batch_params(self):
        """快照当前界面用于批量处理的参数（与具体文件无关）。
        导入Recipe会先把参数写入界面，所以这里读到的就是Recipe或手动调好的设置。"""
        unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
        mode = self.cb_filter.currentIndex()
        filter_text = self.cb_filter.currentText()
        if mode == 2:
            filter_text += f" (k={self.spin_k.value()}, 阈值={self.spin_thresh.value()}µm)"
        elif mode == 3:
            filter_text += f" (σ={self.spin_sigma.value()}, 迭代上限={self.spin_sigma_iter.value()})"
        pipeline = list(self.transform_pipeline)
        return {
            'x_col': self.cb_x_col.currentText(),
            'y_col': self.cb_y_col.currentText(),
            'z_col': self.cb_z_col.currentText(),
            'x_unit': self.cb_x_unit.currentText(),
            'y_unit': self.cb_y_unit.currentText(),
            'z_unit': self.cb_z_unit.currentText(),
            'ux': unit_m[self.cb_x_unit.currentText()],
            'uy': unit_m[self.cb_y_unit.currentText()],
            'uz': unit_m[self.cb_z_unit.currentText()],
            'pipeline': pipeline,
            'pipeline_text': " -> ".join(pipeline) if pipeline else "原始状态",
            'mode': mode,
            'k': self.spin_k.value(),
            'threshold_mm': self.spin_thresh.value() * 1e-3,
            'sigma_k': self.spin_sigma.value(),
            'sigma_iters': self.spin_sigma_iter.value(),
            'filter_text': filter_text,
            'display_detrended': self.display_detrended,
            'roi_enabled': bool(self.roi_enabled),
            'roi_shapes': [dict(r) for r in self.roi_shapes],
        }

    def batch_process(self):
        """多选文件批量处理：沿用当前界面(或已导入Recipe)的设置，逐个出报告图+汇总表。"""
        if self.cb_x_col.count() == 0:
            QMessageBox.warning(
                self, "请先配置参数",
                "批量处理会沿用当前界面的列映射/单位/旋转/滤波设置。\n"
                "请先载入其中一个文件、调好参数(或导入Recipe)，再点批量处理。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "批量选择测量数据 (可多选)", "",
            "Data (*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm);;All Files (*)")
        if not files:
            return
        outdir = QFileDialog.getExistingDirectory(self, "选择结果输出文件夹", str(Path(files[0]).parent))
        if not outdir:
            return
        p = self._capture_batch_params()
        confirm = (
            f"将批量处理 {len(files)} 个文件，沿用当前设置：\n\n"
            f"列映射: X={p['x_col']}({p['x_unit']})  Y={p['y_col']}({p['y_unit']})  Z={p['z_col']}({p['z_unit']})\n"
            f"变换路径: {p['pipeline_text']}\n"
            f"滤波模式: {p['filter_text']}\n\n"
            f"ROI: {self._roi_report_info(roi_enabled=p['roi_enabled'], roi_shapes=p['roi_shapes'])['summary']}\n\n"
            f"每个文件输出: result_<原文件名>.png（含主页面指标+四视图）\n"
            f"另生成: result_batch_summary.csv（指标汇总表）\n"
            f"输出目录: {outdir}\n\n"
            f"注意: 批量仅用自动滤波和当前 ROI，不含手动框选删点；\n请确认所有文件为同一设备、同样列格式。")
        if QMessageBox.question(
                self, "确认批量处理", confirm,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return

        results = self._run_batch(files, outdir, p)
        ok = [r for r in results if r['status'] == 'ok']
        fail = [r for r in results if r['status'] != 'ok']
        msg = (f"批量处理完成：成功 {len(ok)} / 失败 {len(fail)}\n"
               f"输出目录：{outdir}\n"
               f"报告图：result_<原文件名>.png\n"
               f"汇总表：result_batch_summary.csv" + ("（本次无成功项，未生成）" if not ok else ""))
        if fail:
            preview = "\n".join(f"  ✗ {r['file']}: {r['error']}" for r in fail[:8])
            if len(fail) > 8:
                preview += f"\n  …其余 {len(fail) - 8} 个失败未列出"
            msg += "\n\n失败清单：\n" + preview
        self.statusBar().showMessage(
            f"批量完成：成功 {len(ok)} / 失败 {len(fail)}，输出至 {outdir}", 10000)
        (QMessageBox.warning if fail else QMessageBox.information)(self, "批量处理结果", msg)

    def _run_batch(self, files, outdir, params):
        """逐文件执行 读入→映射→变换→滤波→拟合→出报告图，并写汇总CSV；返回结果列表。
        批量期间会临时改动 import_info，结束后恢复，避免污染主界面当前视图状态。"""
        saved_info = dict(self.import_info)
        saved_note = self.last_import_note
        results, summary_rows = [], []
        out = Path(outdir)
        try:
            for path in files:
                name = Path(path).name
                try:
                    df_raw = self._read_table(path)
                    import_info_snap = dict(self.import_info)
                    for col in (params['x_col'], params['y_col'], params['z_col']):
                        if col not in df_raw.columns:
                            raise ValueError(f"列 '{col}' 不在文件列 {list(df_raw.columns)[:6]} 中（列格式不一致？）")
                    d = pd.DataFrame({
                        'X': pd.to_numeric(df_raw[params['x_col']], errors='coerce'),
                        'Y': pd.to_numeric(df_raw[params['y_col']], errors='coerce'),
                        'Z': pd.to_numeric(df_raw[params['z_col']], errors='coerce'),
                    })
                    if '_matrix_row' in df_raw.columns and '_matrix_col' in df_raw.columns:
                        d['_matrix_row'] = pd.to_numeric(df_raw['_matrix_row'], errors='coerce')
                        d['_matrix_col'] = pd.to_numeric(df_raw['_matrix_col'], errors='coerce')
                    d = d.dropna(subset=['X', 'Y', 'Z'])
                    if len(d) < 3:
                        raise ValueError("有效数据点少于 3 个")
                    x = d['X'].values * params['ux']
                    y = d['Y'].values * params['uy']
                    z = d['Z'].values * params['uz']
                    x, y, z = self._apply_transform_pipeline(x, y, z, params['pipeline'])
                    matrix_rc = None
                    if '_matrix_row' in d.columns and '_matrix_col' in d.columns:
                        matrix_rc = (d['_matrix_row'].to_numpy(dtype=int), d['_matrix_col'].to_numpy(dtype=int))
                    n_total = len(z)
                    if self._roi_is_active(params.get('roi_enabled', False), params.get('roi_shapes', [])):
                        roi_mask = self._roi_keep_mask_for_arrays(
                            x, y, z, params.get('roi_shapes', []), params.get('roi_enabled', False), matrix_rc)
                        roi_idx = np.where(roi_mask)[0]
                        if len(roi_idx) < 3:
                            raise ValueError("ROI 内有效数据点少于 3 个")
                    else:
                        roi_idx = np.arange(n_total)
                    bx, by, bz = x[roi_idx], y[roi_idx], z[roi_idx]
                    keep = self.filter_keep_mask(
                        bx, by, bz, params['mode'],
                        k=params['k'], threshold_mm=params['threshold_mm'],
                        sigma_k=params['sigma_k'], sigma_iters=params['sigma_iters'])
                    if params['mode'] != 0 and keep.sum() < 3:
                        keep = np.ones(len(roi_idx), dtype=bool)
                        n_filtered = 0
                    else:
                        n_filtered = int(len(roi_idx) - keep.sum())
                    active_idx = roi_idx[keep]
                    fx, fy, fz = x[active_idx], y[active_idx], z[active_idx]
                    metrics = self.compute_plane_metrics(fx, fy, fz)
                    roi_info = self._roi_report_info(
                        x, y, z, params.get('roi_enabled', False), params.get('roi_shapes', []), matrix_rc)
                    fig = self._render_report_figure(
                        name, x, y, z, active_idx, metrics, n_filtered,
                        params['pipeline_text'], params['filter_text'],
                        import_info_snap, params['display_detrended'], roi_info=roi_info)
                    out_png = out / f"result_{Path(path).stem}.png"
                    fig.savefig(str(out_png), dpi=150)
                    results.append({'status': 'ok', 'file': name, 'out': str(out_png)})
                    summary_rows.append({
                        '文件': name, '总点数': n_total, '参与拟合': int(len(active_idx)),
                        '结果质量': self._metric_quality_from_import(import_info_snap)['label'],
                        'ROI保留': int(len(roi_idx)) if roi_info.get('enabled') else '',
                        '滤波剔除': n_filtered,
                        '平均Z_mm': round(metrics['mean_z'], 6),
                        'PV_um': round(metrics['pv'], 3), 'TTV_um': round(metrics['ttv'], 3),
                        'Rx_urad': round(metrics['rx'], 2), 'Ry_urad': round(metrics['ry'], 2),
                        '平面方程': f"Z={metrics['a']:.4f}X+{metrics['b']:.4f}Y+{metrics['c']:.4f}",
                    })
                except Exception as e:
                    results.append({'status': 'fail', 'file': name, 'error': str(e)})
            if summary_rows:
                pd.DataFrame(summary_rows).to_csv(
                    out / 'result_batch_summary.csv', index=False, encoding='utf-8-sig')
        finally:
            self.import_info = saved_info
            self.last_import_note = saved_note
            self._update_import_status_label()
        return results

    def _render_report_figure(self, source_name, tx, ty, tz, active_idx, metrics,
                              n_filtered, pipeline_text, filter_text,
                              import_info, display_detrended, roi_info=None):
        """生成包含主页面全部信息(指标文本 + 四视图)的报告图，返回 Figure(Agg后端)。"""
        # YaHei 同时含中文与 µ(U+00B5)，避免报告图里 µm/µrad 出现缺字方块；其余字体兜底
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        fig = Figure(figsize=(17, 9), constrained_layout=True)
        FigureCanvasAgg(fig)
        gs = fig.add_gridspec(2, 3, width_ratios=[1.15, 1.0, 1.0])
        # 左栏再切成 元信息 / 结果卡片 / 脚注 三段，互不重叠
        gs_left = gs[:, 0].subgridspec(20, 1)
        ax_meta = fig.add_subplot(gs_left[0:8, 0]); ax_meta.axis('off')
        ax_res = fig.add_subplot(gs_left[8:17, 0]); ax_res.axis('off')
        ax_foot = fig.add_subplot(gs_left[18:20, 0]); ax_foot.axis('off')
        ax3d = fig.add_subplot(gs[0, 1], projection='3d')
        ax_xy = fig.add_subplot(gs[0, 2])
        ax_xz = fig.add_subplot(gs[1, 1])
        ax_yz = fig.add_subplot(gs[1, 2])

        coeffs = metrics['coeffs']
        if roi_info is None:
            roi_info = {'enabled': False, 'summary': '关闭', 'shape_lines': [], 'shapes': [], 'roi_enabled': False}
        # 绘图抽样（与主界面口径一致）；指标仍按全部参与拟合点
        plot_idx = active_idx
        limit = self._display_limit()
        if len(plot_idx) > limit:
            pick = np.linspace(0, len(plot_idx) - 1, limit, dtype=int)
            plot_idx = plot_idx[pick]
        dx, dy = tx[plot_idx], ty[plot_idx]
        if display_detrended:
            plot_z_all = (tz - (coeffs[0] * tx + coeffs[1] * ty + coeffs[2])) * 1000.0
            zlab, ttl3d, txt, tyt = "去倾斜残差 (µm)", "3D 去倾斜残差面型", "X-残差剖面", "Y-残差剖面"
        else:
            plot_z_all = tz
            zlab, ttl3d, txt, tyt = "Z (mm)", "3D 原始高度", "X-Z剖面", "Y-Z剖面"
        dz = plot_z_all[plot_idx]

        sc = {'c': dz, 'cmap': 'turbo', 's': 14, 'alpha': 0.85, 'edgecolors': 'none'}
        ax3d.scatter(dx, dy, dz, **sc)
        ax3d.set_title(ttl3d); ax3d.set_xlabel("X (mm)"); ax3d.set_ylabel("Y (mm)"); ax3d.set_zlabel(zlab)
        m_xy = ax_xy.scatter(dx, dy, **sc); ax_xy.set_title("XY 俯视分布"); ax_xy.set_xlabel("X (mm)"); ax_xy.set_ylabel("Y (mm)")
        self._draw_roi_overlays(ax_xy, roi_info.get('shapes'), roi_info.get('roi_enabled'), report=True)
        ax_xz.scatter(dx, dz, **sc); ax_xz.set_title(txt); ax_xz.set_xlabel("X (mm)"); ax_xz.set_ylabel(zlab)
        ax_yz.scatter(dy, dz, **sc); ax_yz.set_title(tyt); ax_yz.set_xlabel("Y (mm)"); ax_yz.set_ylabel(zlab)
        for ax in (ax_xy, ax_xz, ax_yz):
            ax.grid(True, linestyle=':', alpha=0.5)

        if len(dx) > 0:
            xx, yy = np.meshgrid(np.linspace(dx.min(), dx.max(), 10), np.linspace(dy.min(), dy.max(), 10))
            zz = np.zeros_like(xx) if display_detrended else coeffs[0] * xx + coeffs[1] * yy + coeffs[2]
            ax3d.plot_surface(xx, yy, zz, color='#3498db', alpha=0.3, edgecolor='none')
            # 颜色条：标明散点配色对应的高度/残差量级
            cbar = fig.colorbar(m_xy, ax=[ax_xz, ax_yz], location='bottom',
                                shrink=0.65, aspect=40, pad=0.12)
            cbar.set_label(zlab, fontsize=10)
            cbar.ax.tick_params(labelsize=8)

        # 顶部：元信息（较小字号）
        quality = self._metric_quality_from_import(import_info)
        relation = "≈" if quality['estimated'] else "="
        meta_lines = [
            f"报告时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"数据来源: {source_name}",
            f"导入方式: {import_info.get('strategy', '--')} | 抽样: {import_info.get('sampled', False)}",
            f"结果质量: {quality['label']}",
            f"源文件大小: {import_info.get('file_size_mb', 0.0):.1f} MB | 读入行: {import_info.get('import_rows', 0)}",
            f"有效点数: {import_info.get('valid_rows', len(tz))} | 参与拟合: {len(active_idx)} | 滤波剔除: {n_filtered}",
            f"变换路径: {pipeline_text}",
            f"滤波模式: {filter_text}",
            f"ROI: {roi_info.get('summary', '关闭')}",
            f"显示模式: {'去倾斜残差 (µm)' if display_detrended else '原始Z高度 (mm)'}",
        ]
        if roi_info.get('shape_lines'):
            roi_shape_text = "；".join(roi_info['shape_lines'][:4])
            if len(roi_info['shape_lines']) > 4:
                roi_shape_text += f"；另 {len(roi_info['shape_lines']) - 4} 个"
            meta_lines.append(f"ROI形状: {self._short_report_text(roi_shape_text, 70)}")
        ax_meta.text(0.02, 0.98, "\n".join(meta_lines), va='top', ha='left',
                     fontsize=10.0, linespacing=1.55, color='#34495e', transform=ax_meta.transAxes)

        # 中部：关键结果卡片（大字号 + 高亮底色，手机上一眼可读）
        results_text = (
            "【分析结果】\n\n"
            f"平面方程   Z {relation} {metrics['a']:.4f}·X + {metrics['b']:.4f}·Y + {metrics['c']:.4f}\n\n"
            f"平均厚度 Z    {relation} {metrics['mean_z']:.5f} mm\n"
            f"面型 PV(法向) {relation} {metrics['pv']:.3f} µm\n"
            f"TTV(Z 极差)   {relation} {metrics['ttv']:.3f} µm\n"
            f"物料 Rx       {relation} {metrics['rx']:.2f} µrad\n"
            f"物料 Ry       {relation} {metrics['ry']:.2f} µrad"
        )
        ax_res.text(0.02, 0.97, results_text, va='top', ha='left',
                    fontsize=13.5, linespacing=1.6, color='#11447a', transform=ax_res.transAxes,
                    bbox=dict(boxstyle='round,pad=0.7', facecolor='#eaf2fb', edgecolor='#3498db', linewidth=1.4))

        # 底部：脚注
        foot_text = (f"警告: {quality['warning']}\n" if quality['warning'] else "") + (
                     "注: Rx≈+dZ/dY, Ry≈-dZ/dX，符号约定需标准件校准。\n"
                     "PV 为相对最佳拟合平面的法向残差极差。批量无手动删点。")
        ax_foot.text(0.02, 0.9, foot_text,
                     va='top', ha='left', fontsize=9, style='italic',
                     color='#b42318' if quality['warning'] else '#7f8c8d', transform=ax_foot.transAxes)

        fig.suptitle(f"面型及Rxy分析报告 ({self.APP_VERSION}) — {source_name}", fontsize=16, fontweight='bold')
        return fig
