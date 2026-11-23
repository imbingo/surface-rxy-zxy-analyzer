"""Qt module UI for the optional VIG image-plane extension."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from surface_analyzer import APP_VERSION
from surface_analyzer.theme import is_dark
from surface_analyzer.workers import TaskCancelled

from .engine import compute_vig_image

_EXTENSION_MANIFEST = json.loads(
    Path(__file__).with_name('extension.json').read_text(encoding='utf-8'))


class VigImageModule:
    key = 'vig-image'
    label = 'VIG'
    caption = 'VIG 像面分析'
    export_label = '导出 VIG'
    uses_export_origin = True

    def __init__(self, host):
        self.host = host
        self.slots = {'two': None, 'three': None, 'up': None, 'mid': None, 'low': None}
        self.result = None
        self._task_running = False
        self.thickness_spins = {}
        self.slot_labels = {}
        self.clear_buttons = {}
        self.set_buttons = {}
        self._syncing = False

    def icon(self, color):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(2.1)
        painter.setPen(pen)
        painter.drawEllipse(7, 7, 18, 18)
        painter.drawEllipse(11, 11, 10, 10)
        painter.drawLine(2, 16, 7, 16)
        painter.drawLine(25, 16, 30, 16)
        painter.drawLine(16, 2, 16, 7)
        painter.drawLine(16, 25, 16, 30)
        painter.end()
        return QIcon(pixmap)

    def _spin(self, key, value, minimum, maximum, decimals):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(float(self.host.settings_value(key, value)))
        spin.valueChanged.connect(
            lambda current, name=key: self._spin_value_changed(name, current))
        return spin

    def _spin_value_changed(self, key, value):
        self.host.set_setting(key, value)
        self._invalidate_result()

    def build_sidebar(self, parent):
        root = QWidget(parent)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        note = QLabel(
            "VIG_up = 三层 − 两层 − t_up；VIG_low = 两层 − t_mid − t_low（µm）。"
            "在单层页处理好数据后写入。")
        note.setObjectName('mutedNote')
        note.setWordWrap(True)
        layout.addWidget(note)

        group_two = self._group("1. 两层总厚（mid+low）")
        self._slot_row(group_two, 'two', '两层总厚')
        layout.addWidget(group_two)
        group_three = self._group("2. 三层总厚（up+mid+low）")
        self._slot_row(group_three, 'three', '三层总厚')
        layout.addWidget(group_three)
        group_thickness = self._group("3. 电极厚度（不写入则用固定值）")
        for key, text in (('up', 't_up'), ('mid', 't_mid'), ('low', 't_low')):
            self._slot_row(group_thickness, key, text, thickness=True)
        layout.addWidget(group_thickness)
        group_calc = self._group("4. 计算")
        row = QHBoxLayout()
        row.addWidget(QLabel("XY 匹配容差 (mm)"))
        self.tolerance_spin = self._spin('tolerance_mm', 0.01, 0.0001, 1.0, 4)
        row.addWidget(self.tolerance_spin)
        group_calc.layout().addLayout(row)
        self.btn_calculate = QPushButton("计算 VIG 像面")
        self.btn_calculate.setObjectName('accentBtn')
        self.btn_calculate.clicked.connect(self.calculate_vig)
        group_calc.layout().addWidget(self.btn_calculate)
        layout.addWidget(group_calc)
        layout.addStretch(1)
        controls = [self.btn_calculate, self.tolerance_spin]
        controls.extend(self.set_buttons.values())
        controls.extend(self.clear_buttons.values())
        controls.extend(self.thickness_spins.values())
        self.host.register_task_controls(controls)
        self._sync_slot_labels()
        return root

    def _group(self, title):
        group = QGroupBox(title)
        group.setLayout(QVBoxLayout())
        group.layout().setContentsMargins(10, 8, 10, 8)
        group.layout().setSpacing(6)
        return group

    def _slot_row(self, group, key, label, thickness=False):
        row = QHBoxLayout()
        row.setSpacing(6)
        title = QLabel(label)
        title.setMinimumWidth(52)
        row.addWidget(title)
        if thickness:
            spin = self._spin(f'thickness_{key}_um', 150.0, 1.0, 5000.0, 3)
            spin.setFixedWidth(88)
            row.addWidget(spin)
            self.thickness_spins[key] = spin
            group.layout().addLayout(row)
            row = QHBoxLayout()
            row.setSpacing(6)
        set_button = QPushButton("用当前数据")
        set_button.setMinimumWidth(90)
        set_button.clicked.connect(lambda checked=False, slot=key: self.write_slot(slot))
        clear_button = QPushButton("清除")
        clear_button.setMinimumWidth(82)
        clear_button.setMaximumWidth(90)
        clear_button.clicked.connect(lambda checked=False, slot=key: self.clear_slot(slot))
        row.addWidget(set_button)
        row.addWidget(clear_button)
        group.layout().addLayout(row)
        state = QLabel("固定值：150 µm" if thickness else "未设置")
        state.setWordWrap(True)
        group.layout().addWidget(state)
        self.slot_labels[key] = state
        self.set_buttons[key] = set_button
        self.clear_buttons[key] = clear_button

    def build_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        strip = QFrame()
        strip.setObjectName('metricSurface')
        grid = QGridLayout(strip)
        self.metric_labels = {}
        metrics = (
            ('n_points', '参与计算点', '--'),
            ('rx', 'Rx (µrad)', '--'),
            ('ry', 'Ry (µrad)', '--'),
            ('rms', 'RMS (µm)', '--'),
            ('std3', '3σ (µm)', '--'),
            ('p97_half', 'P97/2 (µm)', '--'),
        )
        for index, (key, title, value) in enumerate(metrics):
            card = QFrame()
            card.setObjectName('metricCard')
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 6, 8, 6)
            card_layout.addWidget(QLabel(title))
            label = QLabel(value)
            label.setObjectName('metricValue')
            card_layout.addWidget(label)
            grid.addWidget(card, index // 3, index % 3)
            self.metric_labels[key] = label
        self.judgment_card = self.host.make_judgment_card(
            self.key,
            {'rx': self.metric_labels['rx'], 'ry': self.metric_labels['ry']},
            ('rx', 'ry'),
        )
        grid.addWidget(self.judgment_card, 0, 3, 2, 1)
        layout.addWidget(strip)
        plot_card = QFrame()
        plot_card.setObjectName('plotCard')
        plot_layout = QVBoxLayout(plot_card)
        heading = QLabel("VIG 像面 image 分布")
        heading.setObjectName('stepTitle')
        plot_layout.addWidget(heading)
        self.hint_label = QLabel("写入两层、三层数据后开始计算。")
        self.hint_label.setWordWrap(True)
        plot_layout.addWidget(self.hint_label)
        self.figure = Figure(figsize=(8, 6), tight_layout=True)
        self.ax = self.figure.add_subplot(111)
        self.canvas = self.host.make_canvas(self.figure)
        plot_layout.addWidget(self.canvas, 1)
        layout.addWidget(plot_card, 1)
        self._draw_result()
        return panel

    def _sync_slot_labels(self):
        for key, label in self.slot_labels.items():
            record = self.slots[key]
            if record is None:
                if key in self.thickness_spins:
                    label.setText(f"固定值：{self.thickness_spins[key].value():g} µm")
                else:
                    label.setText("未设置")
            else:
                label.setText(f"逐点：{record['name']} ({record['n']:,} 点)")
            label.setToolTip(label.text())

    def write_slot(self, slot):
        if not self.host.confirm_estimated_metrics(f'写入 VIG {slot} 数据槽'):
            return False
        try:
            self.slots[slot] = self.host.current_layer_snapshot()
        except ValueError as exc:
            QMessageBox.warning(None, "单层数据尚不可写入", str(exc))
            return False
        self._invalidate_result()
        self._sync_slot_labels()
        self.host.status(f"已写入 VIG 数据槽：{self.slots[slot]['name']} ({self.slots[slot]['n']:,} 点)")
        return True

    def clear_slot(self, slot):
        self.slots[slot] = None
        self._invalidate_result()
        self._sync_slot_labels()

    def _invalidate_result(self):
        self.result = None
        if hasattr(self, 'metric_labels'):
            for key, label in self.metric_labels.items():
                label.setText('--')
            self.host.refresh_judgment(self.key, {})
            self._draw_result()
        self.host.refresh_module_chrome()

    def calculate_vig(self):
        if self.slots['two'] is None or self.slots['three'] is None:
            QMessageBox.warning(None, "数据不足", "请先写入两层总厚和三层总厚数据。")
            return False
        thickness = {
            key: self.slots[key] if self.slots[key] is not None else spin.value()
            for key, spin in self.thickness_spins.items()
        }
        summary = [
            f"两层：{self.slots['two']['name']}（{self.slots['two']['n']:,} 点）",
            f"三层：{self.slots['three']['name']}（{self.slots['three']['n']:,} 点）",
            f"容差：{self.tolerance_spin.value():g} mm",
        ]
        for key in ('up', 'mid', 'low'):
            value = thickness[key]
            summary.append(
                f"{key}: {'逐点 ' + value['name'] if isinstance(value, dict) else f'固定 {value:g} µm'}")
        if QMessageBox.question(
                None, "计算 VIG 像面", "\n".join(summary) + "\n\n开始计算？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return False
        self._task_running = True
        self.on_idle()

        def task(progress, cancel_event):
            def check_cancel():
                if cancel_event.is_set():
                    raise TaskCancelled()
            return compute_vig_image(
                self.slots['two'], self.slots['three'], thickness,
                tolerance_mm=self.tolerance_spin.value(),
                progress=progress, check_cancel=check_cancel)

        def success(payload):
            self._task_running = False
            self._apply_vig_payload(payload)

        def error(message):
            self._task_running = False
            QMessageBox.critical(None, "VIG 像面计算失败", str(message))
            self.on_idle()

        def cancelled():
            self._task_running = False
            self.host.status("VIG 像面计算已取消")
            self.on_idle()

        return self.host.run_task(
            "VIG 像面计算", task, success, on_error=error, on_cancel=cancelled)

    def _apply_vig_payload(self, payload):
        self.result = payload
        plane = payload['plane']
        self.metric_labels['n_points'].setText(f"{payload['n_points']:,}")
        self.metric_labels['rx'].setText(f"{plane['rx'] * 1e6:.4f}")
        self.metric_labels['rx'].setToolTip(f"{plane['rx'] * 1e3:.6f} mrad")
        self.metric_labels['ry'].setText(f"{plane['ry'] * 1e6:.4f}")
        self.metric_labels['ry'].setToolTip(f"{plane['ry'] * 1e3:.6f} mrad")
        for key in ('rms', 'std3', 'p97_half'):
            self.metric_labels[key].setText(f"{plane[key] * 1e6:.5g}")
        self.host.refresh_judgment(self.key, {
            'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6,
        })
        self.hint_label.setText(self._match_summary())
        self.hint_label.setToolTip(self.hint_label.text())
        self._draw_result()
        self.host.status(f"VIG 像面计算完成，参与计算 {payload['n_points']:,} 点")
        self.host.refresh_module_chrome()

    def _match_summary(self):
        if not self.result:
            return "写入两层、三层数据后开始计算。"
        parts = []
        for report in self.result['match_reports']:
            skipped = report['total_ref'] - report['matched']
            parts.append(
                f"{report['label']}：匹配 {report['matched']}/{report['total_ref']} 点，跳过 {skipped} 点")
            coords = report['unmatched_ref_xy']
            if coords:
                shown = '、'.join(f"({x:.4f}, {y:.4f})" for x, y in coords[:10])
                parts.append(f"未匹配坐标：{shown}" + ("…" if skipped > len(coords) else ""))
        return "；".join(parts)

    def _draw_result(self):
        if not hasattr(self, 'ax'):
            return
        dark = is_dark()
        self.figure.patch.set_facecolor('#141415' if dark else '#ffffff')
        if getattr(self, 'colorbar', None) is not None:
            self.colorbar.remove()
            self.colorbar = None
        self.ax.clear()
        self.ax.set_facecolor('#141415' if dark else '#ffffff')
        self.ax.tick_params(colors='#ededed' if dark else '#333333')
        self.ax.set_xlabel('X (mm)')
        self.ax.set_ylabel('Y (mm)')
        if self.result is None:
            self.ax.text(.5, .5, "暂无 VIG 像面结果", ha='center', va='center',
                         transform=self.ax.transAxes,
                         color='#ededed' if dark else '#555555')
        else:
            scatter = self.ax.scatter(
                self.result['x'], self.result['y'], c=self.result['image'],
                cmap='turbo', s=16)
            self.ax.set_aspect('equal', adjustable='box')
            divider = make_axes_locatable(self.ax)
            colorbar_axis = divider.append_axes("right", size="3%", pad=0.08)
            self.colorbar = self.figure.colorbar(scatter, cax=colorbar_axis)
            self.colorbar.set_label('image (µm)')
        for spine in self.ax.spines.values():
            spine.set_color('#777777' if dark else '#555555')
        self.canvas.draw_idle()

    def export_actions(self):
        ready = self.result is not None
        return [
            ("导出 VIG 报告图", self.export_vig_report, ready),
            ("导出 VIG CSV", self.export_vig_csv, ready),
        ]

    def status_text(self):
        def state(key):
            return '已写入' if self.slots[key] is not None else '未设置'
        modes = [
            '逐点' if self.slots[key] is not None else '固定'
            for key in ('up', 'mid', 'low')
        ]
        thickness = modes[0] if len(set(modes)) == 1 else '固定/逐点'
        return (f"VIG · 两层 {state('two')} · 三层 {state('three')}"
                f" · 电极厚度 {thickness}")

    def on_idle(self):
        busy = self._task_running
        for control in getattr(self, 'set_buttons', {}).values():
            control.setEnabled(not busy)
        for control in getattr(self, 'clear_buttons', {}).values():
            control.setEnabled(not busy)
        for control in getattr(self, 'thickness_spins', {}).values():
            control.setEnabled(not busy)
        if hasattr(self, 'tolerance_spin'):
            self.tolerance_spin.setEnabled(not busy)
        if hasattr(self, 'btn_calculate'):
            self.btn_calculate.setEnabled(not busy and self.slots['two'] is not None
                                          and self.slots['three'] is not None)

    def _export_metadata(self):
        payload = self.result
        plane = payload['plane']
        judgment = self.host.current_judgment(
            self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})
        return [
            f"# Surface Analyzer {APP_VERSION}",
            f"# 导出时间: {datetime.now().isoformat(timespec='seconds')}",
            "# 公式: VIG_up = 三层 − 两层 − t_up；VIG_low = 两层 − t_mid − t_low（µm）",
            f"# 输入: {payload['inputs']}",
            f"# 电极厚度: {payload['thickness_description']}",
            f"# XY 匹配容差: {payload['tolerance']:g} mm",
            f"# 匹配报告: {payload['match_reports']}",
            f"# Rx: {plane['rx'] * 1e6:.9g} µrad ({plane['rx'] * 1e3:.9g} mrad)",
            f"# Ry: {plane['ry'] * 1e6:.9g} µrad ({plane['ry'] * 1e3:.9g} mrad)",
            f"# Z: {plane['z']:.12g} m",
            f"# RMS: {plane['rms'] * 1e6:.9g} µm；3σ: {plane['std3'] * 1e6:.9g} µm；"
            f"P97/2: {plane['p97_half'] * 1e6:.9g} µm",
            f"# 判定: {self.host.judgment_summary(self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})}",
            f"# 导出坐标原点: {self.host.export_origin_offset(payload['x'], payload['y'])[2]}",
            *[f"# {line}" for line in self.host.judgment_lines(
                self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})],
        ]

    def export_vig_csv(self):
        if self.result is None:
            return
        try:
            path = self.host.save_path(
                "导出 VIG CSV", "vig_image.csv", "CSV 文件 (*.csv)")
            if not path:
                return
            payload = self.result
            dx, dy, _ = self.host.export_origin_offset(payload['x'], payload['y'])
            data = {
                'X_mm': payload['x'] - dx,
                'Y_mm': payload['y'] - dy,
                'VIG_up_um': payload['vig_up'],
                'VIG_low_um': payload['vig_low'],
                'thickness_up_um': payload['t_up'],
                'thickness_mid_um': payload['t_mid'],
                'thickness_low_um': payload['t_low'],
                'f_img_um': payload['f_img'],
                'img_principle_z_um': payload['img_principle_z'],
                'image_um': payload['image'],
                'Resid_um': payload['resid_um'],
            }
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                stream.write('\n'.join(self._export_metadata()) + '\n')
                pd.DataFrame(data).to_csv(stream, index=False)
            self.host.status(f"已导出 VIG CSV：{path}")
        except Exception as exc:
            QMessageBox.warning(None, "导出失败", f"VIG CSV 导出失败：{exc}")

    def _render_vig_report_figure(self):
        if self.result is None:
            raise ValueError("暂无有效 VIG 像面结果。")
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        payload = self.result
        plane = payload['plane']
        inputs = payload['inputs']
        thickness = payload['thickness_description']
        judgment = self.host.judgment_lines(
            self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})
        judgment_summary = self.host.judgment_summary(
            self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})
        figure = Figure(
            figsize=(12.5, 8.8), facecolor='white', constrained_layout=True)
        FigureCanvasAgg(figure)
        grid = figure.add_gridspec(3, 1, height_ratios=(1.55, 0.38, 5.7))
        ax_header = figure.add_subplot(grid[0])
        ax_metrics = figure.add_subplot(grid[1])
        ax_map = figure.add_subplot(grid[2])
        ax_header.axis('off')
        ax_metrics.axis('off')
        ax_header.text(
            0.01, 0.98, "VIG 像面报告", va='top', ha='left',
            fontsize=16, fontweight='bold', color='#1f2937',
            transform=ax_header.transAxes)
        left_lines = [
            f"主程序 {APP_VERSION} · 扩展 {_EXTENSION_MANIFEST['id']} "
            f"{_EXTENSION_MANIFEST['version']}",
            f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"两层总厚 {Path(str(inputs['two_layer']['name'])).name}",
            f"三层总厚 {Path(str(inputs['three_layer']['name'])).name}",
            "电极厚度 " + "；".join(
                f"{key} {thickness[key]}" for key in ('up', 'mid', 'low')),
        ]
        judgment_header = (
            judgment_summary
            or ("；".join(judgment) if judgment else "结果判定  尚未设置判定规格"))
        right_lines = [
            f"XY 匹配容差 {payload['tolerance']:g} mm",
            f"匹配摘要 {self._match_summary()}",
            judgment_header,
            *(judgment if judgment_summary else []),
        ]
        for x, lines in ((0.01, left_lines), (0.52, right_lines)):
            ax_header.text(
                x, 0.78, "\n".join(lines), va='top', ha='left', fontsize=9,
                linespacing=1.35, wrap=True, color='#334155',
                transform=ax_header.transAxes)
        ax_metrics.text(
            0.01, 0.5,
            f"参与计算 {payload['n_points']:,} 点  ·  "
            f"Rx {plane['rx'] * 1e6:.4f} µrad  ·  "
            f"Ry {plane['ry'] * 1e6:.4f} µrad  ·  "
            f"RMS {plane['rms'] * 1e6:.4g} µm  ·  "
            f"3σ {plane['std3'] * 1e6:.4g} µm  ·  "
            f"P97/2 {plane['p97_half'] * 1e6:.4g} µm",
            va='center', ha='left', fontsize=10, fontweight='bold',
            color='#1f2937', transform=ax_metrics.transAxes)
        scatter = ax_map.scatter(
            payload['x'], payload['y'], c=payload['image'], cmap='turbo',
            s=18, edgecolors='none', rasterized=True)
        ax_map.set_xlabel("X (mm)")
        ax_map.set_ylabel("Y (mm)")
        ax_map.set_title("VIG 像面 image 分布")
        ax_map.grid(True, color='#edf0f3')
        ax_map.set_aspect('equal', adjustable='datalim')
        divider = make_axes_locatable(ax_map)
        colorbar_axis = divider.append_axes("right", size="3%", pad=0.08)
        figure.colorbar(scatter, cax=colorbar_axis, label="image (µm)")
        return figure

    def export_vig_report(self):
        if self.result is None:
            return
        figure = None
        try:
            path = self.host.save_path(
                "导出 VIG 报告图", "vig_image_report.png", "PNG 图片 (*.png)")
            if not path:
                return
            figure = self._render_vig_report_figure()
            figure.savefig(
                path, dpi=180, facecolor='white', bbox_inches='tight', pad_inches=0.2)
            self.host.status(f"已导出 VIG 报告图：{path}")
        except Exception as exc:
            QMessageBox.warning(None, "导出失败", f"VIG 报告图导出失败：{exc}")
        finally:
            if figure is not None:
                plt.close(figure)
