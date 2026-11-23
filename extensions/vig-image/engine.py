"""Qt-free numerical engine for VIG image-plane analysis."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.spatial import cKDTree


pi = 3.14159
q = 1.6E-19
m = 9.1E-31
D1 = 100E-6
D2 = 100E-6
D3 = 100E-6
Ek = 5000
V1 = 0
V2 = -3685
V3 = 0
R0_RANGE = np.array([0, 2E-6, 5E-6, 10E-6, 15E-6, 20E-6, 25E-6])
DEFAULT_CHUNK_SIZE = 64


def phi_detail(z, S, D1, D2, V1, V2):
    z_pos = (2 * z + S) / D1
    z_neg = (2 * z - S) / D2
    A = z_pos * D1 * np.arctan(z_pos)
    B = z_neg * D2 * np.arctan(z_neg)
    phi_z = (V2 - V1) / (2 * pi * S) * (A - B) + (V1 + V2) / 2
    return phi_z


def detail_einzel_cal(S1, S2, E2, D1, D2, D3, V1, V2, V3):
    d1 = (E2 + S1) / 2
    d2 = (E2 + S2) / 2
    z_range = 20000
    z = np.linspace(-1000E-6, 1000E-6, z_range)

    phi_z_tot = phi_detail(z + d1, S1, D1, D2, V1, V2) + \
                phi_detail(z - d2, S2, D2, D3, V2, V3) - V2
    phi_d1z = np.diff(phi_z_tot) / np.diff(z)
    phi_d1z = np.append(phi_d1z, phi_d1z[-1])
    phi_d2z = np.diff(phi_d1z) / np.diff(z)
    phi_d2z = np.append(phi_d2z, phi_d2z[-1])
    phi_d3z = np.diff(phi_d2z) / np.diff(z)
    phi_d3z = np.append(phi_d3z, phi_d3z[-1])
    phi_d4z = np.diff(phi_d3z) / np.diff(z)
    phi_d4z = np.append(phi_d4z, phi_d4z[-1])
    return z, [phi_d1z, phi_d2z, phi_d3z, phi_d4z]


def Cs_func(x, f, cs):
    return -x / f - cs * x ** 3 / f ** 4


def fit_Cs(Cs_func, rs, angle_s, p0_init=[0.5, 0.02]):
    param, extras = curve_fit(Cs_func, rs, angle_s, p0=p0_init, maxfev=10000)
    f, cs = param
    return f, cs


def _trace_rays_py(dz, r0s, vz0, phi_d1z, phi_d2z, phi_d3z, phi_d4z, qm):
    """Euler integration matching the standalone tool's ray trace."""
    n_rays = len(r0s)
    out = np.zeros((n_rays, 3))
    for k in range(n_rays):
        r, vz, vr = r0s[k], vz0, 0.0
        for i in range(len(phi_d1z)):
            dt = dz / vz
            r += vr * dt
            vr += (-0.5 * phi_d2z[i] * r + 1 / 16 * phi_d4z[i] * r ** 3) * qm * dt
            vz += (phi_d1z[i] - 0.25 * phi_d3z[i] * r * r) * qm * dt
        out[k, 0] = r
        out[k, 1] = vz
        out[k, 2] = vr
    return out


def _phi_derivatives_batch(vig_up, vig_low, t_mid):
    count = len(vig_up)
    z = np.linspace(-1000E-6, 1000E-6, 20000)
    z_diff = np.diff(z)
    s1 = vig_up / 1e6
    s2 = vig_low / 1e6
    e2 = t_mid / 1e6
    d1 = (e2 + s1) / 2
    d2 = (e2 + s2) / 2
    z_values = z[None, :]
    phi_z = (
        phi_detail(z_values + d1[:, None], s1[:, None], D1, D2, V1, V2)
        + phi_detail(z_values - d2[:, None], s2[:, None], D2, D3, V2, V3)
        - V2
    )
    derivatives = []
    current = phi_z
    for _ in range(4):
        current = np.diff(current, axis=1) / z_diff[None, :]
        current = np.concatenate((current, current[:, -1:]), axis=1)
        derivatives.append(current)
    if count == 0:
        return z, derivatives
    return z, derivatives


