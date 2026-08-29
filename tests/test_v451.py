import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.analysis import AnalysisMixin
import surface_analyzer.mixins.roi as roi_module
import surface_analyzer.smart_roi as smart_module
from surface_analyzer.smart_roi import build_adaptive_topology, grow_surface_roi


def _grow_v450_reference(x, y, z, seed_x, seed_y, tolerance_mm, topology,
                         sensitivity='standard'):
    """Exact V4.5.0 surface-following loop retained only for regression comparison."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    adjacency = topology['adjacency']
    seed = int(np.argmin((x - float(seed_x)) ** 2 + (y - float(seed_y)) ** 2))
    config = smart_module.SENSITIVITY[str(sensitivity)]
    tolerance = max(float(tolerance_mm), 1e-12) * float(config['residual_factor'])
    neighborhood = smart_module._graph_neighborhood(adjacency, seed, target=36, max_depth=5)
    seed_plane, seed_normal = smart_module._robust_local_plane(x, y, z, neighborhood)
    fits = 1
    plane_cache = {seed: (seed_plane, seed_normal)}

    def local_plane(index):
        nonlocal fits
        value = plane_cache.get(index)
        if value is None:
            local = smart_module._graph_neighborhood(adjacency, index, target=24, max_depth=4)
            value = smart_module._robust_local_plane(x, y, z, local)
            plane_cache[index] = value
            fits += 1
        return value

    visited = np.zeros(len(x), dtype=bool); visited[seed] = True
    queue = deque([seed])
    normal_limit = np.deg2rad(float(config['normal_deg']))
    while queue:
        current = queue.popleft()
        current_plane, current_normal = local_plane(current)
        if current_plane is None:
            continue
        for neighbor in adjacency[current]:
            neighbor = int(neighbor)
            if visited[neighbor]:
                continue
            predicted = (current_plane[0] * x[neighbor] + current_plane[1] * y[neighbor]
                         + current_plane[2])
            if abs(float(z[neighbor] - predicted)) > tolerance:
                continue
            neighbor_plane, neighbor_normal = local_plane(neighbor)
            if neighbor_plane is None:
                continue
            cosine = float(np.clip(np.dot(current_normal, neighbor_normal), -1.0, 1.0))
            if np.arccos(cosine) > normal_limit:
                continue
            visited[neighbor] = True
            queue.append(neighbor)
    return visited, fits


class V451CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _write_bow_surface(path):
        yy, xx = np.mgrid[0:24, 0:32]
        x = xx.ravel() * 0.08
        y = yy.ravel() * 0.10
        z = 1.0 + 0.0015 * (x - np.mean(x)) ** 2 + 0.001 * (y - np.mean(y)) ** 2
        lines = ['X,Y,Z']
        lines.extend(f'{vx:.8f},{vy:.8f},{vz:.10f}' for vx, vy, vz in zip(x, y, z))
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    @staticmethod
    def _roi(window, seed_x, seed_y):
        tx, ty, tz = window.get_final_transformed_data(window.df_raw)
        seed = int(np.argmin((tx - seed_x) ** 2 + (ty - seed_y) ** 2))
        return {
            'type': 'smart_face',
            'seed_x': float(tx[seed]), 'seed_y': float(ty[seed]), 'seed_z': float(tz[seed]),
            'z_tolerance_mm': 0.01,
            'smart_algorithm_version': 2,
            'smart_mode': 'surface_following',
            'sensitivity': 'standard',
            'connectivity': 'auto_xy',
            'xy_radius_mm': 0.0,
        }

    def test_smart_roi_build_and_grow_are_not_repeated_by_ui_operations(self):
        real_build = roi_module.build_adaptive_topology
        real_grow = roi_module.grow_surface_roi
        calls = {'build': 0, 'grow': 0}

        def counted_build(*args, **kwargs):
            calls['build'] += 1
            return real_build(*args, **kwargs)

        def counted_grow(*args, **kwargs):
            calls['grow'] += 1
            return real_grow(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(roi_module, 'build_adaptive_topology', side_effect=counted_build), \
                patch.object(roi_module, 'grow_surface_roi', side_effect=counted_grow):
            path = Path(directory) / 'bow.csv'
            self._write_bow_surface(path)
            window = SurfaceAnalyzerPro()
            self.assertTrue(window.load_path(path))
            tx, ty, tz = window.get_final_transformed_data(window.df_raw)
            matrix_rc = window._matrix_rc_for_current_data()

            roi1 = self._roi(window, float(np.mean(tx)), float(np.mean(ty)))
            first_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, roi1, matrix_rc=matrix_rc)
            self.assertEqual(calls, {'build': 1, 'grow': 1})
            window._complete_smart_face_roi(
                roi1, first_mask, tx, ty, tz, matrix_rc,
                topology_key=window._smart_topology_cache_key(
                    tx, ty, tz, matrix_rc, roi1['sensitivity']))
            self.assertEqual(calls, {'build': 1, 'grow': 1})

            cached_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, window.roi_shapes[0], matrix_rc=matrix_rc)
            self.assertTrue(np.array_equal(first_mask, cached_mask))

            window.on_select(SimpleNamespace(xdata=float(tx.min()), ydata=float(ty.min())),
                             SimpleNamespace(xdata=float(np.percentile(tx, 5)),
                                             ydata=float(np.percentile(ty, 5))), 'XY')
            if int(window.temp_selected_mask.sum()) > 0:
                window.apply_manual_deletion()
                window.undo_manual_deletion()
            for mode in ('raw', 'residual_1', 'residual_2', 'residual_3'):
                index = window.cb_surface_display.findData(mode)
                window.cb_surface_display.setCurrentIndex(index)
                window._on_surface_display_changed()
            window.update_plots_only()
            window._refresh_roi_ui(update=False)
            window.tabs.setCurrentIndex(min(1, window.tabs.count() - 1))
            QApplication.processEvents()
            self.assertEqual(calls, {'build': 1, 'grow': 1})

            roi2 = self._roi(window, float(np.percentile(tx, 65)), float(np.percentile(ty, 55)))
            second_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, roi2, matrix_rc=matrix_rc)
            self.assertEqual(calls['build'], 1)
            self.assertEqual(calls['grow'], 2)
            window._complete_smart_face_roi(
                roi2, second_mask, tx, ty, tz, matrix_rc,
                topology_key=window._smart_topology_cache_key(
                    tx, ty, tz, matrix_rc, roi2['sensitivity']))
            self.assertEqual(calls, {'build': 1, 'grow': 2})
            window.close()

    def test_coarse_to_fine_matches_smooth_components_and_reduces_plane_fits(self):
        rows, cols = 80, 100
        yy, xx = np.mgrid[0:rows, 0:cols]
        x1 = xx.ravel() * 0.04
        y1 = yy.ravel() * 0.05
        z1 = (1.0 + 0.0012 * (x1 - np.mean(x1)) ** 2
              + 0.0008 * (y1 - np.mean(y1)) ** 2)
        # A distant overlapping-shape island must remain disconnected.
        x = np.concatenate([x1, x1 + 8.0])
        y = np.concatenate([y1, y1])
        z = np.concatenate([z1, z1 + 0.08])
        topology = build_adaptive_topology(x, y, sensitivity='standard')
        old_mask, old_fits = _grow_v450_reference(
            x, y, z, np.mean(x1), np.mean(y1), 0.01, topology)
        stats = {}
        new_mask = grow_surface_roi(
            x, y, z, np.mean(x1), np.mean(y1), 0.01, topology,
            mode='surface_following', sensitivity='standard', stats=stats)
        intersection = int(np.sum(old_mask & new_mask))
        union = int(np.sum(old_mask | new_mask))
        self.assertGreaterEqual(intersection / max(union, 1), 0.995)
        self.assertEqual(int(new_mask[:len(x1)].sum()), len(x1))
        self.assertEqual(int(new_mask[len(x1):].sum()), 0)
        self.assertGreater(stats['fast_accept'], int(len(x1) * 0.8))
        self.assertLess(stats['local_plane_fits'], old_fits * 0.35)

    def test_coarse_to_fine_follows_bow_but_stops_step_and_candidate_hole(self):
        rows, cols = 60, 90
        yy, xx = np.mgrid[0:rows, 0:cols]
        x = xx.ravel() * 0.05
        y = yy.ravel() * 0.06
        z = 1.0 + 0.001 * (x - 1.2) ** 2 + 0.0015 * (y - 1.5) ** 2
        z = z + np.where(xx.ravel() >= 58, 0.05, 0.0)
        candidate = np.ones(len(x), dtype=bool)
        hole = ((xx.ravel() >= 20) & (xx.ravel() <= 25)
                & (yy.ravel() >= 22) & (yy.ravel() <= 30))
        candidate[hole] = False
        topology = build_adaptive_topology(
            x, y, matrix_rc=(yy.ravel(), xx.ravel()), sensitivity='standard')
        stats = {}
        seed_index = 30 * cols + 10
        keep = grow_surface_roi(
            x, y, z, x[seed_index], y[seed_index], 0.008, topology,
            mode='surface_following', sensitivity='standard',
            candidate_mask=candidate, stats=stats)
        self.assertEqual(int(np.sum(keep & (xx.ravel() >= 58))), 0)
        self.assertEqual(int(np.sum(keep & hole)), 0)
        self.assertGreater(int(np.sum(keep & (xx.ravel() < 58))), int(rows * 58 * 0.9))
        self.assertGreater(stats['fast_reject'], 0)
        self.assertGreater(stats['selected'], 0)

    def test_v403_golden_step_demo_preserves_measurement_metrics(self):
        """The long-used V4.0.3 release demo is the calculation golden sample."""
        path = (Path(__file__).resolve().parents[1] / 'demo_data'
                / 'V3.9_StepDemo_XYZ_points.csv')
        frame = pd.read_csv(path, encoding='utf-8-sig')
        measure = frame.loc[frame['Region'].eq('measure')]
        x = measure['X'].to_numpy(dtype=float)
        y = measure['Y'].to_numpy(dtype=float)
        z = measure['Z'].to_numpy(dtype=float) * 1e-3
        expected = {
            0: (9990, 76.64709166344717, 50.83765405181541,
                87.00100859572585, 86.95604076754779, 2.6412270043697212),
            1: (9969, 84.4660502121436, 57.07610167148963,
                5.673462124705125, 5.459594006088254, 0.7978453402959608),
            2: (9972, 83.95117091370226, 57.1820337622445,
                6.359967533560135, 6.255248881201983, 0.799457797388855),
            3: (9935, 85.8995859073149, 57.44059521600744,
                5.111752799455305, 4.672557210980752, 0.7855656636186766),
        }
        for mode, golden in expected.items():
            with self.subTest(filter_mode=mode):
                keep = AnalysisMixin.filter_keep_mask(
                    x, y, z, mode, k=12, threshold_mm=0.005,
                    sigma_k=3.0, sigma_iters=5)
                metrics = AnalysisMixin.compute_plane_metrics(x[keep], y[keep], z[keep])
                point_count, rx, ry, ttv, pv, rms = golden
                self.assertEqual(int(keep.sum()), point_count)
                for key, value in (('rx', rx), ('ry', ry), ('ttv', ttv),
                                   ('pv', pv), ('rms', rms)):
                    self.assertAlmostEqual(metrics[key], value, delta=1e-9)

    def test_multiview_seed_uses_the_rendered_layer_index(self):
        window = SurfaceAnalyzerPro()
        x = np.array([0.0, 1.0, 0.0, 1.0] * 2)
        y = np.array([0.0, 0.0, 1.0, 1.0] * 2)
        z = np.array([1.0] * 4 + [1.08] * 4)
        window.df_raw = pd.DataFrame({'Z': z, 'X': x, 'Y': y})
        window.manual_mask = np.ones(len(z), dtype=bool)
        window.active_idx = np.arange(len(z))
        window.selection_mode = 'roi_smart'
        window._smart_seed_view_indices = {
            'XY': np.arange(len(z)), 'XZ': np.arange(len(z)), 'YZ': np.arange(len(z))}
        captured = []

        def capture(px, py, seed_index=None, seed_view='XY'):
            captured.append((seed_index, seed_view))

        with patch.object(window, 'add_smart_face_roi_from_seed', side_effect=capture):
            for ax, view, point in (
                    (window.canvas.ax_xz, 'XZ', (1.0, 1.08)),
                    (window.canvas.ax_yz, 'YZ', (1.0, 1.08))):
                ax.set_xlim(-0.1, 1.1); ax.set_ylim(0.98, 1.10)
                window.canvas.draw()
                sx, sy = ax.transData.transform(point)
                window.on_canvas_click(SimpleNamespace(
                    inaxes=ax, xdata=point[0], ydata=point[1],
                    x=sx, y=sy, button=1))
                self.assertEqual(captured[-1][1], view)
                self.assertGreaterEqual(captured[-1][0], 4)
        window.close()

    def test_small_smart_roi_starts_in_background(self):
        window = SurfaceAnalyzerPro()
        yy, xx = np.mgrid[0:10, 0:12]
        window.df_raw = pd.DataFrame({
            'Z': np.ones(xx.size), 'X': xx.ravel() * 0.1, 'Y': yy.ravel() * 0.1})
        window.manual_mask = np.ones(xx.size, dtype=bool)
        window.active_idx = np.arange(xx.size)
        with patch.object(window, '_run_background_task', return_value=True) as background:
            window.add_smart_face_roi_from_seed(0.5, 0.5)
        background.assert_called_once()
        window.close()


if __name__ == '__main__':
    unittest.main()
