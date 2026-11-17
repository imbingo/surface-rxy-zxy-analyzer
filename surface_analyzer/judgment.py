"""测量结果判定：按规格上限逐项判定，任意一项不合格即整体不合格。"""

import math

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QLabel, QVBoxLayout,
)

# key, 显示名, 单位, 适用范围（surface: 单层/胶厚平面指标；parallel: 平行度）
JUDGMENT_ITEMS = (
    ('rx', '|Rx|', 'µrad', 'surface'),
    ('ry', '|Ry|', 'µrad', 'surface'),
    ('pv', '平面残差 PV', 'µm', 'surface'),
    ('ttv', 'TTV·Z极差', 'µm', 'surface'),
    ('parallel', '平行度·合成夹角', 'µrad', 'parallel'),
)
ABSOLUTE_KEYS = {'rx', 'ry'}
VERDICT_TEXT = {'pass': '合格', 'fail': '不合格', 'pending': '--', 'unset': '未设规格'}
VERDICT_COLOR = {'pass': '#15803d', 'fail': '#b42318', 'pending': '#8a94a0', 'unset': '#8a94a0'}


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def normalize_judgment_spec(data):
    data = data if isinstance(data, dict) else {}
    spec = {}
    for key, *_ in JUDGMENT_ITEMS:
        limit = _finite(data.get(key))
        spec[key] = limit if limit is not None and limit >= 0 else None
    return spec


def judge_measurements(values, spec, scope):
    """返回 {'verdict': pass|fail|pending|unset, 'items': [...], 'failed': [key, ...]}。"""
    spec = normalize_judgment_spec(spec)
    values = values or {}
    items = []
    for key, label, unit, item_scope in JUDGMENT_ITEMS:
        if item_scope != scope or spec[key] is None:
            continue
        value = _finite(values.get(key))
        ok = None
        if value is not None:
            ok = (abs(value) if key in ABSOLUTE_KEYS else value) <= spec[key]
        items.append({'key': key, 'label': label, 'unit': unit,
                      'value': value, 'limit': spec[key], 'ok': ok})
    failed = [item['key'] for item in items if item['ok'] is False]
    if not items:
        verdict = 'unset'
    elif failed:
        verdict = 'fail'
    elif any(item['ok'] is None for item in items):
        verdict = 'pending'
    else:
        verdict = 'pass'
    return {'verdict': verdict, 'items': items, 'failed': failed}


def judgment_lines(result):
    lines = []
    for item in result['items']:
        state = '待测' if item['ok'] is None else '合格' if item['ok'] else '不合格'
        value = '--' if item['value'] is None else f"{item['value']:.3f}"
        lines.append(f"{item['label']}  {value} / ≤ {item['limit']:g} {item['unit']}  {state}")
    return lines


def judgment_summary(result):
    """报告用的一行摘要；未设规格时返回空字符串。"""
    if result['verdict'] == 'unset':
        return ''
    text = f"结果判定  {VERDICT_TEXT[result['verdict']]}"
    if result['failed']:
        labels = [item['label'] for item in result['items'] if item['key'] in result['failed']]
        text += f"（{'、'.join(labels)}）"
    return text


class JudgmentSpecDialog(QDialog):
    """勾选需要判定的指标并填写上限；未勾选的指标不参与判定。"""

    def __init__(self, parent=None, spec=None):
        super().__init__(parent)
        self.setWindowTitle("判定规格")
        spec = normalize_judgment_spec(spec)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        note = QLabel("勾选需要判定的指标并填写上限。任意一项超出即判定不合格；\n"
                      "Rx/Ry 按绝对值判定，平行度按合成夹角判定。")
        note.setObjectName("mutedNote")
        layout.addWidget(note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self._rows = {}
        for row, (key, label, unit, _scope) in enumerate(JUDGMENT_ITEMS):
            check = QCheckBox(label)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1e7)
            spin.setDecimals(3)
            spin.setMinimumWidth(130)
            spin.setPrefix("≤ ")
            spin.setValue(spec[key] if spec[key] is not None else 0.0)
            check.setChecked(spec[key] is not None)
            spin.setEnabled(check.isChecked())
            check.toggled.connect(spin.setEnabled)
            unit_label = QLabel(unit)
            unit_label.setObjectName("mutedNote")
            grid.addWidget(check, row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(unit_label, row, 2)
            self._rows[key] = (check, spin)
        layout.addLayout(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def spec(self):
        return {key: (float(spin.value()) if check.isChecked() else None)
                for key, (check, spin) in self._rows.items()}
