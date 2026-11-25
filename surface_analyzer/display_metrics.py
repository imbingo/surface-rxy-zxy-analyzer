"""Single-layer display/acceptance metrics. Population sigma (ddof=0).

Raw mode retains the existing best-fit-plane normal PV/RMS. Residual modes
measure the displayed residual itself: no second detrend for PV or RMS.
Physical Z stays mm here, including Mean Z and specification values.
"""
import numpy as np
from .mixins.analysis import AnalysisMixin
from .polynomial import evaluate_polynomial_surface, fit_polynomial_surface

SINGLE_ITEMS = (
    ('mean_z', 'Mean Z', 'mm'), ('pv', 'PV', 'µm'),
    ('ttv', 'TTV·Z极差', 'µm'), ('rx', '|Rx|', 'µrad'),
    ('ry', '|Ry|', 'µrad'), ('std3', '3σ', 'µm'),
)


def normalize_single_config(data=None):
    from .judgment import _finite
    data = data if isinstance(data, dict) else {}
    keys = [item[0] for item in SINGLE_ITEMS]
    visible = data.get('visible', keys)
    if not isinstance(visible, list) or any(k not in keys for k in visible):
        raise ValueError('单层显示指标列表无效')
    limits = data.get('limits', {})
    if not isinstance(limits, dict):
        raise ValueError('单层指标规格必须为对象')
    result = {}
    for key in keys:
        value = limits.get(key)
        result[key] = _finite(value)
        if value is not None and (isinstance(value, bool) or result[key] is None or result[key] < 0):
            raise ValueError('单层指标上限必须为非负有限数或空值')
    return {'visible': [key for key in keys if key in visible], 'limits': result}


def display_metrics(x, y, z_mm, mode='raw', model=None):
    x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z_mm))
    if mode != 'raw':
        order = int(mode.rsplit('_', 1)[1])
        model = model or fit_polynomial_surface(x, y, z, order)
        z = z - evaluate_polynomial_surface(model, x, y)
    metrics = AnalysisMixin.compute_plane_metrics(x, y, z)
    if mode != 'raw':
        metrics['pv'] = float(np.ptp(z) * 1000.)
        metrics['rms'] = float(np.sqrt(np.mean(z**2)) * 1000.)
    metrics['std3'] = float(3 * np.std(z, ddof=0) * 1000.)
    metrics['display_mode'] = mode
    return metrics


def judge_single(values, config):
    from .judgment import _finite
    config = normalize_single_config(config)
    items = []
    for key, label, unit in SINGLE_ITEMS:
        limit = config['limits'][key]
        if key not in config['visible'] or limit is None:
            continue
        value = _finite((values or {}).get(key))
        compared = abs(value) if value is not None and key in ('rx', 'ry') else value
        items.append(dict(key=key, label=label, unit=unit, value=value,
                          limit=limit, ok=None if value is None else compared <= limit))
    failed = [item['key'] for item in items if item['ok'] is False]
    verdict = ('unset' if not items else 'fail' if failed else
               'pending' if any(i['ok'] is None for i in items) else 'pass')
    return dict(verdict=verdict, items=items, failed=failed)