def _trace_rays_batch(dz, phi_sets, check_cancel=None, ek=Ek):
    phi_d1z, phi_d2z, phi_d3z, phi_d4z = phi_sets
    count = phi_d1z.shape[0]
    vz0 = (2 * ek * q / m) ** 0.5
    r = np.broadcast_to(R0_RANGE, (count, len(R0_RANGE))).copy()
    vz = np.full_like(r, vz0)
    vr = np.zeros_like(r)
    for i in range(phi_d1z.shape[1]):
        if check_cancel is not None and i % 2000 == 0:
            check_cancel()
        dt = dz / vz
        r += vr * dt
        vr += (-0.5 * phi_d2z[:, i, None] * r
               + 1 / 16 * phi_d4z[:, i, None] * r ** 3) * q / m * dt
        vz += (phi_d1z[:, i, None]
               - 0.25 * phi_d3z[:, i, None] * r * r) * q / m * dt
    return r, vz, vr


def _image_traces_batch(keys, check_cancel=None, ek=Ek + V1):
    keys = np.asarray(keys, dtype=float).reshape(-1, 3)
    if not len(keys):
        return []
    z, phi_sets = _phi_derivatives_batch(keys[:, 0], keys[:, 1], keys[:, 2])
    r_end, vz_end, vr_end = _trace_rays_batch(
        z[1] - z[0], phi_sets, check_cancel, ek=ek)
    exit_angles = vr_end / vz_end
    results = []
    for index in range(len(keys)):
        principle_zs = z[-1] - (
            R0_RANGE[1:] - r_end[index, 1:]) / np.abs(exit_angles[index, 1:])
        img_principle_z = np.nanmean(principle_zs)
        f_img, cs_img = fit_Cs(
            Cs_func, list(R0_RANGE), list(exit_angles[index]), p0_init=[0.5, 0.02])
        results.append((img_principle_z, f_img, cs_img))
    return results


def fast_image_trace(Ek, z, phi_sets):
    """Trace one key using the same batched NumPy kernel as production."""
    r_end, vz_end, vr_end = _trace_rays_batch(
        z[1] - z[0], [np.asarray(values, dtype=float)[None, :] for values in phi_sets],
        ek=Ek)
    exit_angles = vr_end[0] / vz_end[0]
    principle_zs = z[-1] - (
        R0_RANGE[1:] - r_end[0, 1:]) / np.abs(exit_angles[1:])
    img_principle_z = np.nanmean(principle_zs)
    f_img, cs_img = fit_Cs(
        Cs_func, list(R0_RANGE), list(exit_angles), p0_init=[0.5, 0.02])
    return img_principle_z, f_img, cs_img


def warm_up_kernel():
    """Compatibility no-op; NumPy does not need a JIT warm-up."""
    return None


