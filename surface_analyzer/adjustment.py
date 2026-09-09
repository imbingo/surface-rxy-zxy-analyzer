"""Headless N-support geometric adjustment. Planes use mm; shims use µm.

Error = current - target; adjustment = target - current.
Predictions are geometric least-squares estimates, not contact mechanics.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from math import atan, tan, isfinite
from typing import Sequence

import numpy as np


def mm_to_um(value):
    return value * 1000.0


def um_to_mm(value):
    return value / 1000.0


def rad_to_urad(value):
    return value * 1e6


def urad_to_rad(value):
    return value / 1e6


def _finite(*values):
    try:
        valid = all(isfinite(v) for v in values)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('所有输入必须为有限数值')


@dataclass(frozen=True)
class Plane:
    a: float
    b: float
    c: float

    def __post_init__(self):
        _finite(self.a, self.b, self.c)

    def at(self, x, y):
        return self.a * x + self.b * y + self.c

    @classmethod
    def from_pose(cls, rx_urad, ry_urad, z0_um, origin):
        x0, y0 = origin
        _finite(rx_urad, ry_urad, z0_um, x0, y0)
        rx, ry = urad_to_rad(rx_urad), urad_to_rad(ry_urad)
        if max(abs(rx), abs(ry)) >= np.pi / 2:
            raise ValueError('目标角度必须在 (-π/2, π/2) 内')
        a, b = -tan(ry), tan(rx)
        return cls(a, b, um_to_mm(z0_um) - a*x0 - b*y0)


@dataclass(frozen=True)
class SupportPoint:
    name: str
    x_mm: float
    y_mm: float
    base_shim_um: float
    min_shim_um: float
    max_shim_um: float
    step_um: float | None = None


@dataclass(frozen=True)
class AdjustmentConfig:
    origin_mode: str = 'support_centroid'
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    quantization: str = 'none'
    default_step_um: float = 5.0
    coplanarity_warn_um: float = 5.0


@dataclass(frozen=True)
class Coplanarity:
    pv_um: float
    rms_um: float
    max_abs_um: float
    worst_point: str
    residuals_um: tuple[float, ...]
    warning: bool


@dataclass(frozen=True)
class PoseError:
    rx_urad: float
    ry_urad: float
    dz0_um: float


@dataclass(frozen=True)
class SupportResult:
    support: SupportPoint
    current_z_um: float
    target_z_um: float
    error_height_um: float
    theoretical_adjustment_um: float
    ideal_shim_um: float
    recommended_shim_um: float
    actual_adjustment_um: float
    quantization_error_um: float
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentResult:
    reference_origin: tuple[float, float]
    current_plane: Plane
    target_plane: Plane
    adjustment_plane: Plane
    realized_adjustment_plane: Plane
    predicted_plane: Plane
    support_results: tuple[SupportResult, ...]
    before_error: PoseError
    residual_pose: PoseError
    geometry_condition: float
    executable: bool
    prediction_kind: str
    warnings: tuple[str, ...]
    coplanarity: Coplanarity


def quantize_shim(thickness_um, step_um):
    """Nearest final thickness; exact decimal half steps round away from zero."""
    _finite(thickness_um, step_um)
    if step_um <= 0:
        raise ValueError('步进必须大于0')
    step = Decimal(str(step_um))
    return float((Decimal(str(thickness_um)) / step).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP) * step)


def _geometry(supports):
    if len(supports) < 3:
        raise ValueError('调整点至少需要3个')
    xy = np.array([(p.x_mm, p.y_mm) for p in supports], dtype=float)
    if not np.isfinite(xy).all():
        raise ValueError('调整坐标必须为有限数值')
    if len(np.unique(xy, axis=0)) != len(xy):
        raise ValueError('调整点坐标重复')
    center = xy[0] + np.mean(xy - xy[0], axis=0)
    offsets = xy - center
    scale = float(np.max(np.abs(offsets)))
    if not isfinite(scale) or scale == 0:
        raise ValueError('调整点几何退化，无法唯一确定二维倾斜')
    matrix = np.column_stack((offsets / scale, np.ones(len(xy))))
    singular = np.linalg.svd(matrix, compute_uv=False)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float('inf')
    if condition > 1e8:
        raise ValueError('调整点几何退化，无法唯一确定二维倾斜')
    return xy, center, scale, matrix, condition


def _pose_error(current, target, origin):
    x0, y0 = origin
    return PoseError(rad_to_urad(atan(current.b) - atan(target.b)),
                     rad_to_urad(atan(-current.a) - atan(-target.a)),
                     mm_to_um((current.a-target.a)*x0 +
                              (current.b-target.b)*y0 + current.c-target.c))


def calculate_adjustment(current: Plane, target: Plane,
                         supports: Sequence[SupportPoint],
                         config: AdjustmentConfig = AdjustmentConfig()) -> AdjustmentResult:
    """Calculate without modifying inputs or accessing source measurement points."""
    supports = tuple(supports)
    xy, center, scale, matrix, condition = _geometry(supports)
    _finite(config.origin_x_mm, config.origin_y_mm, config.default_step_um)
    _finite(config.coplanarity_warn_um)
    if config.coplanarity_warn_um < 0:
        raise ValueError('共面性阈值不能小于0')
    if config.default_step_um <= 0:
        raise ValueError('步进必须大于0')
    if config.quantization not in ('none', 'fixed_step'):
        raise ValueError('未知量化模式')
    origins = {'support_centroid': tuple(center), 'coordinate_origin': (0., 0.),
               'custom': (config.origin_x_mm, config.origin_y_mm)}
    if config.origin_mode not in origins:
        raise ValueError('未知参考原点模式')
    origin = origins[config.origin_mode]
    delta = Plane(target.a-current.a, target.b-current.b, target.c-current.c)
    h = mm_to_um(delta.at(*origin) + delta.a*(xy[:, 0]-origin[0]) +
                 delta.b*(xy[:, 1]-origin[1]))
    if not np.isfinite(h).all():
        raise ValueError('计算结果超出有限数值范围')
    rows, warnings = [], []
    executable = True
    for p, height in zip(supports, h):
        _finite(p.base_shim_um, p.min_shim_um, p.max_shim_um)
        if p.min_shim_um > p.max_shim_um:
            raise ValueError('最小厚度不能大于最大厚度')
        step = config.default_step_um if p.step_um is None else p.step_um
        _finite(step)
        if step <= 0:
            raise ValueError('步进必须大于0')
        ideal = p.base_shim_um + float(height)
        recommended = quantize_shim(ideal, step) if config.quantization == 'fixed_step' else ideal
        actual = recommended - p.base_shim_um
        statuses = ['需加垫片' if actual > 0 else '需抽垫片' if actual < 0 else '正常']
        if not p.min_shim_um <= p.base_shim_um <= p.max_shim_um:
            warnings.append(f'{p.name}: 基础垫片超出范围')
        for label, value in [('理论目标厚度', ideal), ('推荐实际厚度', recommended)]:
            if value < p.min_shim_um or value > p.max_shim_um:
                statuses.append(label + ('低于最小厚度' if value < p.min_shim_um else '超过最大厚度'))
                executable = False
        if not np.isclose(recommended, ideal, rtol=0, atol=1e-10):
            statuses.append('无法按当前步进精确实现')
        _finite(ideal, recommended, actual)
        rows.append(SupportResult(p, mm_to_um(current.at(p.x_mm, p.y_mm)),
                                  mm_to_um(target.at(p.x_mm, p.y_mm)), -float(height),
                                  float(height), ideal, recommended, actual,
                                  recommended-ideal, tuple(statuses)))
    heights_mm = um_to_mm(np.array([r.actual_adjustment_um for r in rows]))
    fit = np.linalg.lstsq(matrix, heights_mm, rcond=None)[0]
    residuals = mm_to_um(heights_mm - matrix @ fit)
    pv = float(np.ptp(residuals))
    coplanarity = Coplanarity(pv, float(np.sqrt(np.mean(residuals**2))),
                             float(np.max(np.abs(residuals))),
                             supports[int(np.argmax(np.abs(residuals)))].name,
                             tuple(float(v) for v in residuals),
                             len(supports) >= 4 and pv > config.coplanarity_warn_um)
    a, b = fit[:2] / scale
    realized = Plane(float(a), float(b), float(fit[2]-a*center[0]-b*center[1]))
    predicted = Plane(current.a+realized.a, current.b+realized.b, current.c+realized.c)
    if len(supports) >= 4:
        warnings.append('四点及以上支撑属于静力学过约束结构；实际接触状态还受基座/工件刚度、局部平面度和预紧力影响。当前仅为几何预测。')
    if coplanarity.warning:
        warnings.append('量化后的多个支撑点不共面，可能存在单点虚接触或局部过约束。')
    if not executable:
        warnings.append('厚度超限：不可执行方案的几何估计')
    return AdjustmentResult(origin, current, target, delta, realized, predicted,
                            tuple(rows), _pose_error(current, target, origin),
                            _pose_error(predicted, target, origin), condition,
                            executable, 'least_squares_geometry' if len(supports) >= 4 else 'three_point_geometry',
                            tuple(warnings), coplanarity)
