"""Qt workspace for the optional XYZ fitting module."""

from dataclasses import fields
from itertools import permutations
import json
from pathlib import Path
import tempfile

from matplotlib.figure import Figure
from matplotlib.patheffects import withStroke
import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from surface_analyzer.workers import TaskCancelled

from .engine import (
    ANGLE_DEFINITION, NORMAL_DEFINITION, QUALITY_DEFINITION, angle_difference,
    compare_in_base_frame, fit_plane, fit_plane_with_outlier_rejection, frame_rotation, normal_angles_in_frame, plane_angle_deg,
    three_point_frame, two_plane_frame,
)


SLOTS = ('基准面 A', '测量面 B', '测量面 C')
POINT_ORDERS = tuple(permutations(range(3)))
COLORS = ('#4588c4', '#e7883a', '#35a083')
METRICS = ('单位法向量 (X, Y, Z)', 'D (mm)', 'Rx (°)', 'Ry (°)', '有效点数',
           '法向距离 RMS (µm)', '最大绝对偏差 (µm)')


def _label(text='', role=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if role:
        label.setObjectName(role)
    return label


def _scroll(content):
    area = QScrollArea()
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


def _angle(value, sensitive=False):
    if value is None:
        return '不可定义'
    digits = 1 if sensitive else 3
    rounded = round(value, digits)
    if rounded == 90:
        rounded = -90
    prefix = '≈' if sensitive else ''
    return f'{prefix}{rounded + 0.0:.{digits}f}'


def _json_default(value):
    if isinstance(value, (np.ndarray, np.generic)):
        return value.tolist()
    return str(value)


class GenericPlaneFitModule:
    key = 'generic-plane-fit'
    label = '通用三维\n拟合'
    caption = '通用三维拟合'
    export_label = '导出拟合结果'

    def __init__(self, host):
        self.host = host
        self.slots = (None, None, None)
        self.point_orders = {}
        self.result = None

    @staticmethod
    def icon(color):
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 3))
        painter.drawPolygon(QPolygonF([QPointF(5, 29), QPointF(26, 21),
                                      QPointF(43, 29), QPointF(22, 39)]))
        painter.drawLine(24, 29, 24, 7)
        painter.drawLine(24, 7, 18, 13)
        painter.drawLine(24, 7, 30, 13)
        painter.end()
        return QIcon(pixmap)

    def build_sidebar(self, parent):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(_label(
            '在主控选点后，写入对应槽位。', 'mutedNote'))
        controls = []
        self.slot_labels = []
        self.slot_groups = []
        self.trace_buttons = []
        for index, title in enumerate(SLOTS):
            group = QGroupBox(title)
            group.setObjectName('layerCard')
            box = QVBoxLayout(group)
            status = _label('尚未写入')
            status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            write = QPushButton('写入当前数据')
            write.clicked.connect(lambda checked=False, i=index: self.write_slot(i))
            trace = QPushButton('查看写入参数')
            trace.setEnabled(False)
            trace.clicked.connect(lambda checked=False, i=index: self.show_trace(i))
            for widget in (status, write, trace):
                box.addWidget(widget)
            self.slot_groups.append(group)
            self.slot_labels.append(status)
            self.trace_buttons.append(trace)
            controls.extend((write, trace))
            layout.addWidget(group)
        self.reject_outliers = QCheckBox('剔除平面异常点')
        self.reject_outliers.setToolTip('按到拟合平面的正交距离筛查，适用于至少 8 个不同测点。')
        self.reject_outliers.toggled.connect(self._filter_changed)
        layout.addWidget(self.reject_outliers)
        self.fit_button = QPushButton('拟合并比较')
        self.fit_button.setObjectName('accentBtn')
        self.fit_button.clicked.connect(self.compute)
        self.fit_button.setEnabled(False)
        self.clear_button = QPushButton('清空槽位')
        self.clear_button.clicked.connect(self.clear)
        self.clear_button.setEnabled(False)
        self.message = _label('', 'mutedNote')
        for widget in (self.fit_button, self.clear_button, self.message):
            layout.addWidget(widget)
        layout.addStretch()
        self.host.register_task_controls((*controls, self.fit_button, self.clear_button, self.reject_outliers))
        QVBoxLayout(parent).addWidget(_scroll(content))
        return parent

    def build_panel(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        self.workflow = QTabBar()
        self.workflow.setAccessibleName('三坐标分析步骤')
        self.workflow.setExpanding(False)
        for step in ('1  测量特征', '2  建立基准', '3  比较结果'):
            self.workflow.addTab(step)
        layout.addWidget(self.workflow)
        summary = QWidget()
        summary.setObjectName('metricSurface')
        metrics = QGridLayout(summary)
        self.coordinate_frame = QComboBox()
        self.coordinate_frame.addItem('原坐标系', 'source')
        self.coordinate_frame.addItem('基准面 A 坐标系', 'base')
        self.coordinate_frame.addItem('三点建基准 · 测 B', 'points')
        self.coordinate_frame.addItem('两面定基准 · 测 C', 'two_planes')
        frame_label = _label('比较坐标系')
        frame_label.setBuddy(self.coordinate_frame)
        metrics.addWidget(frame_label, 0, 0)
        metrics.addWidget(self.coordinate_frame, 0, 1)
        self.reference_axis = QComboBox()
        self.reference_axis.addItem('自动（投影最长的轴）', None)
        for axis, name in enumerate('XYZ'):
            self.reference_axis.addItem(f'原 +{name} 轴', axis)
        self.reference_label = _label('A 面内 X′ 参考轴')
        self.reference_label.setBuddy(self.reference_axis)
        metrics.addWidget(self.reference_label, 1, 0)
        metrics.addWidget(self.reference_axis, 1, 1)
        self.point_order = QComboBox()
        self.point_order_label = _label('左底点 / 右底点 / 顶点')
        self.point_order_label.setBuddy(self.point_order)
        metrics.addWidget(self.point_order_label, 2, 0)
        metrics.addWidget(self.point_order, 2, 1)
        self.frame_policy = QComboBox()
        for label, policy in (('两面等权', 'equal'), ('以 A 的法向为 X′', 'x'),
                              ('以 B 的法向为 Y′', 'y')):
            self.frame_policy.addItem(label, policy)
        self.frame_policy_label = _label('两面定轴规则')
        self.frame_policy_label.setBuddy(self.frame_policy)
        metrics.addWidget(self.frame_policy_label, 1, 0)
        metrics.addWidget(self.frame_policy, 1, 1)
        self.feature_note = _label('', 'mutedNote')
        self.basis_note = _label('', 'mutedNote')
        indicators = QHBoxLayout()
        values = []
        titles = []
        for title in ('ΔRx (°)', 'ΔRy (°)', '二面角 (°)', 'AB 面夹角 (°)'):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(2, 0, 2, 0)
            cell_layout.setSpacing(2)
            label = _label(title)
            value = _label('—', 'metricValueAccent')
            for widget in (label, value):
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(label)
            cell_layout.addWidget(value)
            indicators.addWidget(cell, 1)
            values.append(value)
            titles.append(label)
        self.delta_values = values[:2]
        self.delta_titles = titles[:2]
        self.dihedral_value, self.ab_angle_value = values[2:]
        self.result_strip = QWidget()
        self.result_strip.setObjectName('metricSurface')
        result_layout = QVBoxLayout(self.result_strip)
        result_layout.addLayout(indicators)
        layout.addWidget(self.result_strip)
        self.comparison_note = _label('写入测面后执行拟合。Δ 表示测量面 B 相对基准面 A。', 'mutedNote')
        layout.addWidget(self.comparison_note)

        views = QHBoxLayout()
        self.view_choice = QComboBox()
        self.view_choice.setAccessibleName('绘图区视图')
        self.view_choice.addItems(('3D 点云', 'XY 投影', 'XZ 投影', 'YZ 投影', '法向残差'))
        self.view_choice.setToolTip('滚轮缩放。3D 视图可拖动旋转。')
        reset_view = QPushButton('复位视图')
        reset_view.clicked.connect(self._reset_view)
        details_button = QPushButton('结果与说明')
        views.addWidget(self.view_choice)
        views.addWidget(reset_view)
        views.addStretch()
        views.addWidget(details_button)
        layout.addLayout(views)

        self.figure = Figure(figsize=(6, 2.85))
        self.axes = self.figure.add_subplot(projection='3d', proj_type='ortho', computed_zorder=False)
        self.projections = [self.figure.add_subplot(111, label=view)
                            for view in ('XY', 'XZ', 'YZ')]
        self.residual_axes = self.figure.add_subplot(111, label='residual')
        self.plot_axes = (self.axes, *self.projections, self.residual_axes)
        for axes in self.plot_axes[1:]:
            axes.set_visible(False)
        self._home_limits = {}
        self._zoom_3d = .85
        self.canvas = self.host.make_canvas(self.figure)
        self.canvas.mpl_connect('scroll_event', self._scroll_plot)
        self.canvas.mpl_connect('button_press_event', self._pick_point)
        self.view_choice.currentIndexChanged.connect(self._show_view)
        self.canvas.setMinimumSize(270, 285)
        self.scene = QStackedWidget()
        self.scene.addWidget(self.canvas)
        self.scene_note = _label('', 'mutedNote')
        self.scene_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scene.addWidget(self.scene_note)
        workspace = QHBoxLayout()
        workspace.setSpacing(10)
        workspace.addWidget(self.scene, 1)
        self.inspector = QStackedWidget()
        self.inspector.setObjectName('metricSurface')
        self.inspector.setMinimumWidth(218)
        self.inspector.setMaximumWidth(310)
        workspace.addWidget(self.inspector)
        layout.addLayout(workspace, 1)

        feature_page = QWidget()
        feature_layout = QVBoxLayout(feature_page)
        feature_layout.addWidget(_label('测点与残差', 'stepTitle'))
        self.inspect_slot = QComboBox()
        self.inspect_slot.setAccessibleName('要检查的测面')
        for name in ('A 面', 'B 面', 'C 面'):
            self.inspect_slot.addItem(name)
        feature_layout.addWidget(self.inspect_slot)
        self.feature_quality = _label('', 'mutedNote')
        feature_layout.addWidget(self.feature_quality)
        self.residual_button = QPushButton('查看法向残差')
        self.residual_button.clicked.connect(lambda: self.view_choice.setCurrentIndex(4))
        feature_layout.addWidget(self.residual_button)
        feature_layout.addStretch()
        self.inspector.addWidget(feature_page)

        basis_page = QWidget()
        basis_layout = QVBoxLayout(basis_page)
        basis_layout.addWidget(_label('坐标系定义', 'stepTitle'))
        basis_layout.addWidget(summary)
        self.pick_points_button = QPushButton('在投影中指定三个点')
        self.pick_points_button.clicked.connect(self._start_point_pick)
        self._picked_points = []
        self._picking_points = False
        basis_layout.addWidget(self.pick_points_button)
        self.basis_preview = _label('', 'mutedNote')
        basis_layout.addWidget(self.basis_preview)
        basis_layout.addStretch()
        self.inspector.addWidget(basis_page)

        result_page = QWidget()
        comparison_layout = QVBoxLayout(result_page)
        comparison_layout.addWidget(_label('基准与质量', 'stepTitle'))
        self.result_quality = _label('', 'mutedNote')
        comparison_layout.addWidget(self.result_quality)
        comparison_layout.addStretch()
        self.inspector.addWidget(result_page)

        results = QWidget()
        results.setObjectName('metricSurface')
        grid = QGridLayout(results)
        grid.setHorizontalSpacing(16)
        self.values = [[], [], []]
        self.table_headers = []
        for col, title in enumerate(SLOTS, 1):
            header = _label(title, 'stepTitle')
            self.table_headers.append(header)
            grid.addWidget(header, 0, col)
            grid.setColumnStretch(col, 1)
        for row, name in enumerate(METRICS, 1):
            grid.addWidget(_label(name, 'mutedNote'), row, 0)
            for col in range(len(SLOTS)):
                value = _label('—')
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(value, row, col + 1)
                self.values[col].append(value)
        self.quality = _label('', 'mutedNote')
        grid.addWidget(self.quality, len(METRICS) + 1, 0, 1, 4)
        self.details = QDialog(content)
        self.details.setWindowTitle('通用三维拟合 · 结果与说明')
        self.details.resize(660, 520)
        details_layout = QVBoxLayout(self.details)
        details_content = QWidget()
        detail_items = QVBoxLayout(details_content)
        self.comparison_detail = _label('', 'mutedNote')
        for widget in (results, self.comparison_detail, self.feature_note, self.basis_note):
            detail_items.addWidget(widget)
        detail_items.addWidget(_label(
            '各槽须来自同一次 XYZ 点表映射。在主控选择原始 Z、关闭滤波，使用矩形 ROI 选点后写入。\n'
            '绘图区、法向量和单面结果采用原坐标系。滚轮缩放，3D 视图可拖动旋转。'
            '二面角为无向平面的最小夹角（0°–90°）。两面定基准时，二面角为 C 与基准 X′Y′ 的夹角，'
            '另显示两个参考面的 AB 夹角。\n'
            f'{ANGLE_DEFINITION}\n{NORMAL_DEFINITION}\n{QUALITY_DEFINITION}', 'mutedNote'))
        detail_items.addStretch()
        details_layout.addWidget(_scroll(details_content))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.details.reject)
        details_layout.addWidget(close)
        details_button.clicked.connect(self.details.show)
        self.coordinate_frame.currentIndexChanged.connect(self._select_frame)
        self.reference_axis.currentIndexChanged.connect(self._render_comparison)
        self.point_order.currentIndexChanged.connect(self._change_point_order)
        self.frame_policy.currentIndexChanged.connect(self._change_frame)
        self.host.register_task_controls((self.coordinate_frame, self.reference_axis,
                                          self.point_order, self.frame_policy))
        self.workflow.currentChanged.connect(self._select_step)
        self.inspect_slot.currentIndexChanged.connect(self._update_inspector)
        self._refresh_point_orders()
        self._change_frame()
        self._select_step(0)
        return _scroll(content)

    def _refresh_slots(self):
        for label, trace, slot in zip(self.slot_labels, self.trace_buttons, self.slots):
            if slot is None:
                label.setText('尚未写入')
            else:
                roi = '、'.join(shape.get('view', 'XY') for shape in slot['roi']['shapes']) or '关闭'
                deleted = len(slot['manual_deletion']['deleted_point_indices'])
                label.setText(f"{slot['source_name']}\n{len(slot['xyz']):,} 点 · {slot['unit']}\n"
                              f"ROI {roi} · 删点 {deleted:,} · 滤波关闭")
            trace.setEnabled(slot is not None)
        self.fit_button.setEnabled(any(slot is not None for slot in self.slots))
        self.clear_button.setEnabled(self.fit_button.isEnabled())
        self.host.refresh_module_chrome()

    def write_slot(self, index):
        try:
            record = self.host.current_xyz_selection_snapshot()
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        slots = list(self.slots)
        slots[index] = record
        self.slots = tuple(slots)
        self.result = None
        if index in (0, 2):
            self.point_orders.pop(index, None)
            if index == (2 if self.coordinate_frame.currentData() == 'two_planes' else 0):
                self._refresh_point_orders()
        self.message.setText(f'已写入{SLOTS[index]}，请执行拟合。')
        self._refresh_slots()
        self.render()

    def clear(self):
        self.slots = (None, None, None)
        self.point_orders = {}
        self.result = None
        self._refresh_point_orders()
        self.message.clear()
        self._refresh_slots()
        self.render()

    def _filter_changed(self):
        if self.result is not None:
            self.result = None
            self.message.setText('滤波选项已改变，请重新拟合。')
            self.render()
            self.host.refresh_module_chrome()

    def compute(self):
        snapshots = self.slots
        use_filter = self.reject_outliers.isChecked()

        def work(progress, cancelled):
            fits, errors, filters = [], [], []

            def check_cancel():
                if cancelled.is_set():
                    raise TaskCancelled()
            for index, slot in enumerate(snapshots):
                if cancelled.is_set():
                    raise TaskCancelled()
                progress(15 + index * 25, f'处理{SLOTS[index]}')
                fit = error = trace = None
                if slot is not None:
                    try:
                        if use_filter:
                            fit, trace = fit_plane_with_outlier_rejection(slot['xyz'], check_cancel)
                            trace['rejected_point_indices'] = slot['point_indices'][trace['rejected_positions']]
                        else:
                            fit = fit_plane(slot['xyz'])
                    except (ValueError, np.linalg.LinAlgError) as exc:
                        error = str(exc)
                fits.append(fit)
                errors.append(error)
                filters.append(trace)
            if cancelled.is_set():
                raise TaskCancelled()
            common_frame = all(slot is not None for slot in snapshots[:2]) and (
                snapshots[0]['frame_id'] == snapshots[1]['frame_id'])
            deltas = (None, None)
            if common_frame and all(fits[:2]):
                a, b = fits[:2]
                deltas = (angle_difference(b.rx_deg, a.rx_deg),
                          angle_difference(b.ry_deg, a.ry_deg))
            return dict(snapshots=snapshots, fits=tuple(fits), errors=tuple(errors),
                        deltas=deltas, common_frame=common_frame, filters=tuple(filters))

        def accept(result):
            if self.slots is snapshots:
                self.result = result
                self.message.setText('拟合已结束。')
                self.render()
                self.host.status('通用三维拟合已完成。')
                self.host.refresh_module_chrome()

        self.host.run_task('通用三维拟合', work, accept,
                           on_error=self.message.setText,
                           on_cancel=lambda: self.message.setText('拟合已取消。'))

    def render(self):
        result = self.result
        fits = result['fits'] if result else (None, None, None)
        for values, fit in zip(self.values, fits):
            texts = ('—',) * len(METRICS)
            if fit is not None:
                texts = (
                    ',  '.join(f'{value:.6f}' for value in fit.normal),
                    f'{fit.offset_mm:.7g}', _angle(fit.rx_deg, fit.sensitive_angles[0]),
                    _angle(fit.ry_deg, fit.sensitive_angles[1]),
                    f'{len(fit.signed_distances_mm):,}', f'{fit.rms_mm * 1000:.3f}',
                    f'{fit.max_abs_mm * 1000:.3f}',
                )
            for label, text in zip(values, texts):
                label.setText(text)

        notes = []
        if result:
            for title, fit, error, trace in zip(SLOTS, fits, result['errors'], result['filters']):
                if title == SLOTS[2] and self.coordinate_frame.currentData() != 'two_planes':
                    continue
                if error:
                    notes.append(f'{title}拟合失败 · {error}')
                elif fit and fit.warnings:
                    notes.append(f'{title} · ' + ' '.join(fit.warnings))
                if trace:
                    rejected = trace['rejected_point_indices']
                    if trace['note']:
                        notes.append(f"{title} · {trace['note']}")
                    else:
                        before_rx, before_ry = trace['original_rx_ry_deg']
                        notes.append(
                            f"{title} · 保留 {len(fit.signed_distances_mm):,} 点，剔除 {len(rejected):,} 点。"
                            f"原 Rx／Ry {_angle(before_rx)}°／{_angle(before_ry)}°，"
                            f"原 RMS {trace['original_rms_mm'] * 1000:.3f} µm。")
                        if len(rejected):
                            shown = '、'.join(f'#{index}' for index in rejected[:20])
                            notes.append(f"剔除点号 {shown}" + ('… 完整列表见 JSON' if len(rejected) > 20 else ''))
        self.quality.setText('\n'.join(notes))
        self.quality.setVisible(bool(notes))
        self._render_comparison()
        self._plot()
        self._update_inspector()

    def _select_step(self, step):
        self.inspector.setCurrentIndex(step)
        self.result_strip.setVisible(step == 2)
        if step != 1:
            self._picking_points = False
            self.pick_points_button.setText('在投影中指定三个点')
        if step == 1 and self.coordinate_frame.currentData() in ('points', 'two_planes'):
            self.view_choice.setCurrentIndex(1)
        elif step == 2:
            self.view_choice.setCurrentIndex(0)

    def _update_inspector(self):
        index = self.inspect_slot.currentIndex()
        slot = self.slots[index]
        fit = self.result['fits'][index] if self.result else None
        note = '在左侧写入测点，再执行拟合。'
        if fit is not None:
            count = len(fit.signed_distances_mm)
            trace = self.result['filters'][index]
            note = (f'输入 {len(slot["xyz"]):,} 点 · 保留 {count:,} 点\n'
                    f'RMS {fit.rms_mm * 1000:.3f} µm\n最大偏差 {fit.max_abs_mm * 1000:.3f} µm')
            if trace and trace['note']:
                note += '\n' + trace['note']
            elif trace:
                note += f"\n剔除前 RMS {trace['original_rms_mm'] * 1000:.3f} µm"
        elif slot is not None:
            note = f'已写入 {len(slot["xyz"]):,} 点，请执行拟合。'
        self.feature_quality.setText(note)
        self.residual_button.setEnabled(fit is not None)
        self.basis_preview.setText(self.comparison_detail.text())
        point_slot = self._basis_point_slot()
        self.pick_points_button.setVisible(point_slot is not None and len(point_slot['xyz']) == 3)
        quality = []
        if self.result:
            count = 3 if self.coordinate_frame.currentData() == 'two_planes' else 2
            for name, face in zip(SLOTS[:count], self.result['fits']):
                if face:
                    quality.append(f'{name} · RMS {face.rms_mm * 1000:.3f} µm')
        self.result_quality.setText('\n'.join(quality) or '拟合后查看各测面的残差质量。')
        if self.view_choice.currentIndex() == 4:
            self._draw_residual()
            self.canvas.draw_idle()

    def _basis_point_slot(self):
        mode = self.coordinate_frame.currentData()
        if mode == 'points':
            return self.slots[0]
        if mode == 'two_planes':
            return self.slots[2]
        return None

    def _start_point_pick(self):
        self._picking_points = not self._picking_points
        self._picked_points = []
        self.pick_points_button.setText('点选底边起点（再按此处取消）' if self._picking_points
                                        else '在投影中指定三个点')
        if self._picking_points:
            self.view_choice.setCurrentIndex(1)

    def _pick_point(self, event):
        view = self.view_choice.currentIndex()
        if not self._picking_points or not 1 <= view <= 3 or event.button != 1:
            return
        slot = self._basis_point_slot()
        if slot is None or len(slot['xyz']) != 3 or event.inaxes is not self.projections[view - 1]:
            return
        columns = ((0, 1), (0, 2), (1, 2))[view - 1]
        positions = event.inaxes.transData.transform(slot['xyz'][:, columns])
        distances = np.linalg.norm(positions - (event.x, event.y), axis=1)
        eligible = [i for i, distance in enumerate(distances) if distance <= 14]
        if len(eligible) != 1 or eligible[0] in self._picked_points:
            self.basis_preview.setText('请选择一个尚未分配、可辨认的点。点重叠时可切换投影。')
            return
        self._picked_points.append(eligible[0])
        if len(self._picked_points) == 3:
            order = tuple(self._picked_points)
            self._picking_points = False
            self.pick_points_button.setText('在投影中指定三个点')
            for i in range(self.point_order.count()):
                if self.point_order.itemData(i) == order:
                    self.point_order.setCurrentIndex(i)
                    break
        else:
            role = ('', '底边终点', '顶点')[len(self._picked_points)]
            self.pick_points_button.setText(f'点选{role}（再按此处取消）')
            ids = slot['point_indices'][self._picked_points]
            self.basis_preview.setText('已选 ' + ' → '.join(f'#{point}' for point in ids))

    def _draw_residual(self):
        axes = self.residual_axes
        axes.clear()
        index = self.inspect_slot.currentIndex()
        fit = self.result['fits'][index] if self.result else None
        if fit is None:
            self._home_limits.pop(axes, None)
            axes.text(.5, .5, '选择已拟合的测面', transform=axes.transAxes,
                      ha='center', va='center')
            return
        xyz = self.slots[index]['xyz']
        trace = self.result['filters'][index]
        retained = trace['retained_positions'] if trace else np.arange(len(xyz))
        rejected = trace['rejected_positions'] if trace else ()
        centered = xyz - fit.centroid_mm
        plane = centered @ fit.tangent_basis.T
        dist = centered @ fit.normal * 1000
        shown = retained[np.linspace(0, len(retained) - 1, min(len(retained), 3500), dtype=int)]
        size = 10 + 25 * np.minimum(np.abs(dist[shown]) / max(fit.rms_mm * 1000, 1e-6), 4) / 4
        axes.scatter(*plane[shown].T, s=size, c=np.where(dist[shown] < 0, '#287abd', '#279260'))
        if len(rejected):
            axes.scatter(*plane[rejected[:3500]].T, s=28, marker='x', color='#aa3928')
        axes.set(xlabel='面内 U (mm)', ylabel='面内 V (mm)', aspect='equal')
        axes.set_title('蓝 −    绿 +    × 剔除点', loc='left', fontsize=10)
        axes.grid(True, alpha=.2)
        self._home_limits[axes] = (axes.get_xlim(), axes.get_ylim())

    def _refresh_point_orders(self):
        self._picking_points = False
        self.pick_points_button.setText('在投影中指定三个点')
        self.point_order.blockSignals(True)
        self.point_order.clear()
        self.point_order.addItem('请选择三个点的顺序', None)
        index = 2 if self.coordinate_frame.currentData() == 'two_planes' else 0
        slot = self.slots[index]
        selected = self.point_orders.get(index)
        if slot is not None and len(slot['xyz']) == 3:
            ids = slot['point_indices']
            for order in POINT_ORDERS:
                self.point_order.addItem(' / '.join(f'#{ids[i]}' for i in order), order)
                if order == selected:
                    self.point_order.setCurrentIndex(self.point_order.count() - 1)
        self.point_order.blockSignals(False)

    def _change_point_order(self):
        index = 2 if self.coordinate_frame.currentData() == 'two_planes' else 0
        self.point_orders[index] = self.point_order.currentData()
        self._change_frame()

    def _select_frame(self):
        self._refresh_point_orders()
        self._change_frame()

    def _change_frame(self):
        mode = self.coordinate_frame.currentData()
        use_third = mode == 'two_planes'
        self.slot_groups[0].setTitle({
            'points': '基准 A · 三个定位点', 'two_planes': '基准面 A · X′ 参考',
        }.get(mode, SLOTS[0]))
        self.slot_groups[1].setTitle('基准面 B · Y′ 参考' if use_third else SLOTS[1])
        self.slot_groups[2].setVisible(use_third)
        for header, group in zip(self.table_headers, self.slot_groups):
            header.setText(group.title())
        self.table_headers[2].setVisible(use_third)
        for label in self.values[2]:
            label.setVisible(use_third)
        self.render()
        self.host.refresh_module_chrome()

    def _comparison(self, result):
        mode = self.coordinate_frame.currentData()
        axis = self.reference_axis.currentData()
        comparison = {
            'frame': mode,
            'requested_reference_axis': ('auto' if axis is None else 'XYZ'[axis]) if mode == 'base' else None,
            'delta_rx_ry_deg': (None, None),
            'sensitive_angles': (False, False),
            'warnings': (),
            'error': None,
            'ready': False,
        }
        a, b, c = result['fits'] if result else (None, None, None)
        if a and b and result['common_frame']:
            angle = plane_angle_deg(a.normal, b.normal)
            comparison['ab_plane_angle_deg'] = angle
            comparison['ab_perpendicularity_error_deg'] = 90 - angle
        if mode == 'points':
            slot, measure = self.slots[:2]
            order = self.point_order.currentData()
            if slot is None or len(slot['xyz']) != 3 or order is None:
                comparison['error'] = '将恰好三个定位点写入 A，再按图中点号依次选择左底点、右底点、顶点。'
                return comparison
            indices = list(order)
            xyz = slot['xyz'][indices]
            comparison.update(ordered_points_mm=xyz, ordered_point_indices=slot['point_indices'][indices])
            try:
                comparison.update(three_point_frame(xyz))
            except ValueError as exc:
                comparison['error'] = str(exc)
                return comparison
            if measure is not None and measure['frame_id'] != slot['frame_id']:
                comparison['error'] = '定位点与测量面 B 必须来自同一次列映射。'
            elif b and result['common_frame']:
                self._apply_frame_angles(comparison, b)
            return comparison
        if not (a and b and result['common_frame']):
            return comparison
        try:
            if mode == 'two_planes':
                policy = self.frame_policy.currentData()
                comparison['axis_policy'] = policy
                comparison.update(two_plane_frame(a.normal, b.normal, policy))
                slot = self.slots[2]
                if slot is not None and slot['frame_id'] != self.slots[0]['frame_id']:
                    comparison['error'] = '三个槽必须来自同一次列映射。'
                elif c:
                    self._apply_frame_angles(comparison, c)
                    order = self.point_order.currentData()
                    if len(slot['xyz']) == 3 and order is not None:
                        ids = list(order)
                        try:
                            target = three_point_frame(slot['xyz'][ids])
                        except ValueError as exc:
                            comparison['warnings'].append(str(exc))
                        else:
                            matrix, angle = frame_rotation(comparison['basis_rows_in_source'],
                                                           target['basis_rows_in_source'])
                            comparison['target_point_frame'] = {
                                **target, 'ordered_point_indices': slot['point_indices'][ids],
                                'ordered_points_mm': slot['xyz'][ids],
                            }
                            comparison['target_axes_in_reference'] = matrix
                            comparison['frame_rotation_deg'] = angle
                            comparison['warnings'].extend(target['warnings'])
            elif mode == 'base':
                comparison.update(compare_in_base_frame(a, b, axis), ready=True)
            else:
                comparison.update(
                    delta_rx_ry_deg=result['deltas'],
                    sensitive_angles=tuple(a.sensitive_angles[i] or b.sensitive_angles[i]
                                           for i in range(2)), ready=True)
        except ValueError as exc:
            comparison['error'] = str(exc)
        return comparison

    @staticmethod
    def _apply_frame_angles(comparison, fit):
        angles = normal_angles_in_frame(fit.normal, comparison['basis_rows_in_source'])
        angles['warnings'] = [*comparison['warnings'], *angles['warnings']]
        comparison.update(angles, ready=True)

    def _render_comparison(self):
        result = self.result
        comparison = self._comparison(result)
        mode = comparison['frame']
        point_slot = self.slots[2] if mode == 'two_planes' else self.slots[0]
        ordered_target = mode == 'two_planes' and point_slot is not None and len(point_slot['xyz']) == 3
        for visible, widgets in (
                (mode == 'base', (self.reference_label, self.reference_axis)),
                (mode == 'points' or ordered_target, (self.point_order_label, self.point_order)),
                (mode == 'two_planes', (self.frame_policy_label, self.frame_policy))):
            for widget in widgets:
                widget.setVisible(visible)
        for label, axis in zip(self.delta_titles, 'xy'):
            label.setText(f'{"R" if mode in ("points", "two_planes") else "ΔR"}{axis} (°)')
        angle = comparison.get('ab_plane_angle_deg')
        feature = ('' if angle is None else
                   f'A／B 面夹角 {angle:.4f}° · 偏离直角 {90 - angle:.4f}°')
        if 'total_tilt_deg' in comparison:
            feature += f" · 总倾角 {comparison['total_tilt_deg']:.4f}°"
        if 'frame_rotation_deg' in comparison:
            feature += f" · C 三点坐标系相对基准的旋转角 {comparison['frame_rotation_deg']:.4f}°"
        if (mode == 'points' or ordered_target) and point_slot is not None and len(point_slot['xyz']) == 3:
            coordinates = 'XYZ (mm) · ' + ' / '.join(
                f"#{index} ({', '.join(f'{value:.6g}' for value in point)})"
                for index, point in zip(point_slot['point_indices'], point_slot['xyz']))
            feature = '\n'.join(filter(None, (feature.lstrip(' ·'), coordinates)))
        self.feature_note.setText(feature)
        self.feature_note.setVisible(bool(feature))
        basis = comparison.get('basis_rows_in_source')
        axes_text = '' if basis is None or mode != 'two_planes' else '源坐标中的基准轴 · ' + '   '.join(
            f"{axis}′ ({', '.join(f'{value + 0.:.4f}' for value in vector)})"
            for axis, vector in zip('XYZ', basis))
        self.basis_note.setText(axes_text)
        self.basis_note.setVisible(bool(axes_text))
        if comparison['error']:
            note = comparison['error']
        elif result and all(result['snapshots'][:2]) and not result['common_frame']:
            note = 'A、B 来自不同的列映射。请从同一次映射重写。'
        elif mode == 'points':
            note = '底边中点为原点 · 红 X′、绿 Y′、蓝 Z′。拟合测量面 B 后，读取其相对 X′Y′ 的倾角。'
        elif mode == 'two_planes':
            note = '用 A、B 的约定法向定 X′、Y′。拟合测量面 C 后，读取其相对 X′Y′ 的倾角。'
        elif not comparison['ready']:
            note = 'Δ 需要两份成功拟合的测面。'
        elif mode == 'base':
            reference = comparison['resolved_reference_axis']
            note = (f'A 面坐标系 · X′ 取原 +{reference} 轴在 A 面内的投影，Z′ 沿 A 法向。'
                    'A 的 Rx、Ry 均为 0°，上方 Δ 为 B 在此坐标系中的投影倾角。')
        else:
            note = '原坐标系 · 测量面 B 相对基准面 A · 最短有符号差（180° 周期）'
        if comparison['warnings']:
            note += ' ' + ' '.join(comparison['warnings'])
        self.comparison_detail.setText(note)
        status = (comparison['error'] or
                  ('部分角度不可定义或对测点噪声敏感，详见结果与说明。' if comparison['warnings'] else '') or
                  ('选择点号时可切换 XY／XZ／YZ 投影。' if mode == 'points' and not comparison['ready'] else ''))
        if not status and result and all(result['snapshots'][:2]) and not result['common_frame']:
            status = 'A、B 来自不同的列映射。'
        if not status and result and any(trace is not None for trace in result['filters']):
            count = 3 if mode == 'two_planes' else 2
            removed = sum(len(trace['rejected_positions']) for trace in result['filters'][:count] if trace)
            status = f'异常点剔除 {removed:,} 点 · × 标示剔除点，详见结果与说明。'
        self.comparison_note.setText(status)
        self.comparison_note.setVisible(bool(status))
        dihedral = comparison.get('total_tilt_deg') if mode in ('points', 'two_planes') else angle
        self.dihedral_value.setText('—' if dihedral is None else f'{dihedral:.3f}')
        self.dihedral_value.setToolTip('C 与基准 X′Y′ 的夹角' if mode == 'two_planes' else
                                        'B 与三点基准 X′Y′ 的夹角' if mode == 'points' else 'A 与 B 的夹角')
        self.ab_angle_value.setText('—' if angle is None else f'{angle:.3f}')
        self.ab_angle_value.parentWidget().setVisible(mode == 'two_planes')
        for label, delta, sensitive in zip(self.delta_values, comparison['delta_rx_ry_deg'],
                                           comparison['sensitive_angles']):
            label.setToolTip('')
            if comparison['ready'] and not comparison['error']:
                label.setText(_angle(delta, sensitive))
                if delta is None:
                    label.setToolTip('相关法向分量在数值容差内均为零。')
            else:
                label.setText('—')

    def _plot(self):
        comparison = self._comparison(self.result)
        point_frame = comparison.get('target_point_frame', comparison)
        points = point_frame.get('ordered_points_mm') if 'basis_rows_in_source' in point_frame else None
        mode = comparison['frame']
        point_slot = self.slots[0] if mode == 'points' else self.slots[2] if mode == 'two_planes' else None
        candidates = point_slot is not None and len(point_slot['xyz']) == 3
        if (self.result is None or not any(self.result['fits'])) and not candidates:
            self.scene_note.setText('拟合后的点集、平面及法向量将在此显示')
            self.scene.setCurrentWidget(self.scene_note)
            return
        if self.result and all(self.result['fits'][:2]) and not self.result['common_frame'] and not candidates:
            self.scene_note.setText('共同坐标图需要同一次映射的两份快照')
            self.scene.setCurrentWidget(self.scene_note)
            return
        ax = self.axes
        ax.clear()
        self._home_limits.clear()
        for projection in self.projections:
            projection.clear()
        self.scene.setCurrentWidget(self.canvas)
        ax.set_proj_type('ortho')
        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.tick_params(labelsize=8)
        count = 3 if comparison['frame'] == 'two_planes' else 2
        result_fits = self.result['fits'] if self.result else ()
        if candidates:
            result_fits = tuple(
                fit if slot is not None and slot['frame_id'] == point_slot['frame_id'] else None
                for slot, fit in zip(self.slots, result_fits))
        rows = [(title, color, slot, fit) for title, color, slot, fit in
                zip(SLOTS[:count], COLORS, self.slots, result_fits) if fit is not None
                and (title != SLOTS[2] or comparison['error'] is None)]
        extents = []
        samples = []
        point_annotations = []
        if candidates:
            candidate_xyz = point_slot['xyz']
            ax.scatter(*candidate_xyz.T, s=26, color='#287abd', depthshade=False, zorder=6)
            order = self.point_order.currentData()
            roles = dict(zip(order, 'ABC')) if order is not None else {}
            for i, (index, point) in enumerate(zip(point_slot['point_indices'], candidate_xyz)):
                label = f'#{index}'
                if i in roles:
                    label += f' · {roles[i]}'
                ax.text(*point, label, fontsize=10, fontweight='bold', zorder=7)
                point_annotations.append((point, label))
            extents.extend(candidate_xyz)
        if points is not None:
            triangle = points[[0, 1, 2, 0]]
            ax.plot(*triangle.T, color='#64748b')
            origin = point_frame['origin_mm_in_source']
            size = np.linalg.norm(points[1] - points[0]) * .45
            for name, color, direction in zip(('X′', 'Y′', 'Z′'),
                                                ('#dd4242', '#279260', '#287abd'),
                                                point_frame['basis_rows_in_source']):
                arrow = direction * size
                ax.quiver(*origin, *arrow, color=color)
                if comparison['frame'] == 'points':
                    ax.text(*(origin + arrow), name, color=color)
                extents.append(origin + arrow)
        if not rows and not extents:
            self.scene_note.setText('拟合后的点集、平面及法向量将在此显示')
            self.scene.setCurrentWidget(self.scene_note)
            return
        rejected_samples = []
        for title, color, slot, fit in rows:
            xyz = slot['xyz']
            trace = self.result['filters']['ABC'.index(title[-1])]
            retained = xyz
            if trace and len(trace['rejected_positions']):
                rejected = xyz[trace['rejected_positions']]
                outliers = rejected[np.linspace(0, len(rejected) - 1, min(len(rejected), 3500), dtype=int)]
                rejected_samples.append((outliers, color))
                ax.scatter(*outliers.T, s=20, marker='x', color=color, depthshade=False, zorder=5)
                extents.extend((rejected.min(axis=0), rejected.max(axis=0)))
                retained = xyz[trace['retained_positions']]
            sample = retained[np.linspace(0, len(retained) - 1, min(len(retained), 3500), dtype=int)]
            samples.append((sample, color, title[-1]))
            ax.scatter(*sample.T, s=4, color=color, depthshade=False, label=title[-1], zorder=3)
            lo, hi = fit.tangent_bounds_mm
            uv = np.array([[lo[0], lo[1]], [lo[0], hi[1]], [hi[0], lo[1]], [hi[0], hi[1]]])
            patch = fit.centroid_mm + uv @ fit.tangent_basis
            ax.plot_surface(*patch.T.reshape(3, 2, 2), color=color, alpha=.24, shade=False, zorder=2)
            arrow = fit.normal * (max(hi - lo) * .35)
            vectors = ax.quiver(*fit.centroid_mm, *arrow, color=color, linewidth=2.3,
                                arrow_length_ratio=.26, zorder=5)
            vectors.set_path_effects([withStroke(linewidth=4, foreground='#ecedf1')])
            extents.extend((*fit.xyz_bounds_mm, *patch, fit.centroid_mm + arrow))
        lo, hi = np.min(extents, axis=0), np.max(extents, axis=0)
        center = (lo + hi) / 2
        radius = max(float(np.max(hi - lo)) * .58, 1e-6)
        ax.set(xlim=(center[0] - radius, center[0] + radius),
               ylim=(center[1] - radius, center[1] + radius),
               zlim=(center[2] - radius, center[2] + radius))
        ax.set_box_aspect((1, 1, 1), zoom=self._zoom_3d)
        if rows:
            ax.legend(loc='upper left')
        for projection, (i, j) in zip(self.projections, ((0, 1), (0, 2), (1, 2))):
            for sample, color, label in samples:
                projection.scatter(sample[:, i], sample[:, j], s=6, color=color, label=label)
            for outliers, color in rejected_samples:
                projection.scatter(outliers[:, i], outliers[:, j], s=20, marker='x', color=color)
            if candidates:
                projection.scatter(candidate_xyz[:, i], candidate_xyz[:, j], s=26, color='#287abd', zorder=6)
                for point, label in point_annotations:
                    projection.annotate(label, (point[i], point[j]), xytext=(5, 5),
                                        textcoords='offset points')
            projection.set(xlabel=f'{"XYZ"[i]} (mm)', ylabel=f'{"XYZ"[j]} (mm)', aspect='equal')
            projection.grid(True, alpha=.22)
            projection.margins(.14)
            if rows:
                projection.legend(loc='upper left')
            self._home_limits[projection] = (projection.get_xlim(), projection.get_ylim())
        self._home_limits[ax] = (ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d())
        self._show_view()

    def _show_view(self):
        selected = self.view_choice.currentIndex()
        if selected == 4:
            self._draw_residual()
        self.figure.subplots_adjust(left=.10 if selected else .06, right=.94,
                                    bottom=.13 if selected else .06, top=.96)
        for index, axes in enumerate(self.plot_axes):
            axes.set_visible(index == selected)
        self.canvas.draw_idle()

    def _reset_view(self):
        axes = self.plot_axes[self.view_choice.currentIndex()]
        limits = self._home_limits.get(axes)
        if limits is None:
            return
        axes.set_xlim(limits[0])
        axes.set_ylim(limits[1])
        if axes is self.axes:
            axes.set_zlim3d(limits[2])
            axes.view_init(elev=30, azim=-60, roll=0)
            self._zoom_3d = .85
            axes.set_box_aspect((1, 1, 1), zoom=self._zoom_3d)
        self.canvas.draw_idle()

    def _scroll_plot(self, event):
        axes = self.plot_axes[self.view_choice.currentIndex()]
        if axes not in self._home_limits or not event.step:
            return
        step = float(np.clip(event.step, -8, 8))
        if axes is self.axes:
            self._zoom_3d = float(np.clip(self._zoom_3d * 1.18 ** step, .2, 8))
            axes.set_box_aspect((1, 1, 1), zoom=self._zoom_3d)
        elif event.inaxes is axes and event.xdata is not None and event.ydata is not None:
            scale = 1.18 ** -step
            for value, limits, setter in ((event.xdata, axes.get_xlim(), axes.set_xlim),
                                          (event.ydata, axes.get_ylim(), axes.set_ylim)):
                setter(*(value + (bound - value) * scale for bound in limits))
        self.canvas.draw_idle()

    def show_trace(self, index):
        slot = self.slots[index]
        if slot is None:
            return
        summary = {key: value for key, value in slot.items()
                   if key not in ('xyz', 'point_indices', 'reader_index_labels')}
        deletion = summary['manual_deletion']
        summary['manual_deletion'] = {
            'deleted_count': len(deletion['deleted_point_indices']),
            'operations': deletion['operations'],
        }
        summary['有效点数'] = len(slot['xyz'])
        dialog = QDialog(self.canvas.window())
        dialog.setWindowTitle(f'{SLOTS[index]} · 写入参数')
        dialog.resize(610, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def export_actions(self):
        return [('导出拟合结果与点集快照 (JSON)', self.export_result, self.result is not None or
                 'basis_rows_in_source' in self._comparison(None))]

    def export_result(self):
        result = self.result
        comparison = self._comparison(result)
        if result is None:
            if 'basis_rows_in_source' not in comparison:
                return
            result = dict(snapshots=self.slots, fits=(None,) * 3, errors=(None,) * 3,
                          common_frame=False, filters=(None,) * 3)
        chosen = self.host.save_path('导出拟合结果与点集快照', 'xyz-plane-fit.json', 'JSON (*.json)')
        if not chosen:
            return
        target = Path(chosen)

        def work(progress, cancelled):
            payload = {
                'schema_version': 3,
                'comparison': comparison,
                'slot_fit_frame': 'source',
                'definitions': {'angles': ANGLE_DEFINITION, 'normal': NORMAL_DEFINITION,
                                'quality': QUALITY_DEFINITION},
                'common_frame': result['common_frame'],
                'delta_rx_deg': comparison['delta_rx_ry_deg'][0],
                'delta_ry_deg': comparison['delta_rx_ry_deg'][1],
                'point_index_definition': 'point_indices 为主控映射后点集的从零开始位置。'
                                          'reader_index_labels 为导入表索引标签。两者均不代表文件行号。',
                'slots': {
                    key: {'snapshot': record, 'fit': (
                        {field.name: getattr(fit, field.name) for field in fields(fit)} if fit else None),
                          'error': error, 'outlier_filter': trace}
                    for key, record, fit, error, trace in zip(
                        ('A', 'B', 'C'), result['snapshots'], result['fits'], result['errors'], result['filters'])
                },
            }
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                        mode='w', encoding='utf-8', dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    encoder = json.JSONEncoder(ensure_ascii=False, default=_json_default, allow_nan=False)
                    for i, chunk in enumerate(encoder.iterencode(payload)):
                        if i % 8192 == 0 and cancelled.is_set():
                            raise TaskCancelled()
                        handle.write(chunk)
                if cancelled.is_set():
                    raise TaskCancelled()
                temporary.replace(target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        self.host.run_task('导出拟合结果', work,
                           lambda _: self.host.status(f'已导出 {target.name}'),
                           on_error=self.message.setText)

    def status_text(self):
        return '通用三维拟合 · ' + ' · '.join(
            f'{title} {len(slot["xyz"]):,} 点' if slot is not None else f'{title} 未写入'
            for title, slot in zip(SLOTS, self.slots))

    def on_idle(self):
        self._refresh_slots()