def calculate_zimg2low_from_df(
        df_data, check_cancel=None, progress=None, on_error=None,
        chunk_size=DEFAULT_CHUNK_SIZE):
    """Calculate images for cached unique keys in bounded NumPy batches.

    ``progress`` receives (completed_rows, total_rows); ``on_error`` receives
    (row_index, message) for individual rows that cannot be calculated.
    """
    total_rows = len(df_data)
    chunk_size = max(1, int(chunk_size))
    output = {name: [] for name in (
        'Index', 'X', 'Y', 'thickness_up', 'thickness_mid', 'thickness_low',
        'VIG_low', 'VIG_up', 'f_img', 'img_principle_z', 'image')}
    keys = []
    row_positions = {}
    invalid_rows = 0
    for position, (row_index, row) in enumerate(df_data.iterrows()):
        for name in ('Index', 'X', 'Y', 'thickness_up', 'thickness_mid',
                     'thickness_low', 'VIG_low', 'VIG_up'):
            output[name].append(row_index if name == 'Index' else row[name])
        key = (float(row['VIG_up']), float(row['VIG_low']), float(row['thickness_mid']))
        if not np.isfinite(key).all():
            output['f_img'].append(np.nan)
            output['img_principle_z'].append(np.nan)
            output['image'].append(np.nan)
            invalid_rows += 1
            if on_error is not None:
                on_error(row_index, '输入参数包含非有限值')
            continue
        if key not in row_positions:
            row_positions[key] = []
            keys.append(key)
        row_positions[key].append(position)
        output['f_img'].append(None)
        output['img_principle_z'].append(None)
        output['image'].append(None)

    cache = {}
    unique_keys = np.asarray(keys, dtype=float).reshape(-1, 3)
    processed_rows = invalid_rows
    for start in range(0, len(unique_keys), chunk_size):
        if check_cancel is not None:
            check_cancel()
        chunk = unique_keys[start:start + chunk_size]
        try:
            values = _image_traces_batch(chunk, check_cancel)
        except Exception:
            if check_cancel is not None:
                check_cancel()
            values = []
            for key in chunk:
                try:
                    values.append(_image_traces_batch([key], check_cancel)[0])
                except Exception as exc:
                    if check_cancel is not None:
                        check_cancel()
                    values.append(None)
                    if on_error is not None:
                        for position in row_positions[tuple(float(v) for v in key)]:
                            on_error(df_data.index[position], str(exc))
        for key, fit in zip(chunk, values):
            key_tuple = tuple(float(value) for value in key)
            cache[key_tuple] = fit
            fit = cache[key_tuple]
            if fit is None:
                for position in row_positions[key_tuple]:
                    output['f_img'][position] = np.nan
                    output['img_principle_z'][position] = np.nan
                    output['image'][position] = np.nan
                processed_rows += len(row_positions[key_tuple])
                continue
            img_z, f_img, cs_img = fit
            for position in row_positions.get(key_tuple, ()):
                row = df_data.iloc[position]
                image = f_img * 1e6 - row['thickness_low'] - key_tuple[1] - key_tuple[2] / 2 + img_z * 1e6
                output['f_img'][position] = f_img * 1e6
                output['img_principle_z'][position] = img_z * 1e6
                output['image'][position] = image
            processed_rows += len(row_positions[key_tuple])
        done = min(processed_rows, total_rows)
        if progress is not None:
            progress(done, total_rows)
    for name in ('f_img', 'img_principle_z', 'image'):
        output[name] = [np.nan if value is None else value for value in output[name]]
    if progress is not None and total_rows == 0:
        progress(0, 0)
    return pd.DataFrame(output)


def plane(x, y, Ry, Rx, Z):
    return -x * Ry + y * Rx + Z


