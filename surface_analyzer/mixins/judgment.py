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
            raw = '{}'
            settings = getattr(self, 'settings', None)
            if settings is not None:
                raw = str(settings.value(JUDGMENT_SETTINGS_KEY, '{}') or '{}')
            try:
                spec = normalize_judgment_spec(json.loads(raw))
            except (ValueError, TypeError):
                spec = normalize_judgment_spec({})
            self.judgment_spec = spec
        return spec

    def set_judgment_spec(self, spec, persist=True):
        self.judgment_spec = normalize_judgment_spec(spec)
        if persist and getattr(self, 'settings', None) is not None:
            self.settings.setValue(JUDGMENT_SETTINGS_KEY, json.dumps(self.judgment_spec))
        self._refresh_all_judgments()

    def open_judgment_spec_dialog(self):
        dialog = JudgmentSpecDialog(self, self._judgment_spec())
        if dialog.exec():
            self.set_judgment_spec(dialog.spec())
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
            metrics = getattr(self, 'last_metrics', None) or {}
            return {key: metrics.get(key) for key in ('rx', 'ry', 'pv', 'ttv')}
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
            names = {'rx': 'lbl_rx', 'ry': 'lbl_ry', 'pv': 'lbl_pv', 'ttv': 'lbl_ttv'}
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
        result = self.current_judgment(module, values)
        verdict = result['verdict']
        label.setText(VERDICT_TEXT[verdict])
        label.setStyleSheet(f"color: {VERDICT_COLOR[verdict]};")
        for key, metric_label in self._judgment_metric_labels(module).items():
            metric_label.setStyleSheet(
                f"color: {VERDICT_COLOR['fail']};" if key in result['failed'] else "")
        lines = judgment_lines(result) or ["尚未设置判定规格"]
        card.setToolTip("\n".join(lines + ["", "点击设置判定规格"]))
        return result

    def _refresh_all_judgments(self):
        for module in tuple(getattr(self, '_judgment_widgets', {})):
            self._refresh_judgment(module)
