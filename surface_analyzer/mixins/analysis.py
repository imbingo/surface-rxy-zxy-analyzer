"""AnalysisMixin extracted from the V3.9.3 application."""

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree

from ..polynomial import TERM_POWERS, fit_polynomial_surface, evaluate_polynomial_surface
from ..workers import TaskCancelled



class AnalysisMixin:
    @staticmethod
    def spatial_gaussian_filter(x, y, z, sigma_mm=0.05, matrix_rc=None,
                                truncate=3.0, max_neighbors=128,
                                cancel_event=None, return_summary=False):
        """Mask-aware areal Gaussian low-pass without creating or filling points.

        Regular matrix data uses normalized convolution over valid cells.  Other
        point clouds use a radius-limited Gaussian over nearby measured points.
        In both cases the original XY locations and point count are preserved.
        """
        x, y, z = (np.asarray(v, dtype=float).reshape(-1) for v in (x, y, z))
        if not (len(x) == len(y) == len(z)) or len(z) < 1:
            raise ValueError("空间高斯滤波需要长度一致的XYZ数据。")
        sigma_mm = float(sigma_mm)
        if not np.isfinite(sigma_mm) or sigma_mm <= 0:
            raise ValueError("空间高斯滤波σ必须大于0 mm。")
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        if not np.all(finite):
            raise ValueError("空间高斯滤波输入必须为有限有效点。")
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()

        method = 'point_cloud_radius'
        output = z.copy()
        support_radius = float(truncate) * sigma_mm
        used_matrix = False
        if matrix_rc is not None:
            rows = np.asarray(matrix_rc[0]).reshape(-1)
            cols = np.asarray(matrix_rc[1]).reshape(-1)
            if len(rows) == len(z) and len(cols) == len(z):
                rows = rows.astype(np.int64, copy=False)
                cols = cols.astype(np.int64, copy=False)
                r0, c0 = int(rows.min()), int(cols.min())
                rr, cc = rows - r0, cols - c0
                shape = (int(rr.max()) + 1, int(cc.max()) + 1)
                # Avoid allocating an unexpectedly sparse giant logical matrix.
                if shape[0] * shape[1] <= max(5_000_000, 6 * len(z)):
                    def axis_pitch(values):
                        unique = np.unique(values)
                        diffs = np.diff(unique)
                        diffs = diffs[diffs > 1e-12]
                        return float(np.median(diffs)) if len(diffs) else np.nan

                    pitch_x, pitch_y = axis_pitch(x), axis_pitch(y)
                    if np.isfinite(pitch_x) and np.isfinite(pitch_y):
                        values = np.zeros(shape, dtype=float)
                        weights = np.zeros(shape, dtype=float)
                        values[rr, cc] = z
                        weights[rr, cc] = 1.0
                        sigma_px = (sigma_mm / pitch_y, sigma_mm / pitch_x)
                        numerator = gaussian_filter(
                            values, sigma=sigma_px, mode='constant', cval=0.0,
                            truncate=float(truncate))
                        denominator = gaussian_filter(
                            weights, sigma=sigma_px, mode='constant', cval=0.0,
                            truncate=float(truncate))
                        valid_weight = denominator[rr, cc] > 1e-12
                        output[valid_weight] = (
                            numerator[rr[valid_weight], cc[valid_weight]] /
                            denominator[rr[valid_weight], cc[valid_weight]])
                        method = 'masked_matrix_gaussian'
                        used_matrix = True

        if not used_matrix:
            tree = cKDTree(np.column_stack([x, y]))
            k = max(2, min(int(max_neighbors), len(z)))
            max_values = 2_000_000
            batch_size = max(500, min(len(z), max_values // k))
            for start in range(0, len(z), batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise TaskCancelled()
                end = min(start + batch_size, len(z))
                distances, indices = tree.query(
                    np.column_stack([x[start:end], y[start:end]]),
                    k=k, distance_upper_bound=support_radius)
                if k == 1:
                    distances = distances[:, None]
                    indices = indices[:, None]
                valid_neighbor = np.isfinite(distances) & (indices < len(z))
                safe_indices = np.where(valid_neighbor, indices, 0)
                weights = np.exp(-0.5 * (distances / sigma_mm) ** 2)
                weights[~valid_neighbor] = 0.0
                weight_sum = weights.sum(axis=1)
                usable = weight_sum > 1e-12
                smoothed = (weights * z[safe_indices]).sum(axis=1)
                output_block = output[start:end]
                output_block[usable] = smoothed[usable] / weight_sum[usable]
                output[start:end] = output_block

        summary = {
            'enabled': True,
            'method': method,
            'sigma_mm': sigma_mm,
            'support_radius_mm': support_radius,
            'input_points': int(len(z)),
            'output_points': int(len(output)),
            'hole_filling': False,
            'max_neighbors': (None if used_matrix else int(max_neighbors)),
            'rms_change_um': float(np.sqrt(np.mean((output - z) ** 2)) * 1000.0),
        }
        return (output, summary) if return_summary else output

    @staticmethod
    def mad_filter(resids, k=3.5):
        """全局 MAD 鲁棒滤波：|r - median| <= k * 1.4826 * MAD"""
        med = np.median(resids)
        mad = np.median(np.abs(resids - med))
        if mad < 1e-12:
            return np.ones(len(resids), dtype=bool)
        return np.abs(resids - med) <= k * 1.4826 * mad

    @staticmethod
    def local_median_filter(x, y, resids, k=12, threshold_mm=0.005, global_threshold_mm=None):
        """局部中位数滤波（邻域比较）+ 全局残差兜底。

        判定逻辑：
        1) 局部条件：每个点与其 k 个最近邻（不含自身）的残差中位数比较，
           偏离超过 threshold_mm 判为局部异常。
        2) 全局兜底：每个点与全局残差中位数比较，
           偏离超过 global_threshold_mm 判为全局大离群。

        最终保留条件 = 局部条件通过 AND 全局兜底通过。

        这样既能保留“邻域比较”对孤立坏点的识别能力，
        也能避免边缘点/小簇异常点因为局部自洽而漏判。
        默认 global_threshold_mm 与 threshold_mm 相同，
        即 UI 中的“阈值(µm)”同时作为局部阈值和全局残差硬阈值。
        """
        n = len(resids)
        kk = min(k, n - 1)
        if kk < 1:
            return np.ones(n, dtype=bool)

        if global_threshold_mm is None:
            global_threshold_mm = threshold_mm

        xy = np.column_stack([x, y])
        tree = cKDTree(xy)

        # 局部一致性：分块查询最近邻，避免一次性生成 n*(k+1) 的超大索引/距离矩阵。
        local_ok = np.empty(n, dtype=bool)
        max_query_values = 2_000_000
        batch_size = max(1000, min(n, max_query_values // (kk + 1)))
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            _, idx = tree.query(xy[start:end], k=kk + 1)
            if idx.ndim == 1:
                idx = idx[:, None]
            local_med = np.median(resids[idx[:, 1:]], axis=1)
            local_ok[start:end] = np.abs(resids[start:end] - local_med) <= threshold_mm

        # 全局兜底：当前点与全局残差中位数比较
        # 用于拦截明显大离群点，尤其是边缘点或成簇坏点。
        global_med = np.median(resids)
        global_ok = np.abs(resids - global_med) <= global_threshold_mm

        return local_ok & global_ok

    @classmethod
    def sigma_clip_filter(cls, x, y, z, sigma_k=3.0, max_iter=5,
                          residual_order='order1', detrend_order=1,
                          cancel_event=None, convergence=1e-6,
                          allow_fallback=True, return_summary=False):
        """迭代σ裁剪（sigma-clipping）：
        反复用所选完整拟合面残差的标准差σ裁掉 |残差-均值| > sigma_k·σ 的点并重拟合，
        直到残差std收敛或本轮无新增剔除为止。

        特性：
        - 单调裁剪：每轮在上一轮保留集基础上继续剔除，只剔不回收，结果稳定可复现。
        - 多次重拟合：弥补单遍滤波“重拟合后新暴露的离群点抓不到”的短板。

        注意：σ来自全局残差，若工件面有真实弧度，残差里含真实信号，
        本档会把面型真正的峰/谷当离群点剪掉，导致PV被人为缩小；弧形面请优先用局部中位数滤波。
        """
        x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
        requested = str(residual_order or 'order1')
        if requested == 'follow_detrend':
            requested_order = max(1, min(3, int(detrend_order)))
        else:
            try:
                requested_order = int(requested.replace('order', ''))
            except (TypeError, ValueError):
                raise ValueError(f"不支持的σ残差模型: {residual_order!r}")
        if requested_order not in (1, 2, 3):
            raise ValueError(f"不支持的σ残差阶数: {requested_order}")

        n = len(z)
        keep = np.ones(n, dtype=bool)
        max_iter = max(1, int(max_iter))
        actual_order = requested_order
        fallback_reason = ''
        previous_sigma = None
        actual_iterations = 0
        for iteration in range(max_iter):
            if cancel_event is not None and cancel_event.is_set():
                raise TaskCancelled()
            minimum_points = len(TERM_POWERS[actual_order])
            while keep.sum() < minimum_points and actual_order > 1 and allow_fallback:
                if not fallback_reason:
                    fallback_reason = (
                        f"保留点数 {int(keep.sum())} 少于{actual_order}阶模型所需的"
                        f" {minimum_points} 点")
                actual_order -= 1
                minimum_points = len(TERM_POWERS[actual_order])
            if keep.sum() < minimum_points:
                break
            while True:
                try:
                    model = fit_polynomial_surface(
                        x[keep], y[keep], z[keep], actual_order,
                        check_stability=True)
                    break
                except ValueError as exc:
                    if not allow_fallback or actual_order <= 1:
                        raise ValueError(
                            f"当前点集几何分布不足以稳定进行{actual_order}阶σ残差拟合，"
                            "请改用较低阶模型。") from exc
                    if not fallback_reason:
                        fallback_reason = str(exc)
                    actual_order -= 1
            resid = z - evaluate_polynomial_surface(model, x, y)
            kept = resid[keep]
            sigma = np.std(kept)
            actual_iterations = iteration + 1
            if sigma < 1e-12:
                break
            mu = np.mean(kept)
            new_keep = keep & (np.abs(resid - mu) <= sigma_k * sigma)
            if new_keep.sum() < 3:
                break
            if int(new_keep.sum()) == int(keep.sum()):
                break  # 本轮无新增剔除，已收敛
            keep = new_keep
            if (previous_sigma is not None and
                    abs(previous_sigma - sigma) <= convergence * max(previous_sigma, 1e-12)):
                break
            previous_sigma = sigma
        summary = {
            'requested_model': requested,
            'requested_order': requested_order,
            'actual_model': f'order{actual_order}',
            'actual_order': actual_order,
            'fallback_reason': fallback_reason,
            'sigma_k': float(sigma_k),
            'max_iterations': max_iter,
            'actual_iterations': actual_iterations,
            'removed_points': int(n - keep.sum()),
            'retained_points': int(keep.sum()),
            'input_points': int(n),
        }
        return (keep, summary) if return_summary else keep

    @classmethod
    def filter_keep_mask(cls, xb, yb, zb, mode, k=12, threshold_mm=0.005,
                         sigma_k=3.0, sigma_iters=5,
                         sigma_residual_order='order1', detrend_order=1,
                         cancel_event=None, return_summary=False):
        """按滤波模式返回保留布尔掩码（相对输入点集）。
        mode: 0关闭 / 1 MAD全局 / 2 局部中位数 / 3 迭代σ裁剪。
        主界面与批量处理共用此分发，保证两条路径算法一致。"""
        n = len(zb)
        if mode == 0 or n <= 10:
            mask = np.ones(n, dtype=bool)
            return (mask, None) if return_summary else mask
        if mode == 3:
            return cls.sigma_clip_filter(
                xb, yb, zb, sigma_k=sigma_k, max_iter=sigma_iters,
                residual_order=sigma_residual_order, detrend_order=detrend_order,
                cancel_event=cancel_event, return_summary=return_summary)
        c0 = cls.fit_plane(xb, yb, zb)
        resids = zb - (c0[0] * xb + c0[1] * yb + c0[2])
        if mode == 1:
            mask = cls.mad_filter(resids, k=3.5)
            return (mask, None) if return_summary else mask
        if mode == 2:
            mask = cls.local_median_filter(xb, yb, resids, k=k,
                                           threshold_mm=threshold_mm,
                                           global_threshold_mm=threshold_mm)
            return (mask, None) if return_summary else mask
        mask = np.ones(n, dtype=bool)
        return (mask, None) if return_summary else mask

    @staticmethod
    def fit_plane(x, y, z):
        """中心化最小二乘拟合 Z = aX + bY + c，返回 [a, b, c]。
        中心化避免绝对 stage 大坐标导致的病态法方程。"""
        x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
        if (any(v.ndim != 1 for v in (x, y, z))
                or not (len(x) == len(y) == len(z)) or len(z) < 3
                or not all(np.isfinite(v).all() for v in (x, y, z))):
            raise ValueError("平面拟合至少需要3个有效且长度一致的XYZ点。")
        x0, y0 = x.mean(), y.mean()
        A = np.column_stack([x - x0, y - y0, np.ones_like(x)])
        sol, _, rank, _ = np.linalg.lstsq(A, z, rcond=None)
        if rank < 3:
            raise ValueError("点分布共线或退化，无法确定二维平面，请扩大ROI或检查扫描数据。")
        a, b, c0 = sol
        return np.array([a, b, c0 - a * x0 - b * y0])

    @classmethod
    def compute_plane_metrics(cls, fx, fy, fz):
        """对参与拟合的点拟合平面并计算指标。主界面与批量处理共用，口径一致。
        返回 dict: a/b/c/coeffs/mean_z/pv/ttv/rx/ry。
        PV 为相对最佳拟合平面的法向残差极差(µm)，TTV 为原始Z极差(µm)，Rx/Ry 单位 µrad。"""
        c = cls.fit_plane(fx, fy, fz)
        mean_z = float(np.mean(fz))
        ttv = float((np.max(fz) - np.min(fz)) * 1000)
        res_z = fz - (c[0] * fx + c[1] * fy + c[2])
        normal_factor = np.sqrt(c[0] ** 2 + c[1] ** 2 + 1.0)
        res_normal = res_z / normal_factor
        pv = float((np.max(res_normal) - np.min(res_normal)) * 1000)
        rms = float(np.sqrt(np.mean(res_normal ** 2)) * 1000)
        # Rx=arctan(+dZ/dY)，Ry=arctan(-dZ/dX)；姿态变换后的 Rx/Ry 物理正确性说明见
        # _apply_transform_pipeline 的文档（本机台 Z=厚度、不变号，旋转/翻面/平移七变换均正确）。
        rx = float(np.arctan(c[1]) * 1e6)
        ry = float(np.arctan(-c[0]) * 1e6)
        return {'a': float(c[0]), 'b': float(c[1]), 'c': float(c[2]), 'coeffs': c,
                'mean_z': mean_z, 'rms': rms, 'pv': pv, 'ttv': ttv, 'rx': rx, 'ry': ry}

    @staticmethod
    def _apply_transform_pipeline(x, y, z, pipeline):
        """基于包围盒的姿态变换（无状态，供主界面缓存与批量处理共用）。
        旋转/翻转均以数据包围盒为参照，坐标不从0开始也不会产生偏移。
        90°旋转采用【物料旋转】语义：CW90 顶部点->右侧；CCW90 顶部点->左侧。

        ===== Rx/Ry 物理正确性（已多方核验，2026-07 结论）=====
        本机台 Z = 厚度（标量），在任何重新装夹 / 翻面 / 镜像下都【不变号】。
        因此所有姿态变换只重排 (x,y)、保持 z 不变，这在物理上是正确的。

        以物料坐标系为基准输出：物料在整机里的位姿是固定真值，姿态变换是把
        实测数据搬回该固定坐标系；变换后 Rx/Ry 是同一物理楔形在新工件轴向下的正确再表达。
        （Rx=arctan(dZ/dY)·1e6，Ry=arctan(-dZ/dX)·1e6，见 compute_plane_metrics。）

        七个变换全部数学自洽 + 物理正确（|tilt|=sqrt(Rx²+Ry²) 在所有变换下恒定不变）：
          · 旋转 CW90/CCW90/ROT180 (det=+1)：工件在平面内转着重新装夹。
          · 反射 SWAP/FLIPX/FLIPY (det=-1)：工件绕【面内轴】翻面。
              对面高(干涉)测量翻面会使 Z→-Z、裸 XY 镜像会给错符号；
              但本机台 Z=厚度不变号，翻面就等于「XY 镜像 + 厚度不变」，
              正是这三个反射所做的事——故对本机台【合法且正确】。
          · 平移 ORIGIN：只改截距，斜率(倾斜)不变。
        闭合性：CW90×4=恒等、CW90×2=ROT180、FLIPX+FLIPY=ROT180。
        用法注意：①选对变换要对准实际装夹/翻面动作（|tilt| 幅值恒定，抓不出选错按钮的 90° 偏差）；
                  ②Rx/Ry 绝对正负号需用已知楔形方向的标准件实测标定一次。"""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        z = np.asarray(z, dtype=float)
        if not pipeline:
            return x, y, z
        # Transform expressions create their own outputs; z is immutable for
        # every supported pose operation and can remain a shared view.
        for action in pipeline:
            xmin, xmax = np.min(x), np.max(x)
            ymin, ymax = np.min(y), np.max(y)
            if action == "ROT180":
                x, y = xmin + xmax - x, ymin + ymax - y
            elif action == "CW90":      # 物料顺时针旋转90°: 顶部点 -> 右侧（主动旋转）
                x, y = xmin + (y - ymin), ymin + (xmax - x)
            elif action == "CCW90":     # 物料逆时针旋转90°: 顶部点 -> 左侧（主动旋转）
                x, y = xmin + (ymax - y), ymin + (x - xmin)
            elif action == "SWAP":
                x, y = y, x
            elif action == "FLIPX":     # 前后翻转 = Y 镜像
                y = ymin + ymax - y
            elif action == "FLIPY":     # 左右翻转 = X 镜像
                x = xmin + xmax - x
            elif action == "ORIGIN(0,0)":
                x = x - xmin
                y = y - ymin
        return x, y, z