def plane_fit(x, y, z):
    x = np.asarray(x, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    z = np.asarray(z, dtype=float).reshape(-1)
    if len(z) < 3:
        raise ValueError('有效点不足或共线，无法拟合平面')
    design = np.column_stack((-x, y, np.ones_like(x)))
    coefficients, _, rank, _ = np.linalg.lstsq(design, z, rcond=None)
    if rank < 3:
        raise ValueError('有效点不足或共线，无法拟合平面')
    Ry, Rx, Z = coefficients
    residuals = plane(x, y, Ry, Rx, Z) - z
    rms = np.sqrt(np.mean(residuals ** 2))
    return Ry, Rx, Z, rms


def process_plane_from_df(df_results):
    """Fit the VIG image plane and return its metrics and plot arrays."""
    df = df_results.copy()
    x = pd.to_numeric(df['X'], errors='coerce')
    y = pd.to_numeric(df['Y'], errors='coerce')
    z = pd.to_numeric(df['image'], errors='coerce')
    valid = x.notna() & y.notna() & z.notna()
    x = x[valid].values
    y = y[valid].values
    z = z[valid].values * (-1)

    x_m = x * 1e-3
    y_m = y * 1e-3
    z_m = z * 1e-6
    x_center = np.mean(x_m)
    y_center = np.mean(y_m)
    x_m = x_m - x_center
    y_m = y_m - y_center

    Ry, Rx, Z, rms = plane_fit(x_m, y_m, z_m)
    fitted_z = plane(x_m, y_m, Ry, Rx, Z)
    residuals = z_m - fitted_z
    std3 = np.std(residuals) * 3
    p_low, p_high = np.percentile(residuals, [1.5, 98.5])
    p97_half = (p_high - p_low) / 2

    xs_plot = x_m * 1e3
    ys_plot = y_m * 1e3
    zs_plot = z_m * 1e6

    X = np.linspace(np.min(x_m), np.max(x_m), 60)
    Y = np.linspace(np.min(y_m), np.max(y_m), 60)
    X, Y = np.meshgrid(X, Y)
    Z_fit = plane(X, Y, Ry, Rx, Z)

    return {
        'Ry': Ry, 'Rx': Rx, 'Z': Z, 'rms': rms, 'std3': std3, 'p97_half': p97_half,
        'xs_plot': xs_plot, 'ys_plot': ys_plot, 'zs_plot': zs_plot,
        'X_plot': X * 1e3, 'Y_plot': Y * 1e3, 'Z_fit_plot': Z_fit * 1e6,
        'valid_mask': valid.to_numpy(), 'residuals_m': residuals,
    }


def match_by_xy(ref_xy, other_xy, tol):
    """Return mutual-nearest XY pairs within ``tol`` mm, ordered by reference row."""
    ref_xy = np.asarray(ref_xy, dtype=float)
    other_xy = np.asarray(other_xy, dtype=float)
    if ref_xy.ndim != 2 or ref_xy.shape[1] != 2:
        raise ValueError('ref_xy 必须是 (N, 2) 坐标数组')
    if other_xy.ndim != 2 or other_xy.shape[1] != 2:
        raise ValueError('other_xy 必须是 (N, 2) 坐标数组')
    tol = float(tol)
    if not np.isfinite(tol) or tol <= 0:
        raise ValueError('XY 匹配容差必须大于 0')

    ref_finite = np.isfinite(ref_xy).all(axis=1)
    other_finite = np.isfinite(other_xy).all(axis=1)
    ref_indices = np.flatnonzero(ref_finite)
    other_indices = np.flatnonzero(other_finite)
    if not len(ref_indices) or not len(other_indices):
        return np.array([], dtype=int), np.array([], dtype=int)

    ref_valid = ref_xy[ref_indices]
    other_valid = other_xy[other_indices]
    dist_ref, nearest_other = cKDTree(other_valid).query(ref_valid, k=1)
    _, nearest_ref = cKDTree(ref_valid).query(other_valid, k=1)
    keep = (dist_ref <= tol) & (nearest_ref[nearest_other] == np.arange(len(ref_valid)))
    matched_ref = ref_indices[np.flatnonzero(keep)]
    matched_other = other_indices[nearest_other[keep]]
    order = np.argsort(matched_ref, kind='stable')
    return matched_ref[order].astype(int), matched_other[order].astype(int)


def _record_arrays(record, label):
    try:
        x = np.asarray(record['x'], dtype=float).reshape(-1)
        y = np.asarray(record['y'], dtype=float).reshape(-1)
        z = np.asarray(record['z'], dtype=float).reshape(-1)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'{label} 数据格式无效：需要 x、y、z 数组') from exc
    if not (len(x) == len(y) == len(z)):
        raise ValueError(f'{label} 的 X/Y/Z 点数不一致')
    return x, y, z


def _record_name(record, fallback):
    return str(record.get('name') or fallback)


def _record_source(record):
    info = record.get('import_info') or {}
    return str(record.get('source') or record.get('source_path')
               or info.get('source_path') or '')


def _match_report(label, ref_xy, other_xy, ref_idx, other_idx):
    matched_ref = np.zeros(len(ref_xy), dtype=bool)
    matched_ref[ref_idx] = True
    unmatched = ref_xy[~matched_ref][:10]
    return {
        'label': label,
        'matched': int(len(ref_idx)),
        'total_ref': int(len(ref_xy)),
        'total_other': int(len(other_xy)),
        'unmatched_ref_xy': [[float(x), float(y)] for x, y in unmatched],
        'unmatched_other': int(len(other_xy) - len(other_idx)),
    }


