import unittest
import tempfile
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QApplication

from surface_analyzer.api import AnalysisOptions, analyze_xyz
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.analysis import AnalysisMixin


class SpatialGaussianTests(unittest.TestCase):
    def test_matrix_gaussian_reduces_high_frequency_noise_and_keeps_points(self):
        rng = np.random.default_rng(473)
        rows, cols = np.mgrid[0:60, 0:80]
        x = cols.ravel() * 0.02
        y = rows.ravel() * 0.02
        trend = 0.4 + 0.003 * x - 0.002 * y
        z = trend + rng.normal(0.0, 0.004, len(x))
        filtered, summary = AnalysisMixin.spatial_gaussian_filter(
            x, y, z, sigma_mm=0.04,
            matrix_rc=(rows.ravel(), cols.ravel()), return_summary=True)
        self.assertEqual(len(filtered), len(z))
        self.assertEqual(summary['method'], 'masked_matrix_gaussian')
        self.assertFalse(summary['hole_filling'])
        self.assertLess(np.std(filtered - trend), np.std(z - trend) * 0.45)

    def test_point_cloud_gaussian_uses_physical_radius(self):
        rng = np.random.default_rng(474)
        x = np.repeat(np.linspace(0, 1, 50), 30)
        y = np.tile(np.linspace(0, 0.6, 30), 50)
        trend = 1.2 + 0.004 * x + 0.003 * y
        z = trend + rng.normal(0.0, 0.003, len(x))
        filtered, summary = AnalysisMixin.spatial_gaussian_filter(
            x, y, z, sigma_mm=0.04, return_summary=True)
        self.assertEqual(summary['method'], 'point_cloud_radius')
        self.assertAlmostEqual(summary['support_radius_mm'], 0.12)
        self.assertLess(np.std(filtered - trend), np.std(z - trend) * 0.55)

    def test_matrix_hole_is_not_filled_or_added(self):
        rows, cols = np.mgrid[0:15, 0:21]
        valid = ~((rows >= 5) & (rows <= 9) & (cols >= 8) & (cols <= 12))
        rr, cc = rows[valid], cols[valid]
        x, y = cc.astype(float), rr.astype(float)
        z = 0.2 + 0.001 * x
        filtered, summary = AnalysisMixin.spatial_gaussian_filter(
            x, y, z, sigma_mm=0.6, matrix_rc=(rr, cc), return_summary=True)
        self.assertEqual(len(filtered), int(valid.sum()))
        self.assertEqual(summary['output_points'], int(valid.sum()))
        self.assertFalse(summary['hole_filling'])

    def test_headless_api_reports_gaussian_and_reduces_rms(self):
        rng = np.random.default_rng(475)
        rows, cols = np.mgrid[0:40, 0:50]
        x = cols.ravel() * 0.02
        y = rows.ravel() * 0.02
        z = 0.8 + 0.002 * x - 0.001 * y + rng.normal(0, 0.004, len(x))
        raw = analyze_xyz(x, y, z)
        filtered = analyze_xyz(
            x, y, z,
            options=AnalysisOptions(
                filter_mode='gaussian_lowpass', gaussian_sigma_mm=0.04))
        self.assertLess(filtered.metrics['rms'], raw.metrics['rms'] * 0.6)
        self.assertEqual(filtered.fitted_points, raw.fitted_points)
        self.assertEqual(filtered.filtered_points, 0)
        self.assertEqual(filtered.filter['spatial_summary']['output_points'], len(z))


class SpatialGaussianRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_recipe_roundtrip_and_legacy_default(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.cb_filter.setCurrentIndex(4)
        window.spin_gaussian_sigma.setValue(0.075)
        recipe = window._current_recipe_dict()
        self.assertEqual(recipe['schema_version'], 9)
        self.assertEqual(recipe['filter']['gaussian_sigma_mm'], 0.075)

        other = SurfaceAnalyzerPro()
        self.addCleanup(other.close)
        other.apply_recipe(recipe, remap_current_data=False, show_message=False)
        self.assertEqual(other.cb_filter.currentIndex(), 4)
        self.assertAlmostEqual(other.spin_gaussian_sigma.value(), 0.075)

        legacy = dict(recipe)
        legacy['schema_version'] = 8
        legacy['filter'] = {'mode_index': 0}
        other.apply_recipe(legacy, remap_current_data=False, show_message=False)
        self.assertEqual(other.cb_filter.currentIndex(), 0)

    def test_gui_pipeline_uses_filtered_z_for_metrics_and_views(self):
        rng = np.random.default_rng(476)
        rows, cols = np.mgrid[0:32, 0:44]
        x, y = cols.ravel() * 0.02, rows.ravel() * 0.02
        z_um = 400.0 + 2.0 * x - 1.0 * y + rng.normal(0, 5.0, len(x))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'noisy_xyz.csv'
            np.savetxt(path, np.column_stack([x, y, z_um]), delimiter=',',
                       header='X,Y,Z', comments='')
            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            self.assertTrue(window.load_path(path))
            raw_rms = window.last_metrics['rms']
            raw_z = window.get_final_transformed_data(window.df_raw)[2].copy()
            window.spin_gaussian_sigma.setValue(0.04)
            window.cb_filter.setCurrentIndex(4)
            QApplication.processEvents()
            self.assertIsNotNone(window.last_spatial_filter_summary)
            self.assertLess(window.last_metrics['rms'], raw_rms * 0.55)
            self.assertEqual(len(window._analysis_z_full), len(raw_z))
            self.assertFalse(np.array_equal(window._analysis_z_full, raw_z))
            self.assertIn('高斯', window.lbl_filter_info.text())


if __name__ == '__main__':
    unittest.main()
