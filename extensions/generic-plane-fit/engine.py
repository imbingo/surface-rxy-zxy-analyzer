"""Orthogonal least-squares planes in a shared Cartesian frame (mm)."""

from dataclasses import dataclass

import numpy as np


ANGLE_DEFINITION = (
    'Rx = atan2(−nᵧ, n_z)，Ry = atan2(nₓ, n_z)，以 180° 为周期归入 [−90°, 90°)。'
    'Δ 为 B−A 的最短有符号差，同区间，恰好相差 90° 时取 −90°。'
    '若相关分量均为零，对应角度及 Δ 不可定义。Rx／Ry 为投影倾角。'
)
NORMAL_DEFINITION = (
    '法向量符号由绝对值最大的分量决定，取该分量为正。'
    '相差不超过 10⁻⁸ 时按 Z、X、Y 优先。该规则只确定无向平面的表示。'
)
QUALITY_DEFINITION = '有符号法向距离 d = n·p + D。RMS = √mean(d²)，最大偏差 = max|d|。'


@dataclass(frozen=True)
class PlaneFit:
    normal: np.ndarray
    offset_mm: float
    centroid_mm: np.ndarray
    tangent_basis: np.ndarray
    tangent_bounds_mm: np.ndarray
    xyz_bounds_mm: np.ndarray
    singular_values_mm: np.ndarray
    signed_distances_mm: np.ndarray
    rms_mm: float
    max_abs_mm: float
    unique_points: int
    rx_deg: float | None
    ry_deg: float | None
    sensitive_angles: tuple[bool, bool]
    warnings: tuple[str, ...]


def _wrap_deg(angle):
    wrapped = float((angle + 90.0) % 180.0 - 90.0)
    # Floating-point remainder can round up to the excluded endpoint.
    return -90.0 if wrapped == 90.0 else wrapped


def angle_difference(measure, base):
    return None if measure is None or base is None else _wrap_deg(measure - base)


def _projected_angle(numerator, denominator, label, warnings):
    length = np.hypot(numerator, denominator)
    if length <= 1e-12:
        warnings.append(f'{label} 不可定义，相关法向分量在数值容差内均为零。')
        return None, False
    sensitive = length < 1e-3
    if sensitive:
        warnings.append(f'{label} 接近投影退化，对测点噪声敏感。')
    return _wrap_deg(np.degrees(np.arctan2(numerator, denominator))), sensitive


