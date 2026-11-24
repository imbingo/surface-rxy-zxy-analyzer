"""Qt module UI for the optional VIG image-plane extension."""

from __future__ import annotations

import json
from copy import deepcopy
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
    QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget, QScrollArea, QLayout,
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
        self.host_module = self

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
        self._sync_slot_labels()

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
        self.btn_secondary = QPushButton('载入像面到单层二次分析')
        self.btn_secondary.clicked.connect(self.load_secondary)
        group_calc.layout().addWidget(self.btn_secondary)
        self.btn_save_composite = QPushButton('保存组合 Recipe')
        self.btn_save_composite.clicked.connect(lambda: self.host.save_composite_recipe('vig'))
        group_calc.layout().addWidget(self.btn_save_composite)
        self.btn_run_composite = QPushButton('运行组合 Recipe / 批量运行')
        self.btn_run_composite.clicked.connect(lambda: self.host.choose_composite_recipe('vig'))
        group_calc.layout().addWidget(self.btn_run_composite)
        layout.addWidget(group_calc)
        layout.addStretch(1)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        controls = [self.btn_calculate, self.tolerance_spin, self.btn_secondary, self.btn_save_composite, self.btn_run_composite]
        controls.extend(self.set_buttons.values())
        controls.extend(self.clear_buttons.values())
        controls.extend(self.thickness_spins.values())
        self.host.register_task_controls(controls)
        self._sync_slot_labels()
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(root)
        return scroll

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
        self.figure = Figure(figsize=(8, 6), constrained_layout=True)
        self.ax = None
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
        self.on_idle()
        self.host.refresh_module_chrome()

    def compute_snapshot(self, records, config, progress=lambda value,text: None, check_cancel=lambda: None):
        thickness = {key: (records[key] if config['electrodes'][key]['mode'] == 'file'
                    else config['electrodes'][key]['value_um']) for key in ('up','mid','low')}
        payload = compute_vig_image(records['two'], records['three'], thickness,
            tolerance_mm=config['calculation']['tolerance_mm'], progress=progress, check_cancel=check_cancel)
        return self._trace_payload(payload, records)

    @staticmethod
    def _trace_payload(payload, records):
        payload['input_traces'] = {key: {field: deepcopy(record.get(field)) for field in
            ('name','single_recipe','metric_quality','import_info','source_path')}
            for key,record in records.items() if record is not None}
        return payload

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
            payload = compute_vig_image(
                self.slots['two'], self.slots['three'], thickness,
                tolerance_mm=self.tolerance_spin.value(),
                progress=progress, check_cancel=check_cancel)
            return self._trace_payload(payload, self.slots)

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
        judgment_values = {
            'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6,
        }
        quality = payload.get('metric_quality') or {}
        if quality.get('estimated'):
            judgment_values['_estimated'] = True
            judgment_values['_quality_warning'] = quality.get('warning', '')
        self.host.refresh_judgment(self.key, judgment_values)
        quality_warning = quality.get('warning', '')
        self.hint_label.setText(
            (f"⚠ {quality_warning}；" if quality_warning else '')
            + self._match_summary())
        self.hint_label.setToolTip(self.hint_label.text())
        self._draw_result()
        self.on_idle()
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
        parts.append(f"计算失败 {self.result.get('n_errors', 0)} 点")
        for error in self.result.get('errors', [])[:10]:
            parts.append(f"点 {error['index']}: {error['message']}")
        if self.result.get('n_errors', 0) > 10:
            parts.append('其余错误见 CSV 元数据和运行记录')
        return "；".join(parts)

    def load_secondary(self):
        if self.result is None:
            return False
        payload = self.result
        self.host.load_result_copy(payload['x'], payload['y'],
            np.asarray(payload['image'], dtype=float) * .001,
            name='VIG image 二次分析（编辑副本）',
            quality=payload.get('metric_quality', {}),
            metadata={'inputs': payload['inputs'], 'image_unit': 'µm', 'secondary_scale_to_mm': .001,
                      'coordinate_sign': '保留 image 原始符号；VIG 模块原拟合使用 -image，单层使用当前 Z，各自口径不改',
                      'slot_parameters': {k: v.get('single_recipe') for k,v in payload.get('input_traces', {}).items()}, 'n_errors': payload.get('n_errors', 0),
                      'errors': payload.get('errors', []), 'tolerance': payload['tolerance'],
                      'thickness_description': payload['thickness_description']})
        return True

    def _draw_result(self):
        if not hasattr(self, 'figure'):
            return
        from surface_analyzer.result_views import draw_four_views
        self.figure.clear()
        payload = self.result
        if payload is None:
            self.figure.text(.5, .5, '暂无 VIG 像面结果', ha='center', va='center')
            self.ax = None
            self.canvas.draw_idle()
            return
        axes = draw_four_views(self.figure,
            [] if payload is None else payload['x'],
            [] if payload is None else payload['y'],
            [] if payload is None else payload['image'], quantity='image', unit='µm')
        self.ax = axes[1]
        self.colorbar = self.figure.axes[-1]._colorbar
        self.ax.set_title('VIG 像面 image 分布')
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
        if hasattr(self, 'btn_secondary'):
            self.btn_secondary.setEnabled(not busy and self.result is not None)
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
            f"# 结果质量: {(payload.get('metric_quality') or {}).get('warning') or '全量计算'}",
            f"# 电极厚度: {payload['thickness_description']}",
            f"# XY 匹配容差: {payload['tolerance']:g} mm",
            f"# 匹配报告: {payload['match_reports']}",
            f"# 失败点数: {payload.get('n_errors', 0)}",
            "# 输入处理参数快照: " + json.dumps(payload.get('input_traces', {}), ensure_ascii=False, default=lambda v: v.tolist() if isinstance(v,np.ndarray) else str(v)),
            f"# 错误详情: {json.dumps(payload.get('errors', []), ensure_ascii=False)}",
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
            self.write_csv(path)
            self.host.status(f"已导出 VIG CSV：{path}")
        except Exception as exc:
            QMessageBox.warning(None, "导出失败", f"VIG CSV 导出失败：{exc}")

    def write_csv(self, path):
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

    def _render_vig_report_figure(self):
        if self.result is None:
            raise ValueError("暂无有效 VIG 像面结果。")
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        payload = self.result
        plane = payload['plane']
        inputs = payload['inputs']
        thickness = payload['thickness_description']
        dx, dy, origin_line = self.host.export_origin_offset(
            payload['x'], payload['y'])
        judgment = self.host.judgment_lines(
            self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})
        judgment_summary = self.host.judgment_summary(
            self.key, {'rx': plane['rx'] * 1e6, 'ry': plane['ry'] * 1e6})
        figure = Figure(
            figsize=(17, 10.5), facecolor='white', constrained_layout=True)
        FigureCanvasAgg(figure)
        header_height = 1.55
        grid = figure.add_gridspec(
            3, 1, height_ratios=(header_height, 0.38, 5.7))
        ax_header = figure.add_subplot(grid[0])
        ax_metrics = figure.add_subplot(grid[1])
        plot_spec = grid[2]
        ax_header.axis('off')
        ax_metrics.axis('off')
        ax_header.text(
            0.01, 0.98, "VIG 像面报告", va='top', ha='left',
            fontsize=16, fontweight='bold', color='#1f2937',
            transform=ax_header.transAxes, parse_math=False)
        left_lines = [
            f"主程序 {APP_VERSION} · 扩展 {_EXTENSION_MANIFEST['id']} "
            f"{_EXTENSION_MANIFEST['version']}",
            f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"两层总厚 {Path(str(inputs['two_layer']['name'])).name}",
            f"三层总厚 {Path(str(inputs['three_layer']['name'])).name}",
            "电极厚度 " + "；".join(
                f"{key} {thickness[key]}" for key in ('up', 'mid', 'low')),
            f"导出坐标原点 {origin_line}",
        ]
        for key, trace in payload.get('input_traces', {}).items():
            single = trace.get('single_recipe')
            if not single:
                left_lines.append(f"{key} 参数: 未记录可重放单层参数（内存或旧槽）；来源与质量见 CSV")
                continue
            units = single.get('units', {})
            unit_text = '/'.join(str(units.get(axis+'_unit','--')) for axis in 'xyz')
            pose = ' → '.join(single.get('transform_pipeline', [])) or '原始状态'
            roi = '启用' if (single.get('roi') or {}).get('enabled', False) else '关闭'
            left_lines.append(f"{key} 参数: X/Y/Z 单位 {unit_text}；姿态 {pose}；"
                              f"滤波 {(single.get('filter') or {}).get('mode_text', '关闭')}；ROI {roi}")
        judgment_header = (
            judgment_summary
            or ("；".join(judgment) if judgment else "结果判定  尚未设置判定规格"))
        right_lines = [
            f"XY 匹配容差 {payload['tolerance']:g} mm",
            f"匹配摘要 {self._match_summary()}",
            (payload.get('metric_quality') or {}).get('warning') or "结果质量 全量计算",
            judgment_header,
            *(judgment if judgment_summary else []),
        ]
        left_text = ax_header.text(
            0.01, 0.78, "", va='top', ha='left', fontsize=9,
            linespacing=1.35, color='#334155', transform=ax_header.transAxes,
            parse_math=False)
        left_text.set_gid('vig-report-left-column')
        right_text = ax_header.text(
            0.52, 0.78, "", va='top', ha='left', fontsize=9,
            linespacing=1.35, color='#334155', transform=ax_header.transAxes,
            parse_math=False)
        right_text.set_gid('vig-report-right-column')
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()

        def wrap_to_width(text_artist, lines, start, end):
            start_x = ax_header.transAxes.transform((start, 0))[0]
            end_x = ax_header.transAxes.transform((end, 0))[0]
            max_width = end_x - start_x - 2
            wrapped = []
            for line in lines:
                current = ''
                for character in line:
                    candidate = current + character
                    text_artist.set_text(candidate)
                    if (current
                            and text_artist.get_window_extent(renderer).width > max_width):
                        wrapped.append(current)
                        current = character
                    else:
                        current = candidate
                if current:
                    wrapped.append(current)
            return "\n".join(wrapped)

        left_text.set_text(wrap_to_width(left_text, left_lines, 0.01, 0.50))
        right_text.set_text(wrap_to_width(right_text, right_lines, 0.52, 0.99))
        for _ in range(5):
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
            header_bbox = ax_header.get_window_extent(renderer)
            text_bboxes = (
                left_text.get_window_extent(renderer),
                right_text.get_window_extent(renderer),
            )
            overflow = header_bbox.y0 + 4 - min(box.y0 for box in text_bboxes)
            if overflow <= 0:
                break
            header_height = header_height * (1 + overflow / header_bbox.height) + 0.2
            grid.set_height_ratios((header_height, 0.38, 5.7))
        else:
            raise RuntimeError("VIG 报告表头内容无法完整放入表头区域")

        renderer = figure.canvas.get_renderer()
        figure_bbox = figure.bbox
        header_bbox = ax_header.get_window_extent(renderer)
        left_bbox = left_text.get_window_extent(renderer)
        right_bbox = right_text.get_window_extent(renderer)
        left_boundary = ax_header.transAxes.transform((0.50, 0))[0]
        right_boundary = ax_header.transAxes.transform((0.99, 0))[0]
        if (left_bbox.x1 > left_boundary or right_bbox.x1 > right_boundary
                or left_bbox.x0 < figure_bbox.x0 or right_bbox.x0 < figure_bbox.x0
                or left_bbox.y0 < header_bbox.y0 or right_bbox.y0 < header_bbox.y0
                or left_bbox.y1 > figure_bbox.y1 or right_bbox.y1 > figure_bbox.y1):
            raise RuntimeError("VIG 报告表头文字超出列宽或图像边界")
        ax_metrics.text(
            0.01, 0.5,
            f"参与计算 {payload['n_points']:,} 点  ·  "
            f"Rx {plane['rx'] * 1e6:.4f} µrad  ·  "
            f"Ry {plane['ry'] * 1e6:.4f} µrad  ·  "
            f"RMS {plane['rms'] * 1e6:.4g} µm  ·  "
            f"3σ {plane['std3'] * 1e6:.4g} µm  ·  "
            f"P97/2 {plane['p97_half'] * 1e6:.4g} µm",
            va='center', ha='left', fontsize=10, fontweight='bold',
            color='#1f2937', transform=ax_metrics.transAxes, parse_math=False)
        from surface_analyzer.result_views import draw_four_views
        view_axes = draw_four_views(figure, payload['x'] - dx, payload['y'] - dy,
                        payload['image'], spec=plot_spec, quantity='image', unit='µm')
        view_axes[1].set_title('VIG 像面 image 分布')
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
