import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.judgment import judge_measurements, judgment_summary
from surface_analyzer.mixins.gap import GapAnalysisMixin

SPEC = {'rx': 100.0, 'ry': 100.0, 'pv': 5.0, 'ttv': 8.0, 'parallel': 50.0}


def _record(x, y, z, name):
    return {
        'x': np.asarray(x, dtype=float), 'y': np.asarray(y, dtype=float),
        'z': np.asarray(z, dtype=float), 'name': name, 'n': len(x),
        'offset_x': 0.0, 'offset_y': 0.0, 'registration_mode': 'none', 'sampled': False,
        'metric_quality': {'label': '全量计算', 'extrema_preserved': True},
    }


class JudgmentLogicTests(unittest.TestCase):
    def test_all_within_limits_pass(self):
        result = judge_measurements({'rx': -80, 'ry': 20, 'pv': 4.9, 'ttv': 8.0}, SPEC, 'surface')
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual([i['key'] for i in result['items']], ['rx', 'ry', 'pv', 'ttv'])

    def test_any_single_failure_fails_overall(self):
        result = judge_measurements({'rx': -120, 'ry': 20, 'pv': 1, 'ttv': 1}, SPEC, 'surface')
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual(result['failed'], ['rx'])
        self.assertIn('不合格', judgment_summary(result))
        self.assertIn('|Rx|', judgment_summary(result))

    def test_missing_value_is_not_reported_as_pass(self):
        result = judge_measurements({'rx': 1, 'ry': 1, 'pv': float('nan')}, SPEC, 'surface')
        self.assertEqual(result['verdict'], 'pending')
        self.assertEqual(judge_measurements({}, SPEC, 'parallel')['verdict'], 'pending')

    def test_unset_items_are_skipped(self):
        self.assertEqual(judge_measurements({'rx': 1e9}, {}, 'surface')['verdict'], 'unset')
        only_pv = judge_measurements({'rx': 1e9, 'pv': 1}, {'pv': 5}, 'surface')
        self.assertEqual(only_pv['verdict'], 'pass')


class JudgmentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.addCleanup(self.window.close)
        self.window.set_judgment_spec(SPEC, persist=False)

    def _verdict(self, module):
        return self.window._judgment_widgets[module][1].text()

    def test_single_card_fails_and_highlights_failed_metric(self):
        w = self.window
        w.last_metrics = {'rx': 10.0, 'ry': 10.0, 'pv': 9.0, 'ttv': 2.0}
        w._refresh_judgment('single')
        self.assertEqual(self._verdict('single'), '不合格')
        self.assertIn('#b42318', w.lbl_pv.styleSheet())
        self.assertEqual(w.lbl_rx.styleSheet(), '')
        w._clear_result_labels()
        self.assertEqual(self._verdict('single'), '--')
        self.assertEqual(w.lbl_pv.styleSheet(), '')

    def test_parallel_card_follows_result_and_clear(self):
        w = self.window
        w.parallel_result = {'drx': 3.0, 'dry': 4.0, 'angle': 5.0, 'step_height': 0.1}
        w._update_parallel_result_ui()
        self.assertEqual(self._verdict('parallel'), '合格')
        w.parallel_result['angle'] = 60.0
        w._update_parallel_result_ui()
        self.assertEqual(self._verdict('parallel'), '不合格')
        w.parallel_result = None
        w._update_parallel_result_ui()
        self.assertEqual(self._verdict('parallel'), '--')

    def test_spec_is_saved_in_recipe(self):
        self.assertEqual(self.window._current_recipe_dict()['judgment'], SPEC)


class GapRegistrationMeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_diagnostic_reports_mean_registration_distance(self):
        x = np.arange(12, dtype=float)
        stack = _record(x, np.zeros_like(x), np.ones_like(x), 'stack')
        base = _record(x + 0.03, np.full_like(x, 0.04), np.full_like(x, 0.4), 'base1')
        diag = GapAnalysisMixin._registration_diagnostic(stack, base, None, 0.06)
        self.assertAlmostEqual(diag['layers']['base1']['mean'], 0.05, places=12)
        layer = diag['layers']['base1']
        report = GapAnalysisMixin._match_report('单片1', layer['dist'], layer['idx'], layer['valid'])
        self.assertIn('均值 50.00 µm', report)

    def test_gap_strip_shows_mean_registration_error(self):
        x, y = np.meshgrid(np.arange(6, dtype=float), np.arange(5, dtype=float))
        x, y = x.ravel(), y.ravel()
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        self.assertEqual(window.lbl_gap_reg_mean.text(), '--')
        window.data_stack = _record(x, y, np.ones_like(x), 'stack')
        window.data_base1 = _record(x + 0.03, y - 0.04, np.full_like(x, 0.4), 'base1')
        window.spin_tol.setValue(0.06)
        window._refresh_gap_registration(preserve_view=False)
        self.assertEqual(window.lbl_gap_reg_mean.text(), '50.00')
        self.assertIn('RMS 50.00 µm', window.gap_reg_mean_card.toolTip())


if __name__ == '__main__':
    unittest.main()
