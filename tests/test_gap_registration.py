import os
import threading
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.gap import GapAnalysisMixin


def _record(x, y, z, name="layer", dx=0.0, dy=0.0):
    return {
        'x': np.asarray(x, dtype=float),
        'y': np.asarray(y, dtype=float),
        'z': np.asarray(z, dtype=float),
        'name': name,
        'n': len(x),
        'offset_x': float(dx),
        'offset_y': float(dy),
        'sampled': False,
        'metric_quality': {'label': '全量计算', 'extrema_preserved': True},
    }


class GapRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_auto_registration_recovers_xy_translation(self):
        rng = np.random.default_rng(461)
        x = rng.uniform(-4.0, 5.0, 600)
        y = rng.uniform(-3.0, 6.0, 600)
        stack = _record(x, y, np.ones_like(x), "stack")
        moving = _record(x + 0.237, y - 0.164, np.full_like(x, 0.4), "base1")

        result = GapAnalysisMixin._optimize_translation(stack, moving, tolerance=0.01)

        self.assertAlmostEqual(result['offset_x'], -0.237, places=6)
        self.assertAlmostEqual(result['offset_y'], 0.164, places=6)
        self.assertLess(result['rms'], 1e-8)
        self.assertEqual(result['matched'], len(x))

    def test_live_tolerance_diagnostic_uses_euclidean_xy_distance(self):
        x = np.arange(12, dtype=float)
        y = np.zeros_like(x)
        stack = _record(x, y, np.ones_like(x), "stack")
        base = _record(x + 0.03, y + 0.04, np.full_like(x, 0.4), "base1")

        within = GapAnalysisMixin._registration_diagnostic(stack, base, None, 0.0501)
        outside = GapAnalysisMixin._registration_diagnostic(stack, base, None, 0.0499)

        self.assertEqual(int(within['final_valid'].sum()), len(x))
        self.assertEqual(int(outside['final_valid'].sum()), 0)

    def test_gap_payload_applies_saved_layer_offsets(self):
        x, y = np.meshgrid(np.arange(5, dtype=float), np.arange(4, dtype=float))
        x, y = x.ravel(), y.ravel()
        stack = _record(x, y, np.full_like(x, 1.2), "stack")
        base1 = _record(x + 0.2, y - 0.1, np.full_like(x, 0.45),
                        "base1", dx=-0.2, dy=0.1)
        base2 = _record(x - 0.3, y + 0.25, np.full_like(x, 0.15),
                        "base2", dx=0.3, dy=-0.25)
        progress_values = []

        payload = GapAnalysisMixin._compute_gap_payload(
            stack, base1, base2, 0.001,
            lambda value, message: progress_values.append(value), threading.Event())

        self.assertEqual(len(payload['z']), len(x))
        np.testing.assert_allclose(payload['z'], 0.6)
        np.testing.assert_allclose(payload['details']['base1_distance'], 0.0, atol=1e-12)
        np.testing.assert_allclose(payload['details']['base2_distance'], 0.0, atol=1e-12)
        self.assertEqual(payload['layers']['base1']['offset_x'], -0.2)
        self.assertIn(92, progress_values)

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
