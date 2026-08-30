import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from matplotlib.backend_bases import MouseEvent
from matplotlib.figure import Figure

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
import surface_analyzer.mixins.roi as roi_module
from surface_analyzer.polynomial import fit_polynomial_surface


class V453InteractionAndBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _batch_params(roi_shapes=None, roi_enabled=False):
        return {
            'x_col': 'X', 'y_col': 'Y', 'z_col': 'Z',
            'ux': 1.0, 'uy': 1.0, 'uz': 1.0,
            'pipeline': [], 'pipeline_text': '原始状态',
            'mode': 0, 'k': 12, 'threshold_mm': 0.005,
            'sigma_k': 3.0, 'sigma_iters': 5,
            'filter_text': '关闭', 'display_surface_mode': 'raw',
            'roi_enabled': roi_enabled, 'roi_shapes': roi_shapes or [],
        }

    @staticmethod
    def _grid_frame(reverse=False):
        yy, xx = np.mgrid[0:4, 0:4]
        frame = pd.DataFrame({
            'X': xx.ravel().astype(float),
            'Y': yy.ravel().astype(float),
            'Z': 1.0 + 0.0002 * xx.ravel() + 0.0003 * yy.ravel(),
        })
        return frame.iloc[::-1].reset_index(drop=True) if reverse else frame

    def test_right_click_is_bound_once_to_each_2d_canvas_only(self):
        with patch.object(SurfaceAnalyzerPro, 'on_canvas_click', autospec=True) as handler:
            window = SurfaceAnalyzerPro()
            for ax in (window.canvas.ax_xy, window.canvas.ax_xz, window.canvas.ax_yz):
                canvas = ax.figure.canvas
                canvas.draw()
                x, y = ax.transAxes.transform((0.5, 0.5))
                event = MouseEvent('button_press_event', canvas, x, y, button=3)
                before = handler.call_count
                canvas.callbacks.process('button_press_event', event)
                self.assertEqual(handler.call_count, before + 1)

            ax = window.canvas.ax3d
            canvas = ax.figure.canvas
            canvas.draw()
            x, y = ax.transAxes.transform((0.5, 0.5))
            event = MouseEvent('button_press_event', canvas, x, y, button=3)
            before = handler.call_count
            canvas.callbacks.process('button_press_event', event)
            self.assertEqual(handler.call_count, before)
            window.close()

    def test_context_roi_creation_is_rectangular_even_with_smart_combo(self):
        window = SurfaceAnalyzerPro()
        window.df_raw = self._grid_frame()
        n = len(window.df_raw)
        window.manual_mask = np.ones(n, dtype=bool)
        window.temp_selected_mask = np.ones(n, dtype=bool)
        window.active_idx = np.arange(n)
        window.pending_delete_operation = {
            'view': 'YZ', 'display_mode': 'raw_z_mm',
            'bounds': {'x_min': 0.0, 'x_max': 2.0,
                       'y_min': 0.99, 'y_max': 1.01},
        }
        window.cb_roi_shape.setCurrentIndex(1)
        window.set_temp_selection_as_roi()
        self.assertEqual(window.roi_shapes[-1]['type'], 'rect')
        self.assertEqual(window.roi_shapes[-1]['view'], 'YZ')
        window.close()

    def test_batch_rebinds_smart_seed_and_isolates_runtime_caches(self):
        window = SurfaceAnalyzerPro()
        frames = {'a.csv': self._grid_frame(False), 'b.csv': self._grid_frame(True)}
        roi = {
            'id': 1, 'type': 'smart_face', 'enabled': True,
            'seed_x': 0.0, 'seed_y': 0.0, 'seed_index': 7,
            'z_tolerance_mm': 0.02, 'smart_algorithm_version': 3,
            'smart_mode': 'surface_following', 'sensitivity': 'standard',
            'connectivity': 'auto_xy',
        }
        params = self._batch_params([roi], True)
        saved_topology = {'gui': object()}
        saved_masks = {'gui': object()}
        window._smart_topology_cache = saved_topology
        window._smart_roi_mask_cache = saved_masks
        real_build = roi_module.build_adaptive_topology
        real_grow = roi_module.grow_surface_roi
        seeds = []
        original_keep = window._smart_face_keep_mask_for_arrays

        def read(path):
            window.import_info = {'valid_rows': 16, 'import_rows': 16}
            return frames[Path(path).name].copy()

        def capture_keep(x, y, z, shape, **kwargs):
            seeds.append(int(shape['seed_index']))
            return original_keep(x, y, z, shape, **kwargs)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(window, '_read_table', side_effect=read), \
                patch.object(window, '_render_report_figure', return_value=Figure()), \
                patch.object(window, '_smart_face_keep_mask_for_arrays', side_effect=capture_keep), \
                patch.object(roi_module, 'build_adaptive_topology', wraps=real_build) as build, \
                patch.object(roi_module, 'grow_surface_roi', wraps=real_grow) as grow:
            payload = window._run_batch(['a.csv', 'b.csv'], directory, params)
        self.assertFalse(payload['cancelled'])
        self.assertEqual(seeds[0], 0)
        self.assertIn(15, seeds)
        self.assertEqual(build.call_count, 2)
        self.assertEqual(grow.call_count, 2)
        self.assertIs(window._smart_topology_cache, saved_topology)
        self.assertIs(window._smart_roi_mask_cache, saved_masks)
        window.close()

    def test_batch_residual_roi_refits_each_dataset(self):
        window = SurfaceAnalyzerPro()
        yy, xx = np.mgrid[-2:3, -2:3]
        x = xx.ravel().astype(float)
        y = yy.ravel().astype(float)
        surfaces = {
            1: (1.0 + 0.001 * x + 0.002 * y,
                1.2 - 0.004 * x + 0.006 * y),
            2: (1.0 + 0.001 * x ** 2 + 0.002 * x * y,
                1.2 - 0.004 * x ** 2 + 0.006 * y ** 2),
            3: (1.0 + 0.001 * x ** 3 + 0.002 * x * y,
                1.2 - 0.004 * x ** 3 + 0.006 * x * y ** 2),
        }
        for order, (z_a, z_b) in surfaces.items():
            with self.subTest(order=order):
                stale_model = fit_polynomial_surface(x, y, z_a, order)
                display_mode = 'detrended_um' if order == 1 else f'residual_{order}_um'
                roi = {
                    'type': 'rect', 'view': 'XZ', 'enabled': True,
                    'cx': 0.0, 'cy': 0.0, 'width': 10.0, 'height': 0.01,
                    'display_mode': display_mode,
                    'display_polynomial_model': stale_model,
                }
                prepared = window._batch_roi_shapes_for_dataset([roi], x, y, z_b)[0]
                current_residual = window._gate_plot_z(x, y, z_b, prepared)
                stale_residual = window._gate_plot_z(x, y, z_b, roi)
                self.assertLess(float(np.max(np.abs(current_residual))), 1e-8)
                self.assertGreater(float(np.max(np.abs(stale_residual))), 1.0)
        window.close()

    def test_batch_matches_single_file_metrics_for_same_recipe(self):
        window = SurfaceAnalyzerPro()
        frame = self._grid_frame()
        x = frame['X'].to_numpy()
        y = frame['Y'].to_numpy()
        z = frame['Z'].to_numpy()
        roi = {'type': 'rect', 'view': 'XY', 'enabled': True,
               'cx': 1.5, 'cy': 1.5, 'width': 2.2, 'height': 2.2}
        expected_mask = window._roi_keep_mask_for_arrays(x, y, z, [roi], True)
        expected_idx = np.flatnonzero(expected_mask)
        expected = window.compute_plane_metrics(
            x[expected_idx], y[expected_idx], z[expected_idx])

        def read(_path):
            window.import_info = {'valid_rows': len(frame), 'import_rows': len(frame)}
            return frame.copy()

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(window, '_read_table', side_effect=read), \
                patch.object(window, '_render_report_figure', return_value=Figure()):
            payload = window._run_batch(
                ['same.csv'], directory, self._batch_params([roi], True))
            summary = pd.read_csv(payload['summary_path']).iloc[0]
        self.assertEqual(int(summary['ROI保留']), len(expected_idx))
        self.assertEqual(int(summary['参与拟合']), len(expected_idx))
        self.assertAlmostEqual(float(summary['平均Z_mm']), round(expected['mean_z'], 6))
        self.assertAlmostEqual(float(summary['PV_um']), round(expected['pv'], 3))
        self.assertAlmostEqual(float(summary['TTV_um']), round(expected['ttv'], 3))
        self.assertAlmostEqual(float(summary['Rx_urad']), round(expected['rx'], 2))
        self.assertAlmostEqual(float(summary['Ry_urad']), round(expected['ry'], 2))
        window.close()

    def test_summary_paths_do_not_overwrite_and_cancel_writes_partial(self):
        window = SurfaceAnalyzerPro()
        with tempfile.TemporaryDirectory() as directory:
            first = window._unique_batch_summary_path(directory)
            first.write_text('first', encoding='utf-8')
            second = window._unique_batch_summary_path(directory)
            self.assertEqual(second.name, 'result_batch_summary_2.csv')

            cancel = threading.Event()
            calls = {'count': 0}

            def read(_path):
                calls['count'] += 1
                if calls['count'] == 1:
                    cancel.set()
                window.import_info = {'valid_rows': 16, 'import_rows': 16}
                return self._grid_frame()

            with patch.object(window, '_read_table', side_effect=read), \
                    patch.object(window, '_render_report_figure', return_value=Figure()):
                payload = window._run_batch(
                    ['a.csv', 'b.csv'], directory, self._batch_params(),
                    cancel_event=cancel)
            self.assertTrue(payload['cancelled'])
            self.assertIn('PARTIAL', Path(payload['summary_path']).name)
            summary = pd.read_csv(payload['summary_path'])
            self.assertEqual(summary.loc[0, '状态'], 'ok')
            self.assertIn('cancelled', str(summary.loc[1, '状态']))
        window.close()

    def test_batch_report_xy_overview_and_detail_views(self):
        window = SurfaceAnalyzerPro()
        x = np.arange(6, dtype=float)
        y = np.arange(6, dtype=float) * 0.5
        z = 1.0 + x * 0.001
        roi_mask = np.array([False, True, True, True, False, False])
        # Plane fit needs at least three non-collinear points for the report.
        active = np.array([0, 1, 2, 3])
        y = np.array([0.0, 0.0, 1.0, 1.0, 2.0, 2.0])
        metrics = window.compute_plane_metrics(x[active], y[active], z[active])
        fig = window._render_report_figure(
            'demo.csv', x, y, z, active, metrics, 0, '原始状态', '关闭', {},
            overview_idx=np.arange(6), roi_mask_all=roi_mask)
        by_title = {ax.get_title(): ax for ax in fig.axes if ax.get_title()}
        xy_ax = by_title['XY 俯视分布']
        self.assertEqual(len(xy_ax.collections[0].get_offsets()), 6)
        self.assertEqual(len(xy_ax.collections[1].get_offsets()), 3)
        self.assertEqual(len(by_title['X-Z投影'].collections[0].get_offsets()), 4)
        self.assertEqual(len(by_title['Y-Z投影'].collections[0].get_offsets()), 4)
        window.close()

    def test_scroll_zoom_changes_only_view_limits(self):
        window = SurfaceAnalyzerPro()
        window.active_idx = np.array([1, 2, 3])
        window.manual_mask = np.array([True, True, True, False])
        window._effective_roi_mask_cache = np.array([False, True, True, False])
        state = (window.active_idx.copy(), window.manual_mask.copy(),
                 window._effective_roi_mask_cache.copy())

        for ax in (window.canvas.ax_xy, window.canvas.ax_xz, window.canvas.ax_yz):
            ax.set_xlim(0.0, 10.0); ax.set_ylim(-5.0, 5.0)
            before = (ax.get_xlim(), ax.get_ylim())
            with patch.object(ax.figure.canvas, 'draw_idle') as draw, \
                    patch.object(window, 'update_analysis') as update:
                window.on_canvas_scroll(SimpleNamespace(
                    inaxes=ax, button='up', step=1, xdata=5.0, ydata=0.0,
                    canvas=ax.figure.canvas))
            self.assertNotEqual(before, (ax.get_xlim(), ax.get_ylim()))
            draw.assert_called_once()
            update.assert_not_called()

        ax = window.canvas.ax3d
        ax.set_xlim3d(0.0, 10.0); ax.set_ylim3d(0.0, 8.0); ax.set_zlim3d(0.0, 2.0)
        before = (ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d())
        elev, azim = ax.elev, ax.azim
        window.on_canvas_scroll(SimpleNamespace(
            inaxes=ax, button='up', step=1, xdata=None, ydata=None,
            canvas=ax.figure.canvas))
        self.assertNotEqual(before, (ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()))
        self.assertEqual((elev, azim), (ax.elev, ax.azim))
        self.assertTrue(np.array_equal(state[0], window.active_idx))
        self.assertTrue(np.array_equal(state[1], window.manual_mask))
        self.assertTrue(np.array_equal(state[2], window._effective_roi_mask_cache))
        window.close()


if __name__ == '__main__':
    unittest.main()