def fit_plane(xyz):
    points = np.asarray(xyz, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
        raise ValueError('拟合需要至少 3 个 XYZ 点。')
    if not np.isfinite(points).all():
        raise ValueError('XYZ 点集中包含非有限值。')

    # Sorting stabilizes accumulation against reordering. Preserve repeated
    # measurements as weights and retain distances in input order for tracing.
    unique, counts = np.unique(points, axis=0, return_counts=True)
    if len(unique) < 3:
        raise ValueError('去除完全重复的坐标后不足 3 个不同的点。')
    origin = unique[0]
    shifted = unique - origin
    shift = np.average(shifted, axis=0, weights=counts)
    center = origin + shift
    centered = shifted - shift
    _, spans, basis = np.linalg.svd(centered * np.sqrt(counts[:, None]), full_matrices=False)
    if spans[0] == 0 or spans[1] <= spans[0] * 1e-6:
        raise ValueError('点集共线或近共线，第二方向的分布不足。')
    if spans[2] >= spans[1] * 0.98:
        raise ValueError('最小两个奇异值过于接近，无法可靠确定平面方向。')

    normal = basis[2].copy()
    largest = np.abs(normal).max()
    pivot = next(axis for axis in (2, 0, 1) if largest - abs(normal[axis]) <= 1e-8)
    if normal[pivot] < 0:
        normal *= -1
    distances = ((points - origin) - shift) @ normal
    tangents = centered @ basis[:2].T
    tangent_bounds = np.array([tangents.min(axis=0), tangents.max(axis=0)])
    xyz_bounds = np.array([unique.min(axis=0), unique.max(axis=0)])
    warnings = []
    if len(unique) < len(points):
        warnings.append(f'存在 {len(points) - len(unique):,} 个完全重复的点，按原测量次数计权。')
    if spans[1] < spans[0] * 1e-3:
        warnings.append('测点在第二方向分布较窄，法向对噪声敏感。')
    if spans[2] >= spans[1] * 0.2:
        warnings.append('法向散布相对面内次方向较大，请检查选区是否包含多个面。')
    rx, rx_sensitive = _projected_angle(-normal[1], normal[2], 'Rx', warnings)
    ry, ry_sensitive = _projected_angle(normal[0], normal[2], 'Ry', warnings)
    return PlaneFit(
        normal, float(-center @ normal), center, basis[:2], tangent_bounds, xyz_bounds,
        spans, distances,
        float(np.linalg.norm(distances) / np.sqrt(len(distances))),
        float(np.max(np.abs(distances))), len(unique), rx, ry, (rx_sensitive, ry_sensitive), tuple(warnings))


def compare_in_base_frame(base, measure, reference_axis):
    """Express both normals in a right-handed frame attached to plane A."""
    z_axis = base.normal
    axis = reference_axis
    if axis is None:
        alignments = np.abs(z_axis)
        axis = int(np.flatnonzero(alignments <= alignments.min() + 1e-8)[0])
    reference = np.eye(3)[axis]
    projected = reference - (reference @ z_axis) * z_axis
    length = np.linalg.norm(projected)
    if length < 1e-3:
        raise ValueError('所选参考轴近似垂直于 A 面，面内投影过短。请选择另一条轴或自动。')
    x_axis = projected / length
    y_axis = np.cross(z_axis, x_axis)
    basis = np.stack((x_axis, y_axis, z_axis))
    normal = basis @ measure.normal
    warnings = []
    rx, rx_sensitive = _projected_angle(-normal[1], normal[2], 'Rx', warnings)
    ry, ry_sensitive = _projected_angle(normal[0], normal[2], 'Ry', warnings)
    return {
        'resolved_reference_axis': 'XYZ'[axis],
        'basis_rows_in_source': basis,
        'origin_mm_in_source': base.centroid_mm,
        'base_rx_ry_deg': (0.0, 0.0),
        'measure_rx_ry_deg': (rx, ry),
        'delta_rx_ry_deg': (rx, ry),
        'sensitive_angles': (rx_sensitive, ry_sensitive),
        'warnings': warnings,
    }


def three_point_frame(points):
    """Rows are +X, +Y, +Z in source coordinates; input order is A, B, C."""
    points = np.asarray(points, dtype=float)
    if points.shape != (3, 3) or not np.isfinite(points).all():
        raise ValueError('三点建基准需要三个有限的 XYZ 点。')
    a, b, c = points
    ab, ac = b - a, c - a
    length = np.linalg.norm(ab)
    scale = max(length, np.linalg.norm(ac), np.linalg.norm(c - b))
    if length <= scale * 1e-6:
        raise ValueError('两底点重复或底边过短。')
    x = ab / length
    altitude = ac - (ac @ x) * x
    height = np.linalg.norm(altitude)
    if height <= scale * 1e-6:
        raise ValueError('三点共线或接近共线，无法稳定定轴。')
    y = altitude / height
    warnings = []
    if min(length, height) < scale * 1e-3:
        warnings.append('底边或三角形高度相对点距过短，定轴对测量误差敏感。')
    return dict(basis_rows_in_source=np.stack((x, y, np.cross(x, y))),
                origin_mm_in_source=a + ab / 2, warnings=warnings)


def plane_angle_deg(first, second):
    """Unoriented plane angle, in [0, 90] degrees."""
    return float(np.degrees(np.arctan2(np.linalg.norm(np.cross(first, second)),
                                       abs(first @ second))))


def two_plane_frame(first, second, policy, *, flip_first=False, flip_second=False):
    """First reference defines nominal X, second defines nominal Y."""
    first = np.asarray(first, dtype=float)
    second = np.asarray(second, dtype=float)
    if flip_first:
        first = -first
    if flip_second:
        second = -second
    z = np.cross(first, second)
    length = np.linalg.norm(z)
    if length < 1e-3:
        raise ValueError('两基准面近似平行，无法稳定定轴。')
    z /= length
    if policy == 'x':
        x = first
        y = np.cross(z, x)
    elif policy == 'y':
        y = second
        x = np.cross(y, z)
    else:
        # Preserve the two bisectors, giving each normal equal angular correction.
        plus, minus = first + second, first - second
        plus /= np.linalg.norm(plus)
        minus /= np.linalg.norm(minus)
        x, y = (plus + minus) / np.sqrt(2), (plus - minus) / np.sqrt(2)
    angle = plane_angle_deg(first, second)
    perp_error = 90.0 - angle
    warnings = []
    if perp_error > 5.0:
        warnings.append(f'两参考面偏离直角 {perp_error:.2f}°，请确认基准面接近正交。')
    return dict(basis_rows_in_source=np.stack((x, y, z)), reference_plane_angle_deg=angle,
                perpendicularity_error_deg=perp_error,
                flip_first=bool(flip_first), flip_second=bool(flip_second),
                warnings=warnings)


def normal_angles_in_frame(normal, basis):
    nx, ny, nz = basis @ normal
    warnings = []
    rx, sx = _projected_angle(-ny, nz, 'Rx', warnings)
    ry, sy = _projected_angle(nx, nz, 'Ry', warnings)
    return dict(delta_rx_ry_deg=(rx, ry), measure_rx_ry_deg=(rx, ry),
                sensitive_angles=(sx, sy), warnings=warnings,
                total_tilt_deg=plane_angle_deg(normal, basis[2]))


def frame_rotation(reference_basis, target_basis):
    """Columns are the target axes expressed in the reference frame."""
    matrix = reference_basis @ target_basis.T
    skew = np.array((matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0],
                     matrix[1, 0] - matrix[0, 1]))
    angle = np.degrees(np.arctan2(np.linalg.norm(skew), np.trace(matrix) - 1))
    return matrix, float(angle)


