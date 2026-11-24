"""结果判定卡片：挂在各模块指标条末尾，点击设置规格。"""

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from ..judgment import (
    VERDICT_COLOR, VERDICT_TEXT, JudgmentSpecDialog, judge_measurements, judgment_lines,
    normalize_judgment_spec,
)

JUDGMENT_SETTINGS_KEY = "judgment/spec"


class JudgmentMixin:
    def _judgment_spec(self):
        spec = getattr(self, 'judgment_spec', None)
        if spec is None:
            # Startup is deliberately blank, regardless of the previous session.
            spec = normalize_judgment_spec({})
            self.judgment_spec = spec
        return spec

    def set_judgment_spec(self, spec, persist=True, update_single=True):
        self.judgment_spec = normalize_judgment_spec(spec)
        if persist and getattr(self, 'settings', None) is not None:
            self.settings.setValue(JUDGMENT_SETTINGS_KEY, json.dumps(self.judgment_spec))
        if update_single:
            from ..display_metrics import normalize_single_config
            self.single_metric_config = normalize_single_config({'limits': self.judgment_spec})
            self._layout_single_metric_cards()
        self._refresh_all_judgments()

    def open_judgment_spec_dialog(self):
        dialog = JudgmentSpecDialog(self, self._judgment_spec(), self._single_metric_config())
        if dialog.exec():
            self.set_judgment_spec(dialog.spec(), update_single=False)
            self.set_single_metric_config(dialog.single_config())
            if hasattr(self, '_show_status'):
                self._show_status("判定规格已更新。", 4000)

    def _make_judgment_card(self, module):
        label = QLabel("--")
        card = self._make_metric_card("结果判定", label)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.mousePressEvent = lambda event: self.open_judgment_spec_dialog()
        if not hasattr(self, '_judgment_widgets'):
            self._judgment_widgets = {}
        self._judgment_widgets[module] = (card, label)
        return card

    def _make_extension_judgment_card(self, module, metric_labels, keys):
        if module in ('single', 'gap', 'parallel'):
            raise ValueError("扩展判定模块 key 与内置模块冲突")
        if not hasattr(self, '_extension_judgment_registry'):
            self._extension_judgment_registry = {}
        self._extension_judgment_registry[module] = {
            'keys': tuple(keys),
            'labels': dict(metric_labels),
            'values': {},
        }
        card = self._make_judgment_card(module)
        self._refresh_judgment(module)
        return card

    def _unregister_extension_judgment(self, module):
        getattr(self, '_extension_judgment_registry', {}).pop(module, None)
        getattr(self, '_judgment_widgets', {}).pop(module, None)

    def _refresh_extension_judgment(self, module, values):
        registry = getattr(self, '_extension_judgment_registry', {}).get(module)
        if registry is None:
            raise KeyError(f"未注册扩展判定模块：{module}")
        registry['values'] = dict(values or {})
        return self._refresh_judgment(module, registry['values'])

    def _judgment_values(self, module):
        if module == 'single':
            values = getattr(self, '_single_display_metrics', None)
            if values is not None: return values
            return (getattr(self, 'last_metrics', None) or {}) if getattr(self, 'display_surface_mode', 'raw') == 'raw' else {}
        if module == 'gap':
            payload = getattr(self, 'gap_result', None)
            metrics = (payload or {}).get('plane_metrics') or {}
            return {key: metrics.get(key) for key in ('rx', 'ry', 'pv', 'ttv')}
        extension = getattr(self, '_extension_judgment_registry', {}).get(module)
        if extension is not None:
            return extension['values']
        result = getattr(self, 'parallel_result', None)
        return {'parallel': result.get('angle')} if result else {}

    def _judgment_metric_labels(self, module):
        if module == 'single':
            names = {'rx': 'lbl_rx', 'ry': 'lbl_ry', 'pv': 'lbl_pv', 'ttv': 'lbl_ttv', 'std3': 'lbl_std3', 'mean_z': 'lbl_z'}
        elif module == 'parallel':
            names = {'parallel': 'lbl_par_angle'}
        else:
            names = {}
        labels = {key: getattr(self, name) for key, name in names.items()
                  if hasattr(self, name)}
        extension = getattr(self, '_extension_judgment_registry', {}).get(module)
        if extension is not None:
            labels.update(extension['labels'])
        return labels

    def current_judgment(self, module, values=None):
        if module == 'single':
            from ..display_metrics import judge_single
            return judge_single(self._judgment_values(module) if values is None else values, self._single_metric_config())
        extension = getattr(self, '_extension_judgment_registry', {}).get(module)
        scope = 'parallel' if module == 'parallel' else 'surface'
        if values is None:
            values = self._judgment_values(module)
        keys = extension['keys'] if extension is not None else None
        return judge_measurements(values, self._judgment_spec(), scope, keys=keys)

    def _refresh_judgment(self, module, values=None):
        widgets = getattr(self, '_judgment_widgets', {}).get(module)
        if widgets is None:
            return None
        card, label = widgets
        quality_values = values if values is not None else self._judgment_values(module)
        result = self.current_judgment(module, values)
        verdict = result['verdict']
        quality_values = quality_values or {}
        estimated = bool(quality_values.get('_estimated'))
        quality_warning = quality_values.get('_quality_warning', '')
        label.setText(VERDICT_TEXT[verdict] + (" · 抽样估计" if estimated else ""))
        label.setStyleSheet(f"color: {VERDICT_COLOR[verdict]};")
        for key, metric_label in self._judgment_metric_labels(module).items():
            metric_label.setStyleSheet(
                f"color: {VERDICT_COLOR['fail']};" if key in result['failed'] else "")
        lines = judgment_lines(result) or ["尚未设置判定规格"]
        if quality_warning:
            lines.insert(0, quality_warning)
        card.setToolTip("\n".join(lines + ["", "点击设置判定规格"]))
        return result

    def _refresh_all_judgments(self):
        for module in tuple(getattr(self, '_judgment_widgets', {})):
            self._refresh_judgment(module)

    def _single_metric_config(self):
        from ..display_metrics import normalize_single_config
        if not hasattr(self, 'single_metric_config'):
            self.single_metric_config = normalize_single_config()
        return self.single_metric_config

    def set_single_metric_config(self, config):
        from ..display_metrics import normalize_single_config
        self.single_metric_config = normalize_single_config(config)
        self._layout_single_metric_cards()
        self._refresh_judgment('single')

    def _layout_single_metric_cards(self):
        if not hasattr(self, '_single_cards'):
            return
        visible = self._single_metric_config()['visible']
        active = [card for key, card in self._single_cards.items() if key in visible]
        for key, card in self._single_cards.items():
            card.setVisible(key in visible)
        verdict = self._judgment_widgets['single'][0]
        for card in self._metric_cards: self._metric_grid.removeWidget(card)
        width = self._results_strip.width()
        columns = min(len(active) + 1, max(2, width // 150))
        for col in range(len(self._metric_cards)): self._metric_grid.setColumnStretch(col, 0)
        metric_columns = max(1, columns - 1)
        rows = max(1, (len(active) + metric_columns - 1) // metric_columns)
        for index, card in enumerate(active):
            self._metric_grid.addWidget(card, index // metric_columns, index % metric_columns)
        self._metric_grid.addWidget(verdict, 0, columns - 1, rows, 1)
        for col in range(columns): self._metric_grid.setColumnStretch(col, 1)
        self._metric_columns = columns

    def _refresh_single_display_metrics(self):
        from ..display_metrics import display_metrics
        self._single_display_metrics = None
        if self.last_metrics is None or self._high_order_inputs is None:
            return
        mode = self.display_surface_mode
        order = int(mode.rsplit('_', 1)[1]) if mode != 'raw' else None
        model = self.high_order_models.get(order) if order else None
        if order and model is None:
            for label in (self.lbl_z,self.lbl_pv,self.lbl_ttv,self.lbl_rx,self.lbl_ry,self.lbl_std3):label.setText('--')
            self._refresh_judgment('single', {})
            return
        values = display_metrics(*self._high_order_inputs, mode, model)
        quality = self._current_metric_quality()
        values.update(_estimated=quality['estimated'], _quality_warning=quality.get('warning',''))
        self._single_display_metrics = values
        self._single_cards['pv'].findChildren(QLabel)[0].setText(
            '平面法向 PV (µm)' if mode == 'raw' else '显示残差 PV (µm)')
        self._single_cards['pv'].setToolTip('原始模式：现有最佳拟合平面法向残差极差。' if mode == 'raw' else '当前显示残差 Z 的极差，不再次去平面。')
        approx = '≈' if quality['estimated'] else ''
        for key, name in [('mean_z','lbl_z'),('pv','lbl_pv'),('ttv','lbl_ttv'),('rx','lbl_rx'),('ry','lbl_ry'),('std3','lbl_std3')]:
            label = getattr(self, name)
            label.setText(f"{approx}{values[key]:.6g}")
            label.setToolTip(f"显示面型 {mode}；{key} = {values[key]:.12g}；3σ 使用总体标准差 ddof=0。")
        self._refresh_judgment('single')
