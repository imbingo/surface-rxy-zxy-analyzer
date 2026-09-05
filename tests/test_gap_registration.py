import os
import threading
import unittest
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.gap import GapAnalysisMixin
from surface_analyzer.workers import TaskCancelled


def _record(x, y, z, name="layer", dx=0.0, dy=0.0, mode="none"):
    return {
        'x': np.asarray(x, dtype=float),
        'y': np.asarray(y, dtype=float),
        'z': np.asarray(z, dtype=float),
        'name': name,
        'n': len(x),
        'offset_x': float(dx),
        'offset_y': float(dy),
        'registration_mode': mode,
        'sampled': False,
        'metric_quality': {'label': '全量计算', 'extrema_preserved': True},
    }


class GapRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _grid(nx=6, ny=5):
        x, y = np.meshgrid(np.arange(nx, dtype=float), np.arange(ny, dtype=float))
        return x.ravel(), y.ravel()

    def _window_with_layers(self, base2=False):
        x, y = self._grid()
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.data_stack = _record(x, y, 1.0 + 0.002 * x - 0.003 * y, "stack")
        window.data_base1 = _record(x + 0.03, y - 0.02, np.full_like(x, 0.4), "base1")
        window.data_base2 = (
            _record(x - 0.02, y + 0.01, np.full_like(x, 0.1), "base2") if base2 else None)
        window.gap_active_layer = 'base1'
        window._update_gap_slot_labels()
        window._refresh_gap_registration(preserve_view=False)
        return window

    def test_auto_registration_refines_from_current_manual_offset_only(self):
        x, y = self._grid(9, 7)
        stack = _record(x, y, np.ones_like(x), "stack")
        moving = _record(
            x + 0.237, y - 0.164, np.full_like(x, 0.4), "base1",
            dx=-0.232, dy=0.160, mode="manual")

        result = GapAnalysisMixin._optimize_translation(stack, moving, tolerance=0.01)

        self.assertTrue(result['accepted'])
        self.assertAlmostEqual(result['start_offset_x'], -0.232, places=12)
        self.assertAlmostEqual(result['start_offset_y'], 0.160, places=12)
        self.assertAlmostEqual(result['offset_x'], -0.237, places=6)
        self.assertAlmostEqual(result['offset_y'], 0.164, places=6)
        self.assertLess(result['rms'], 1e-8)
        self.assertEqual(result['matched'], len(x))
        self.assertLess(result['adjustment'], result['max_adjustment'])

    def test_auto_registration_rejects_global_jump_and_keeps_current_offset(self):
        x, y = self._grid(6, 5)
        stack = _record(x, y, np.ones_like(x), "stack")
        moving = _record(x + 0.22, y - 0.18, np.full_like(x, 0.4), "base1")

        result = GapAnalysisMixin._optimize_translation(stack, moving, tolerance=0.005)

        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'insufficient_local_overlap')
        self.assertEqual(result['offset_x'], 0.0)
        self.assertEqual(result['offset_y'], 0.0)
        self.assertEqual(result['adjustment'], 0.0)
        self.assertEqual(result['local_points'], 0)

    def test_auto_registration_ignores_nonoverlap_inside_annular_reference(self):
        axis = np.arange(-40.0, 40.25, 0.5)
        gx, gy = np.meshgrid(axis, axis)
        x, y = gx.ravel(), gy.ravel()
        radius = np.hypot(x, y)
        stack_mask = (radius <= 40.0) & (radius > 15.0)
        moving_mask = radius <= 15.0
        stack = _record(
            x[stack_mask], y[stack_mask], np.ones(int(stack_mask.sum())), "annular-stack")
        moving = _record(
            x[moving_mask], y[moving_mask], np.full(int(moving_mask.sum()), 0.4),
            "wafer", dx=-15.4222, dy=-10.4964, mode="manual")

        result = GapAnalysisMixin._optimize_translation(stack, moving, tolerance=0.060)

        self.assertEqual(int(stack_mask.sum()), 17260)
        self.assertEqual(int(moving_mask.sum()), 2821)
        self.assertTrue(result['accepted'])
        self.assertAlmostEqual(result['offset_x'], -15.5, places=9)
        self.assertAlmostEqual(result['offset_y'], -10.5, places=9)
        self.assertGreater(result['local_points'], 1000)
        self.assertGreater(result['matched'], 1000)
        self.assertLess(result['overlap_rms'], result['start_overlap_rms'])

    def test_auto_registration_accepts_partial_layer_when_overlap_improves(self):
        gx, gy = np.meshgrid(
            np.arange(-15.0, 15.01, 0.5), np.arange(-15.0, 15.01, 0.5))
        reference = np.column_stack([gx.ravel(), gy.ravel()])
        reference = reference[
            (reference[:, 0] / 15.0) ** 2 + (reference[:, 1] / 15.0) ** 2 <= 1.0]
        partial = reference[
            (reference[:, 1] >= -8.0) & (reference[:, 1] <= 4.0)
            & (reference[:, 0] >= -14.0)]
        stack = _record(
            reference[:, 0], reference[:, 1], np.ones(len(reference)), "large-stack")
        moving = _record(
            partial[:, 0] + 0.12, partial[:, 1] + 0.03,
            np.full(len(partial), 0.4), "partial-base")

        result = GapAnalysisMixin._optimize_translation(stack, moving, tolerance=0.05)

        self.assertTrue(result['accepted'])
        self.assertAlmostEqual(result['offset_x'], -0.12, places=9)
        self.assertAlmostEqual(result['offset_y'], -0.03, places=9)
        self.assertEqual(result['matched'], len(partial))
        self.assertLess(result['overlap_rms'], result['start_overlap_rms'])
        self.assertGreater(result['rms'], result['start_rms'])

    def test_live_tolerance_diagnostic_uses_euclidean_xy_distance(self):
        x = np.arange(12, dtype=float)
        y = np.zeros_like(x)
        stack = _record(x, y, np.ones_like(x), "stack")
        base = _record(x + 0.03, y + 0.04, np.full_like(x, 0.4), "base1")

        within = GapAnalysisMixin._registration_diagnostic(stack, base, None, 0.0501)
        outside = GapAnalysisMixin._registration_diagnostic(stack, base, None, 0.0499)

        self.assertEqual(int(within['final_valid'].sum()), len(x))
        self.assertEqual(int(outside['final_valid'].sum()), 0)

    def test_manual_offset_directly_calculates_two_layer_gap_without_auto(self):
        x, y = self._grid()
        stack = _record(x, y, 0.8 + 0.001 * x, "stack")
        base1 = _record(
            x + 0.2, y - 0.1, 0.3 + 0.001 * x, "base1",
            dx=-0.2, dy=0.1, mode="manual")

        payload = GapAnalysisMixin._compute_gap_payload(
            stack, base1, None, 0.001, lambda *_: None, threading.Event())

        self.assertEqual(len(payload['z']), len(x))
        np.testing.assert_allclose(payload['z'], 0.5, atol=1e-12)
        np.testing.assert_allclose(payload['details']['base1_distance'], 0.0, atol=1e-12)
        self.assertEqual(payload['layers']['base1']['offset_x'], -0.2)
        self.assertEqual(payload['layers']['base1']['offset_y'], 0.1)
        self.assertEqual(payload['layers']['base1']['registration_mode'], 'manual')

    def test_manual_offsets_directly_calculate_three_layer_gap_without_auto(self):
        x, y = np.meshgrid(np.arange(5, dtype=float), np.arange(4, dtype=float))
        x, y = x.ravel(), y.ravel()
        stack = _record(x, y, np.full_like(x, 1.2), "stack")
        base1 = _record(x + 0.2, y - 0.1, np.full_like(x, 0.45),
                        "base1", dx=-0.2, dy=0.1, mode="manual")
        base2 = _record(x - 0.3, y + 0.25, np.full_like(x, 0.15),
                        "base2", dx=0.3, dy=-0.25, mode="manual")
        progress_values = []

        payload = GapAnalysisMixin._compute_gap_payload(
            stack, base1, base2, 0.001,
            lambda value, message: progress_values.append(value), threading.Event())

        self.assertEqual(len(payload['z']), len(x))
        np.testing.assert_allclose(payload['z'], 0.6)
        np.testing.assert_allclose(payload['details']['base1_distance'], 0.0, atol=1e-12)
        np.testing.assert_allclose(payload['details']['base2_distance'], 0.0, atol=1e-12)
        self.assertEqual(payload['layers']['base1']['offset_x'], -0.2)
        self.assertEqual(payload['layers']['base2']['offset_y'], -0.25)
        self.assertEqual(payload['layers']['base2']['registration_mode'], 'manual')
        self.assertIn(92, progress_values)

    def test_auto_cancel_insufficient_invalid_and_guard_preserve_manual_offset(self):
        x, y = self._grid()
        stack = _record(x, y, np.ones_like(x), "stack")
        manual = _record(
            x + 0.22, y - 0.18, np.full_like(x, 0.4), "base1",
            dx=0.125, dy=-0.075, mode="manual")
        original = (manual['offset_x'], manual['offset_y'])

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TaskCancelled):
            GapAnalysisMixin._auto_registration_payload(
                stack, manual, None, 0.01, lambda *_: None, cancelled)
        self.assertEqual((manual['offset_x'], manual['offset_y']), original)

        insufficient = _record([0.0, 1.0], [0.0, 1.0], [0.4, 0.4], "short", *original)
        with self.assertRaises(ValueError):
            GapAnalysisMixin._optimize_translation(stack, insufficient, 0.01)
        self.assertEqual((insufficient['offset_x'], insufficient['offset_y']), original)

        window = self._window_with_layers()
        window.data_base1['offset_x'], window.data_base1['offset_y'] = original
        window.data_base1['registration_mode'] = 'manual'
        rejected = {
            'accepted': False, 'reason': 'max_adjustment',
            'offset_x': original[0], 'offset_y': original[1],
            'start_offset_x': original[0], 'start_offset_y': original[1],
            'adjustment': np.inf, 'max_adjustment': 0.03,
            'rms': np.nan, 'matched': 0, 'iterations': 1,
        }
        window._apply_auto_registration({'base1': rejected})
        self.assertEqual(
            (window.data_base1['offset_x'], window.data_base1['offset_y']), original)
        self.assertEqual(window.data_base1['registration_mode'], 'manual')

        invalid = dict(rejected, accepted=True, reason='', offset_x=np.nan, adjustment=0.0)
        window._apply_auto_registration({'base1': invalid})
        self.assertEqual(
            (window.data_base1['offset_x'], window.data_base1['offset_y']), original)
        self.assertEqual(window.data_base1['registration_mode'], 'manual')

        guarded = GapAnalysisMixin._optimize_translation(
            stack, _record(x + 0.25, y - 0.2, np.full_like(x, 0.4), "far"), 0.005)
        self.assertFalse(guarded['accepted'])
        self.assertEqual((guarded['offset_x'], guarded['offset_y']), (0.0, 0.0))

    def test_registration_mode_tracks_manual_then_successful_auto(self):
        window = self._window_with_layers()
        window.on_gap_layer_moved('base1', -0.025, 0.015, finished=True)
        self.assertEqual(window.data_base1['registration_mode'], 'manual')
        window._apply_auto_registration({'base1': {
            'accepted': True, 'reason': '',
            'offset_x': -0.03, 'offset_y': 0.02,
            'start_offset_x': -0.025, 'start_offset_y': 0.015,
            'adjustment': np.hypot(0.005, 0.005), 'max_adjustment': 0.15,
            'rms': 0.0, 'matched': window.data_base1['n'], 'iterations': 1,
        }})
        self.assertEqual(window.data_base1['registration_mode'], 'auto')
        self.assertAlmostEqual(window.data_base1['offset_x'], -0.03)
        self.assertAlmostEqual(window.data_base1['offset_y'], 0.02)
        self.assertIn('自动精对齐完成', window.lbl_gap_auto_feedback.text())

    def test_gap_report_metrics_numerically_equal_main_plane_metrics(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        x, y = self._grid(7, 6)
        z = 0.0007 * x - 0.0011 * y + 0.48
        payload = {'x': x, 'y': y, 'z': z}

        gap_metrics = window._gap_plane_metrics(payload)
        main_metrics = window.compute_plane_metrics(x, y, z)

        self.assertAlmostEqual(gap_metrics['rx'], main_metrics['rx'], places=12)
        self.assertAlmostEqual(gap_metrics['ry'], main_metrics['ry'], places=12)
        self.assertAlmostEqual(gap_metrics['rx'], np.arctan(-0.0011) * 1e6, places=7)
        self.assertAlmostEqual(gap_metrics['ry'], np.arctan(-0.0007) * 1e6, places=7)

    def test_gap_canvas_wheel_zoom_is_cursor_centered_and_view_only(self):
        window = self._window_with_layers()
        canvas = window.gap_match_canvas
        before_x, before_y = canvas.ax.get_xlim(), canvas.ax.get_ylim()
        cursor_x = before_x[0] + 0.28 * (before_x[1] - before_x[0])
        cursor_y = before_y[0] + 0.63 * (before_y[1] - before_y[0])
        offset = (window.data_base1['offset_x'], window.data_base1['offset_y'])
        matched = int(window._registration_diagnostic(
            window.data_stack, window.data_base1, None, window.spin_tol.value())['final_valid'].sum())
        sentinel = {'result': 'keep'}
        window.gap_result = sentinel

        canvas._on_scroll(SimpleNamespace(
            inaxes=canvas.ax, xdata=cursor_x, ydata=cursor_y, button='up', step=1))
        zoom_x, zoom_y = canvas.ax.get_xlim(), canvas.ax.get_ylim()

        self.assertLess(zoom_x[1] - zoom_x[0], before_x[1] - before_x[0])
        self.assertLess(zoom_y[1] - zoom_y[0], before_y[1] - before_y[0])
        self.assertAlmostEqual(
            (cursor_x - zoom_x[0]) / (zoom_x[1] - zoom_x[0]),
            (cursor_x - before_x[0]) / (before_x[1] - before_x[0]), places=12)
        self.assertEqual((window.data_base1['offset_x'], window.data_base1['offset_y']), offset)
        self.assertEqual(int(window._registration_diagnostic(
            window.data_stack, window.data_base1, None,
            window.spin_tol.value())['final_valid'].sum()), matched)
        self.assertIs(window.gap_result, sentinel)

        canvas._on_scroll(SimpleNamespace(
            inaxes=canvas.ax, xdata=cursor_x, ydata=cursor_y, button='down', step=-1))
        np.testing.assert_allclose(canvas.ax.get_xlim(), before_x, atol=1e-12)
        np.testing.assert_allclose(canvas.ax.get_ylim(), before_y, atol=1e-12)

    def test_gap_canvas_preserves_zoom_while_drag_updates_offset(self):
        window = self._window_with_layers()
        canvas = window.gap_match_canvas
        home_x, home_y = canvas.ax.get_xlim(), canvas.ax.get_ylim()
        cursor_x = np.mean(home_x)
        cursor_y = np.mean(home_y)
        canvas._on_scroll(SimpleNamespace(
            inaxes=canvas.ax, xdata=cursor_x, ydata=cursor_y, button='up', step=1))
        zoom_x, zoom_y = canvas.ax.get_xlim(), canvas.ax.get_ylim()
        canvas._on_press(SimpleNamespace(
            inaxes=canvas.ax, xdata=cursor_x, ydata=cursor_y, button=1, dblclick=False))
        canvas._on_release(SimpleNamespace(
            inaxes=canvas.ax, xdata=cursor_x + 0.01, ydata=cursor_y - 0.015, button=1))

        self.assertAlmostEqual(window.data_base1['offset_x'], 0.01)
        self.assertAlmostEqual(window.data_base1['offset_y'], -0.015)
        self.assertEqual(window.data_base1['registration_mode'], 'manual')
        np.testing.assert_allclose(canvas.ax.get_xlim(), zoom_x, atol=1e-12)
        np.testing.assert_allclose(canvas.ax.get_ylim(), zoom_y, atol=1e-12)

    def test_gap_selection_and_drag_preserve_rendered_xy_scale(self):
        window = self._window_with_layers(base2=True)
        canvas = window.gap_match_canvas
        canvas.resize(1200, 600)
        window._refresh_gap_registration(preserve_view=False)

        def geometry():
            canvas.draw()
            points = canvas.ax.transData.transform([[0, 0], [1, 0], [0, 1]])
            scale_x = np.linalg.norm(points[1] - points[0])
            scale_y = np.linalg.norm(points[2] - points[0])
            self.assertAlmostEqual(scale_x, scale_y, places=7)
            return points

        geometry()
        canvas._on_scroll(SimpleNamespace(
            inaxes=canvas.ax, xdata=2., ydata=2., button='up', step=1))
        before = geometry()
        for layer in ('base2', 'base1'):
            window.select_gap_layer(layer)
            np.testing.assert_allclose(geometry(), before, atol=0.5, rtol=0)
        canvas._on_press(SimpleNamespace(
            inaxes=canvas.ax, xdata=2., ydata=2., button=1, dblclick=False))
        canvas._on_motion(SimpleNamespace(inaxes=canvas.ax, xdata=2.01, ydata=1.99))
        np.testing.assert_allclose(geometry(), before, atol=0.5, rtol=0)
        canvas._on_release(SimpleNamespace(
            inaxes=canvas.ax, xdata=2.01, ydata=1.99, button=1))
        np.testing.assert_allclose(geometry(), before, atol=0.5, rtol=0)
        self.assertAlmostEqual(window.data_base1['offset_x'], .01)
        self.assertAlmostEqual(window.data_base1['offset_y'], -.01)

    def test_gap_canvas_double_click_restores_home_without_changing_offset(self):
        window = self._window_with_layers()
        canvas = window.gap_match_canvas
        home_x, home_y = tuple(canvas._home_limits[0]), tuple(canvas._home_limits[1])
        center_x, center_y = np.mean(home_x), np.mean(home_y)
        canvas._on_scroll(SimpleNamespace(
            inaxes=canvas.ax, xdata=center_x, ydata=center_y, button='up', step=1))
        offset = (window.data_base1['offset_x'], window.data_base1['offset_y'])
        canvas._on_press(SimpleNamespace(
            inaxes=canvas.ax, xdata=center_x, ydata=center_y, button=1, dblclick=True))

        np.testing.assert_allclose(canvas.ax.get_xlim(), home_x, atol=1e-12)
        np.testing.assert_allclose(canvas.ax.get_ylim(), home_y, atol=1e-12)
        self.assertEqual((window.data_base1['offset_x'], window.data_base1['offset_y']), offset)
        self.assertIsNone(canvas._drag_state)

    def test_gap_page_exposes_registration_and_export_workflow(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)

        self.assertEqual(window.gap_match_canvas._active_layer, None)
        self.assertFalse(window.btn_auto_match_gap.isEnabled())
        self.assertFalse(window.btn_calc_gap.isEnabled())
        self.assertFalse(window.btn_export_gap_csv.isEnabled())
        self.assertFalse(window.btn_export_gap_report.isEnabled())
        self.assertTrue(window.btn_gap_select_base1.isCheckable())
        self.assertTrue(window.btn_gap_select_base2.isCheckable())


if __name__ == '__main__':
    unittest.main()