def fit_plane_with_outlier_rejection(xyz, check_cancel=lambda: None):
    """Refit after conservative clipping of orthogonal residuals."""
    points = np.asarray(xyz, dtype=float)
    original = fit_plane(points)
    retained = np.arange(len(points))
    fit = original
    note = ''
    if len(points) < 8 or original.unique_points < 8:
        note = '少于 8 个不同测点，跳过异常点剔除。'
    else:
        max_rejected = len(points) // 5
        # A numerical floor avoids clipping round-off on an ideal plane.
        floor = max(1e-10, np.max(np.abs(points)) * np.finfo(float).eps * 64)
        for _ in range(5):
            check_cancel()
            residual = fit.signed_distances_mm
            median = np.median(residual)
            deviation = np.abs(residual - median)
            cutoff = max(3.5 * 1.4826 * np.median(deviation), floor)
            keep = retained[deviation <= cutoff]
            if len(keep) == len(retained):
                break
            if len(keep) < 8 or len(points) - len(keep) > max_rejected:
                note = '候选剔除过多，保留原始拟合。请检查选区。'
                break
            try:
                candidate = fit_plane(points[keep])
            except (ValueError, np.linalg.LinAlgError):
                note = '候选保留点无法稳定拟合，保留原始拟合。请检查选区。'
                break
            if candidate.unique_points < 8:
                note = '候选保留点不足 8 个不同测点，保留原始拟合。'
                break
            fit, retained = candidate, keep
    if note:
        fit, retained = original, np.arange(len(points))
    check_cancel()
    return fit, {
        'method': 'orthogonal_mad',
        'retained_positions': retained,
        'rejected_positions': np.setdiff1d(np.arange(len(points)), retained, assume_unique=True),
        'original_rx_ry_deg': (original.rx_deg, original.ry_deg),
        'original_rms_mm': original.rms_mm,
        'note': note,
    }