def _fmt_seconds(seconds):
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def compute_vig_image(
        two_layer, three_layer, thickness, tolerance_mm=0.01,
        progress=None, check_cancel=None) -> dict:
    """Match layer points and calculate the VIG image plane.

    Slot records use X/Y in mm and total thickness Z in mm. Constant electrode
    thickness values are expressed in µm.
    """
    tolerance_mm = float(tolerance_mm)
    if not np.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError('XY 匹配容差必须大于 0')

    def cancel_check():
        if check_cancel is not None:
            check_cancel()

    def report(value, text):
        if progress is not None:
            progress(max(0, min(100, int(value))), str(text))

    cancel_check()
    report(0, '正在预热 VIG 光线追踪内核…')
    warm_up_kernel()
    cancel_check()

    ref_x, ref_y, ref_z = _record_arrays(two_layer, '两层总厚')
    three_x, three_y, three_z = _record_arrays(three_layer, '三层总厚')
    ref_xy = np.column_stack((ref_x, ref_y))
    three_xy = np.column_stack((three_x, three_y))
    n_ref = len(ref_xy)
    ref_indices = np.arange(n_ref, dtype=int)
    maps = {}
    reports = []

    def add_layer_match(key, label, record):
        nonlocal ref_indices
        layer_x, layer_y, _ = _record_arrays(record, label)
        layer_xy = np.column_stack((layer_x, layer_y))
        matched_ref, matched_other = match_by_xy(ref_xy, layer_xy, tolerance_mm)
        row_map = np.full(n_ref, -1, dtype=int)
        row_map[matched_ref] = matched_other
        maps[key] = row_map
        reports.append(_match_report(label, ref_xy, layer_xy, matched_ref, matched_other))
        ref_indices = ref_indices[row_map[ref_indices] >= 0]

    add_layer_match('three', '三层总厚', three_layer)
    thickness_records = {}
    for key, label in (('up', '电极厚度 up'), ('mid', '电极厚度 mid'),
                       ('low', '电极厚度 low')):
        value = thickness.get(key)
        if isinstance(value, dict):
            thickness_records[key] = value
            add_layer_match(key, label, value)

    if not len(ref_indices):
        raise ValueError('各图层 XY 坐标没有可配对的点，请检查容差和坐标原点')

    cancel_check()
    three_values = three_z[maps['three'][ref_indices]] * 1000.0
    two_values = ref_z[ref_indices] * 1000.0
    thickness_values = {}
    thickness_descriptions = {}
    quality_sources = [('two_layer', two_layer), ('three_layer', three_layer)]
    inputs = {
        'two_layer': {
            'name': _record_name(two_layer, '两层总厚'),
            'n': int(len(ref_z)),
            'source': _record_source(two_layer),
            'metric_quality': dict(two_layer.get('metric_quality') or {}),
            'sampled': bool(two_layer.get('sampled', False)),
        },
        'three_layer': {
            'name': _record_name(three_layer, '三层总厚'),
            'n': int(len(three_z)),
            'source': _record_source(three_layer),
            'metric_quality': dict(three_layer.get('metric_quality') or {}),
            'sampled': bool(three_layer.get('sampled', False)),
        },
    }
    for key in ('up', 'mid', 'low'):
        value = thickness.get(key)
        if isinstance(value, dict):
            _, _, layer_z = _record_arrays(value, f'电极厚度 {key}')
            thickness_values[key] = layer_z[maps[key][ref_indices]] * 1000.0
            name = _record_name(value, f'电极厚度 {key}')
            thickness_descriptions[key] = f'逐点：{name} ({len(layer_z):,} 点)'
            quality_sources.append((f'thickness_{key}', value))
            inputs[f'thickness_{key}'] = {
                'name': name,
                'n': int(len(layer_z)),
                'source': _record_source(value),
                'mode': 'per_point',
                'metric_quality': dict(value.get('metric_quality') or {}),
                'sampled': bool(value.get('sampled', False)),
            }
        else:
            constant = float(value)
            thickness_values[key] = np.full(len(ref_indices), constant, dtype=float)
            thickness_descriptions[key] = f'固定值：{constant:g} µm'
            inputs[f'thickness_{key}'] = {
                'name': thickness_descriptions[key],
                'n': int(len(ref_indices)),
                'source': '',
                'mode': 'constant',
                'value_um': constant,
                'metric_quality': {},
                'sampled': False,
            }

    quality_details = [
        {
            'input': key,
            'name': _record_name(record, key),
            'metric_quality': dict(record.get('metric_quality') or {}),
            'sampled': bool(record.get('sampled', False)),
        }
        for key, record in quality_sources
    ]
    sampled = any(item['sampled'] for item in quality_details)
    estimated = sampled or any(
        item['metric_quality'].get('estimated', False) for item in quality_details)
    metric_quality = {
        'estimated': bool(estimated),
        'sampled': bool(sampled),
        'sources': quality_details,
        'label': '抽样估计' if estimated else '全量计算',
        'warning': (
            '输入包含抽样或估计数据；VIG 结果为估计，不可直接用于产线放行。'
            if estimated else ''),
    }

    x = ref_x[ref_indices]
    y = ref_y[ref_indices]
    vig_up = three_values - two_values - thickness_values['up']
    vig_low = two_values - thickness_values['mid'] - thickness_values['low']
    df_data = pd.DataFrame({
        'X': x,
        'Y': y,
        'thickness_up': thickness_values['up'],
        'thickness_mid': thickness_values['mid'],
        'thickness_low': thickness_values['low'],
        'VIG_low': vig_low,
        'VIG_up': vig_up,
    })
    valid_input = ~df_data.isnull().any(axis=1).to_numpy()
    df_data = df_data.loc[valid_input].reset_index(drop=True)
    if df_data.empty:
        raise ValueError('各图层 XY 坐标没有可配对的点，请检查容差和坐标原点')

    errors = []
    started = time.perf_counter()
    last_update = [0.0]

    def on_progress(done, total):
        cancel_check()
        now = time.perf_counter()
        if done < total and now - last_update[0] < 0.2:
            return
        last_update[0] = now
        elapsed = now - started
        remaining = elapsed / done * (total - done) if done else 0
        text = (f'已算 {done:,} / {total:,} 点 · 已用 {_fmt_seconds(elapsed)}'
                f' · 预计剩余 {_fmt_seconds(remaining)}')
        if done == total:
            text = f'完成：{total:,} 点 · 用时 {_fmt_seconds(elapsed)}'
        if errors:
            text += f' · 出错 {len(errors)} 点'
        report(int(done * 100 / max(total, 1)), text)

    def on_error(index, message):
        errors.append({'index': int(index), 'message': str(message)})

    results = calculate_zimg2low_from_df(
        df_data, check_cancel=cancel_check, progress=on_progress, on_error=on_error)
    result_valid = (
        pd.to_numeric(results['X'], errors='coerce').notna()
        & pd.to_numeric(results['Y'], errors='coerce').notna()
        & pd.to_numeric(results['image'], errors='coerce').notna()
    )
    if not result_valid.any():
        raise ValueError('没有可用于 VIG 像面拟合的有效结果')
    plane_info = process_plane_from_df(results)
    valid = plane_info['valid_mask']

    plane_payload = {
        'rx': float(plane_info['Rx']),
        'ry': float(plane_info['Ry']),
        'z': float(plane_info['Z']),
        'rms': float(plane_info['rms']),
        'std3': float(plane_info['std3']),
        'p97_half': float(plane_info['p97_half']),
    }
    return {
        'x': np.asarray(x[valid_input][valid], dtype=float),
        'y': np.asarray(y[valid_input][valid], dtype=float),
        'vig_up': np.asarray(vig_up[valid_input][valid], dtype=float),
        'vig_low': np.asarray(vig_low[valid_input][valid], dtype=float),
        't_up': np.asarray(thickness_values['up'][valid_input][valid], dtype=float),
        't_mid': np.asarray(thickness_values['mid'][valid_input][valid], dtype=float),
        't_low': np.asarray(thickness_values['low'][valid_input][valid], dtype=float),
        'f_img': results['f_img'].to_numpy(dtype=float)[valid],
        'img_principle_z': results['img_principle_z'].to_numpy(dtype=float)[valid],
        'image': results['image'].to_numpy(dtype=float)[valid],
        'resid_um': plane_info['residuals_m'] * 1e6,
        'plane': plane_payload,
        'n_points': int(valid.sum()),
        'n_errors': int(len(errors)),
        'errors': errors,
        'tolerance': tolerance_mm,
        'match_reports': reports,
        'inputs': inputs,
        'metric_quality': metric_quality,
        'estimated': bool(estimated),
        'sampled': bool(sampled),
        'thickness_description': thickness_descriptions,
        'plane_info': plane_info,
        'matched_reference_indices': ref_indices[valid_input][valid],
        'valid_result_indices': np.flatnonzero(valid),
    }
