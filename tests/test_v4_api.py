import tempfile
import unittest
from pathlib import Path

import numpy as np

from surface_analyzer import AnalysisOptions, analyze_file, analyze_xyz, compare_plane_results
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.mixins.data_io import DataIOMixin
from surface_analyzer.mixins.roi import ROIMixin


class _RoiHarness(ROIMixin, AnalysisMixin):
    pass


class V4ApiTests(unittest.TestCase):
    def test_plane_metrics_follow_gui_sign_rule(self):
        x, y = np.meshgrid(np.linspace(0, 8, 20), np.linspace(0, 10, 24))
        z = 1.2 + 0.00001 * x + 0.00002 * y
        result = analyze_xyz(x.ravel(), y.ravel(), z.ravel())
        self.assertAlmostEqual(result.metrics["rx"], np.arctan(0.00002) * 1e6, places=6)
        self.assertAlmostEqual(result.metrics["ry"], np.arctan(-0.00001) * 1e6, places=6)
        self.assertAlmostEqual(result.metrics["pv"], 0.0, places=8)

    def test_metadata_and_semicolon_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fizeau.dat"
            path.write_text(
                "Instrument=Demo\nWavelength=632.8 nm\n"
                "0;0;1000;base\n1;0;1001;base\n0;1;1002;measure\n1;1;1003;measure\n",
                encoding="utf-8",
            )
            result = analyze_file(path, options=AnalysisOptions(z_unit="um"))
            self.assertEqual(result.input_points, 4)
            self.assertFalse(result.sampled)

    def test_parallel_result_delta(self):
        x = np.array([0.0, 1.0, 0.0, 1.0])
        y = np.array([0.0, 0.0, 1.0, 1.0])
        base = analyze_xyz(x, y, 1.0 + 0.00001 * x)
        measure = analyze_xyz(x, y, 1.1 + 0.00003 * x)
        parallel = compare_plane_results(base, measure)
        self.assertAlmostEqual(parallel["delta_ry_urad"], -20.0, places=5)
        self.assertAlmostEqual(parallel["step_height_mm"], 0.10001, places=8)

    def test_plane_residual_smart_roi_keeps_seed_connected_component(self):
        axis = np.linspace(0.0, 1.0, 11)
        xx, yy = np.meshgrid(axis, axis)
        x1, y1 = xx.ravel(), yy.ravel()
        x2, y2 = x1 + 5.0, y1.copy()
        x = np.concatenate([x1, x2])
        y = np.concatenate([y1, y2])
        z = 1.0 + 0.001 * x + 0.002 * y
        roi = {
            "seed_x": 0.5,
            "seed_y": 0.5,
            "seed_z": 1.0015,
            "z_tolerance_mm": 0.0001,
            "xy_radius_mm": 0.0,
            "smart_mode": "plane_residual",
        }
        keep = _RoiHarness()._smart_face_keep_mask_plane_residual(x, y, z, roi, update_radius=True)
        self.assertEqual(int(keep[: len(x1)].sum()), len(x1))
        self.assertEqual(int(keep[len(x1):].sum()), 0)

    def test_file_position_sampling_is_marked_as_estimated(self):
        quality = DataIOMixin._metric_quality_from_import({
            "sampled": True,
            "sample_method_key": "file_position",
            "extrema_preserved": False,
        })
        self.assertTrue(quality["estimated"])
        self.assertFalse(quality["extrema_preserved"])
        self.assertIn("不可直接用于产线放行", quality["warning"])


if __name__ == "__main__":
    unittest.main()
