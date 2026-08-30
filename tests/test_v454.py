import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.polynomial import evaluate_polynomial_surface
from surface_analyzer.workers import FunctionWorker


class V454BatchAndViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _frame():
        yy, xx = np.mgrid[0:4, 0:4]
        return pd.DataFrame({
            'X': xx.ravel().astype(float),
            'Y': yy.ravel().astype(float),
            'Z': 1.0 + 2e-5 * xx.ravel() - 4e-5 * yy.ravel(),
        })

    @staticmethod
    def _params(roi_shapes=None, roi_enabled=False, mode=0):
        return {
            'x_col': 'X', 'y_col': 'Y', 'z_col': 'Z',
            'ux': 1.0, 'uy': 1.0, 'uz': 1.0,
            'pipeline': [], 'pipeline_text': '原始状态',
            'mode': mode, 'k': 12, 'threshold_mm': 0.005,
            'sigma_k': 3.0, 'sigma_iters': 5,
            'filter_text': '测试', 'display_surface_mode': 'raw',
            'roi_enabled': roi_enabled, 'roi_shapes': roi_shapes or [],
        }

    def test_batch_filter_under_three_points_is_fail(self):
        window = SurfaceAnalyzerPro()
        frame = self._frame()

        def read(_):
            window.import_info = {'valid_rows': len(frame), 'import_rows': len(frame)}
            return frame.copy()

        keep = np.zeros(len(frame), dtype=bool); keep[:2] = True
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(window, '_read_table', side_effect=read), \
                patch.object(window, 'filter_keep_mask', return_value=keep), \
                patch.object(window, '_render_report_figure', return_value=Figure()):
            payload = window._run_batch(
                ['filter.csv'], directory, self._params(mode=1))
            summary = pd.read_csv(payload['summary_path']).iloc[0]
        self.assertEqual(payload['results'][0]['status'], 'fail')
        self.assertIn('少于 3', payload['results'][0]['error'])
        self.assertEqual(summary['状态'], 'fail')
        window.close()

    def test_batch_residual_roi_fits_smart_base_only_and_updates_count(self):
        window = SurfaceAnalyzerPro()
        yy, xx = np.mgrid[-2:3, -2:3]
        x = np.concatenate([xx.ravel(), xx.ravel()]).astype(float)
        y = np.concatenate([yy.ravel(), yy.ravel()]).astype(float)
        z_a = 1.0 + 0.001 * x[:25] ** 2 + 0.002 * y[:25]
        z_b = 2.0 - 0.03 * x[25:] + 0.02 * y[25:]
        z = np.concatenate([z_a, z_b])
        smart = {'type': 'smart_face', 'enabled': True, 'seed_x': 0.0,
                 'seed_y': 0.0, 'seed_index': 40, 'point_count_at_create': 999}
        residual = {'type': 'rect', 'view': 'XZ', 'enabled': True,
                    'cx': 0.0, 'cy': 0.0, 'width': 10.0, 'height': 0.2,
                    'display_mode': 'residual_2_um'}
        smart_mask = np.zeros(len(z), dtype=bool); smart_mask[:25] = True
        original = [dict(smart), dict(residual)]
        with patch.object(window, '_smart_face_keep_mask_for_arrays', return_value=smart_mask):
            prepared = window._batch_roi_shapes_for_dataset(original, x, y, z)
        model = prepared[1]['display_polynomial_model']
        residual_a = (z_a - evaluate_polynomial_surface(model, x[:25], y[:25])) * 1000.0
        self.assertLess(float(np.max(np.abs(residual_a))), 1e-7)
        self.assertEqual(prepared[0]['point_count_at_create'], 25)
        self.assertEqual(original[0]['point_count_at_create'], 999)
        window.close()

    def test_temp_selection_redraw_preserves_all_viewports(self):
        window = SurfaceAnalyzerPro()
        window.df_raw = self._frame()
        n = len(window.df_raw)
        window.manual_mask = np.ones(n, dtype=bool)
        window.temp_selected_mask = np.zeros(n, dtype=bool)
        window.update_analysis()
        window.canvas.ax_xy.set_xlim(0.5, 2.5); window.canvas.ax_xy.set_ylim(0.4, 2.4)
        window.canvas.ax_xz.set_xlim(0.6, 2.6); window.canvas.ax_xz.set_ylim(0.999, 1.001)
        window.canvas.ax_yz.set_xlim(0.7, 2.7); window.canvas.ax_yz.set_ylim(0.999, 1.001)
        window.canvas.ax3d.set_xlim3d(0.3, 2.3); window.canvas.ax3d.set_ylim3d(0.2, 2.2)
        window.canvas.ax3d.set_zlim3d(0.999, 1.001); window.canvas.ax3d.view_init(22, -41)
        before = self._limits(window)
        window.temp_selected_mask[0] = True
        with patch.object(window, 'update_analysis') as update, \
                patch.object(window, '_smart_face_keep_mask_for_arrays') as grow:
            window.update_plots_only()
            window.cancel_temp_selection()
        after = self._limits(window)
        for old, new in zip(before, after):
            np.testing.assert_allclose(old, new)
        update.assert_not_called(); grow.assert_not_called()
        window.close()

    @staticmethod
    def _limits(window):
        return (window.canvas.ax_xy.get_xlim(), window.canvas.ax_xy.get_ylim(),
                window.canvas.ax_xz.get_xlim(), window.canvas.ax_xz.get_ylim(),
                window.canvas.ax_yz.get_xlim(), window.canvas.ax_yz.get_ylim(),
                window.canvas.ax3d.get_xlim3d(), window.canvas.ax3d.get_ylim3d(),
                window.canvas.ax3d.get_zlim3d(), (window.canvas.ax3d.elev, window.canvas.ax3d.azim))

    def test_no_roi_report_has_no_gray_overlay(self):
        window = SurfaceAnalyzerPro()
        frame = self._frame(); x = frame.X.to_numpy(); y = frame.Y.to_numpy(); z = frame.Z.to_numpy()
        idx = np.arange(len(frame)); metrics = window.compute_plane_metrics(x, y, z)
        fig = window._render_report_figure(
            'demo.csv', x, y, z, idx, metrics, 0, '原始状态', '关闭', {},
            roi_info={'enabled': False, 'summary': '关闭', 'shapes': [], 'roi_enabled': False},
            overview_idx=idx, roi_mask_all=np.ones(len(frame), dtype=bool))
        xy = next(ax for ax in fig.axes if ax.get_title() == 'XY 俯视分布')
        self.assertEqual(len(xy.collections), 1)
        window.close()

    def test_cancelled_result_is_delivered_and_finish_shows_partial_counts(self):
        delivered = []
        worker = FunctionWorker(lambda _p, cancel: {'ok': True}, True)
        worker.cancel_event.set(); worker.succeeded.connect(delivered.append); worker.run()
        self.assertEqual(delivered, [{'ok': True}])

        window = SurfaceAnalyzerPro()
        payload = {'cancelled': True, 'outdir': 'D:/results',
                   'summary_path': 'D:/results/result_batch_summary_PARTIAL.csv',
                   'results': [
                       {'status': 'ok', 'file': 'a.csv'},
                       {'status': 'fail', 'file': 'b.csv', 'error': 'bad'},
                       {'status': 'cancelled', 'file': 'c.csv'},
                   ]}
        with patch.object(QMessageBox, 'warning') as warning:
            window._finish_batch_process(payload)
        message = warning.call_args.args[2]
        self.assertIn('成功 1 / 失败 1 / 未处理 1', message)
        self.assertIn('result_batch_summary_PARTIAL.csv', message)
        window.close()

    def test_plane_equation_summary_uses_scientific_slope_precision(self):
        window = SurfaceAnalyzerPro(); frame = self._frame()

        def read(_):
            window.import_info = {'valid_rows': len(frame), 'import_rows': len(frame)}
            return frame.copy()

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(window, '_read_table', side_effect=read), \
                patch.object(window, '_render_report_figure', return_value=Figure()):
            payload = window._run_batch(['plane.csv'], directory, self._params())
            equation = str(pd.read_csv(payload['summary_path']).iloc[0]['平面方程'])
        self.assertIn('e-', equation.lower())
        self.assertNotIn('0.0000X', equation)
        window.close()

    def test_precitec_80mm_wafer_coldplate_demo_imports_with_expected_step(self):
        window = SurfaceAnalyzerPro()
        path = (Path(__file__).resolve().parents[1] / 'demo_data' /
                'V4.5.4_Precitec_80mm_Wafer_on_ColdPlate_Demo.dat')
        frame = window._read_table(str(path))
        x = pd.to_numeric(frame['X Pos [mm]']).to_numpy()
        y = pd.to_numeric(frame['Y Pos [mm]']).to_numpy()
        z = pd.to_numeric(frame['Thickness 1']).to_numpy()
        radius = np.hypot(x, y)
        wafer = z[radius <= 15.0]
        plate = z[(radius >= 20.0) & (radius <= 38.0)]
        self.assertEqual(window.import_info['source_format'],
                         'Precitec FSS Explorer SCAN PATH DATA')
        self.assertEqual(window.import_info['expected_points'], 161 * 161)
        self.assertEqual(window.import_info['bad_rows'], 5840)
        self.assertGreater(float(np.median(wafer) - np.median(plate)), 995.0)
        self.assertLess(float(np.median(wafer) - np.median(plate)), 1010.0)
        window.close()

    def test_residual_display_uses_micrometre_data_without_changing_masks(self):
        window = SurfaceAnalyzerPro()
        yy, xx = np.mgrid[-4:5, -4:5]
        x = xx.ravel().astype(float); y = yy.ravel().astype(float)
        z = (0.25 + 1e-5 * x + 2e-5 * y
             + 2e-6 * np.sin(x) * np.cos(y))
        window.df_raw = pd.DataFrame({'X': x, 'Y': y, 'Z': z})
        n = len(z)
        window.manual_mask = np.ones(n, dtype=bool)
        window.temp_selected_mask = np.zeros(n, dtype=bool)
        window.update_analysis()
        active_before = window.active_idx.copy()
        manual_before = window.manual_mask.copy()
        roi_before = window._get_effective_roi_mask_cached(x, y, z).copy()
        filter_count_before = window.n_filtered

        index = window.cb_surface_display.findData('residual_1')
        window.cb_surface_display.setCurrentIndex(index)
        plot_z, label, short_label = window._get_plot_z(x, y, z)
        active_plot = plot_z[window.active_idx]
        model = window.high_order_models[1]

        self.assertEqual(label, '1阶去除后残差 (µm)')
        self.assertEqual(short_label, '1阶残差')
        self.assertAlmostEqual(float(np.mean(active_plot)), 0.0, places=10)
        self.assertAlmostEqual(float(np.ptp(active_plot)),
                               float(model['residual_pv_um']), places=9)
        self.assertLess(float(np.max(np.abs(active_plot))), 0.01)
        np.testing.assert_array_equal(window.active_idx, active_before)
        np.testing.assert_array_equal(window.manual_mask, manual_before)
        np.testing.assert_array_equal(
            window._get_effective_roi_mask_cached(x, y, z), roi_before)
        self.assertEqual(window.n_filtered, filter_count_before)
        self.assertLess(window.canvas.ax_xz.get_ylim()[0], float(np.min(active_plot)))
        self.assertGreater(window.canvas.ax_xz.get_ylim()[1], float(np.max(active_plot)))
        window.close()

    def test_unavailable_residual_mode_stays_raw_in_state_data_and_titles(self):
        window = SurfaceAnalyzerPro()
        window.high_order_models = {}
        index = window.cb_surface_display.findData('residual_2')
        window.cb_surface_display.setCurrentIndex(index)
        self.assertEqual(window.display_surface_mode, 'raw')
        self.assertEqual(window.cb_surface_display.currentData(), 'raw')
        z = np.array([0.249, 0.250, 0.251])
        plot_z, label, short_label = window._get_plot_z(z, z, z)
        np.testing.assert_array_equal(plot_z, z)
        self.assertEqual((label, short_label), ('Z (mm)', 'Z'))
        self.assertEqual(window.canvas.title_xz.text(), 'X-Z 投影')
        window.close()


if __name__ == '__main__':
    unittest.main()
