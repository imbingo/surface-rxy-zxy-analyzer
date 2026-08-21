"""Numerically stable polynomial surface fitting for display diagnostics."""

from __future__ import annotations

import numpy as np


TERM_POWERS = {
    1: ((0, 0), (1, 0), (0, 1)),
    2: ((0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)),
    3: (
        (0, 0), (1, 0), (0, 1),
        (2, 0), (1, 1), (0, 2),
        (3, 0), (2, 1), (1, 2), (0, 3),
    ),
}


def _scale(values: np.ndarray) -> tuple[float, float]:
    center = float(np.mean(values))
    span = float(np.ptp(values))
    scale = span / 2.0
    if not np.isfinite(scale) or scale <= 1e-12:
        scale = 1.0
    return center, scale


def _design_matrix(xn: np.ndarray, yn: np.ndarray, powers) -> np.ndarray:
    return np.column_stack([(xn ** px) * (yn ** py) for px, py in powers])


def evaluate_polynomial_surface(model: dict, x, y) -> np.ndarray:
    """Evaluate a model returned by :func:`fit_polynomial_surface`."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xn = (x - model['x_center']) / model['x_scale']
    yn = (y - model['y_center']) / model['y_scale']
    matrix = _design_matrix(xn, yn, model['powers'])
    return matrix @ model['coefficients']


def fit_polynomial_surface(x, y, z, order: int) -> dict:
    """Fit a total-degree 1/2/3 polynomial surface in centered coordinates.

    X and Y are centered and scaled before solving so stage coordinates in the
    hundreds of millimetres do not make the high-order design matrix ill-conditioned.
    Returned PV/RMS values are vertical Z residual metrics in micrometres. They
    are deliberately separate from the application's authoritative plane-normal PV.
    """
    if order not in TERM_POWERS:
        raise ValueError("多项式阶数仅支持 1、2、3")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    if not (x.ndim == y.ndim == z.ndim == 1 and len(x) == len(y) == len(z)):
        raise ValueError("X/Y/Z 必须为长度一致的一维数组")
    powers = TERM_POWERS[order]
    if len(z) < len(powers):
        raise ValueError(f"{order}阶曲面至少需要 {len(powers)} 个有效点")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or not np.all(np.isfinite(z)):
        raise ValueError("高阶曲面拟合不接受非有限数值")

    x_center, x_scale = _scale(x)
    y_center, y_scale = _scale(y)
    xn = (x - x_center) / x_scale
    yn = (y - y_center) / y_scale
    matrix = _design_matrix(xn, yn, powers)
    coefficients, _, rank, singular_values = np.linalg.lstsq(matrix, z, rcond=None)
    if int(rank) < len(powers):
        raise ValueError(f"{order}阶曲面拟合矩阵秩不足，请检查点分布或 ROI")

    fitted = matrix @ coefficients
    residual = z - fitted
    centered = z - np.mean(z)
    ss_res = float(np.sum(residual ** 2))
    ss_total = float(np.sum(centered ** 2))
    r_squared = 1.0 - ss_res / ss_total if ss_total > 1e-30 else 1.0
    condition = float(singular_values[0] / singular_values[-1]) if singular_values[-1] > 0 else np.inf
    return {
        'order': int(order),
        'powers': tuple(powers),
        'coefficients': np.asarray(coefficients, dtype=float),
        'x_center': x_center,
        'y_center': y_center,
        'x_scale': x_scale,
        'y_scale': y_scale,
        'rank': int(rank),
        'condition': condition,
        'r_squared': float(r_squared),
        'fit_pv_um': float(np.ptp(fitted) * 1000.0),
        'residual_pv_um': float(np.ptp(residual) * 1000.0),
        'residual_rms_um': float(np.sqrt(np.mean(residual ** 2)) * 1000.0),
    }
