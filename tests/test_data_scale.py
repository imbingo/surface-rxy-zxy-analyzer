import copy
import unittest
import os
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from surface_analyzer.data_scale import Count, source_counts, scale_summary


class DataScaleTests(unittest.TestCase):
    def test_import_status_lives_only_in_bottom_bar_and_restores(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtTest import QTest
        from surface_analyzer.app import SurfaceAnalyzerPro
        app = QApplication.instance() or QApplication([])
        window = SurfaceAnalyzerPro()
        try:
            window.resize(900, 700)
            window.show()
            bar = window.statusBar()
            label = window.lbl_import_status
            bar.clearMessage()
            app.processEvents()
            self.assertTrue(bar.isAncestorOf(label))
            self.assertTrue(label.isVisible())
            self.assertFalse(label.wordWrap())
            self.assertLess(label.height(), 42)
            window._show_status('临时操作提示', 50)
            app.processEvents()
            self.assertFalse(label.isVisible())
            window._update_import_status_label()
            self.assertEqual(bar.currentMessage(), '临时操作提示')
            QTest.qWait(100)
            self.assertEqual(bar.currentMessage(), '')
            self.assertTrue(label.isVisible())
            self.assertTrue(label.toolTip())
        finally:
            window.close()

    def test_unknown_is_not_zero(self):
        self.assertEqual(Count().text(), '未知')
        self.assertEqual(Count(0, 'exact').text(), '0')
        self.assertEqual(Count(100, 'estimated').text(), '≈100')

    def test_position_and_early_stride_do_not_claim_source_total(self):
        for method in ('file_position', 'stride'):
            info = dict(sampled=True, sample_method_key=method,
                        import_rows=100, source_valid_rows=1000,
                        original_valid_points=100, mapped_finite_points=100)
            self.assertIsNone(source_counts(info)[1].value)

    def test_full_scan_and_matrix_positions_are_separate(self):
        info = dict(sampled=True, height_matrix=True, source_valid_rows=123,
                    source_matrix_positions=2571 * 3066)
        records, valid, positions = source_counts(info)
        self.assertIsNone(records.value)
        self.assertEqual(valid.value, 123)
        self.assertEqual(positions.value, 7882686)

    def test_streaming_formats_supply_exact_source_counts(self):
        for fmt in ('Zygo XYZ Data File - Format 1',
                    'Precitec FSS Explorer SCAN PATH DATA'):
            info = dict(source_format=fmt, sampled=True, source_record_rows=120,
                        source_valid_rows=110, import_rows=10)
            self.assertEqual(source_counts(info)[0].value, 120)
            self.assertEqual(source_counts(info)[1].value, 110)

    def test_mapping_change_invalidates_sampled_source_validity(self):
        info = dict(sampled=True, sample_method_key='spatial_grid',
                    source_valid_rows=1000, count_mapping=['X', 'Y', 'Z'],
                    mapping_x_col='X', mapping_y_col='Y', mapping_z_col='Other')
        self.assertIsNone(source_counts(info)[1].value)
        info.update(sampled=False, mapped_finite_points=987)
        self.assertEqual(source_counts(info)[1].value, 987)

    def test_display_counts_do_not_modify_provenance(self):
        info = dict(sampled=True, sample_method_key='spatial_grid',
                    source_valid_rows=7500000, import_rows=1000000)
        before = copy.deepcopy(info)
        for limit in (10000, 30000, 60000):
            text, tooltip = scale_summary(info, 1000000, 923417, limit, limit)
            self.assertIn('源有效点 7,500,000', text)
            self.assertIn('最终计算 923,417', text)
            self.assertIn('（抽样）', text)
        self.assertEqual(info, before)

    def test_actual_display_refresh_keeps_measurement_and_indices(self):
        import numpy as np
        import pandas as pd
        from PyQt6.QtWidgets import QApplication
        from surface_analyzer.app import SurfaceAnalyzerPro
        app = QApplication.instance() or QApplication([])
        window = SurfaceAnalyzerPro()
        try:
            x, y = np.meshgrid(np.linspace(-15, 15, 101), np.linspace(-15, 15, 101))
            x, y = x.ravel(), y.ravel()
            z = .45 + 1e-5*x - 2e-5*y + .0001*np.sin(x)
            window.df_raw = pd.DataFrame({'X': x, 'Y': y, 'Z': z})
            window._df_version += 1
            window.manual_mask = np.ones(len(x), dtype=bool)
            window.temp_selected_mask = np.zeros(len(x), dtype=bool)
            window.import_info = dict(import_rows=len(x), sampled=False,
                                      mapped_finite_points=len(x))
            window.update_analysis()
            self.assertIsNotNone(window.last_metrics)
            metrics = copy.deepcopy(window.last_metrics)
            active = window.active_idx.copy()
            with patch.object(window, 'update_analysis', side_effect=AssertionError('display must not analyze')):
                for limit in (10000, 30000, 60000):
                    window.display_point_limit = limit
                    window.update_plots_only()
                    for key, value in metrics.items():
                        np.testing.assert_equal(window.last_metrics[key], value)
                    np.testing.assert_array_equal(window.active_idx, active)
                    self.assertIn('最终计算 10,201', window.lbl_import_status.text())
            if os.environ.get('SURFACE_SCALE_SCREENSHOT'):
                window.resize(1366, 768)
                window.show()
                app.processEvents()
                window.grab().save(os.environ['SURFACE_SCALE_SCREENSHOT'])
        finally:
            window.close()


if __name__ == '__main__':
    unittest.main()
