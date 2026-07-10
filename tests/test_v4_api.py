import tempfile
import unittest
from pathlib import Path

import numpy as np

from surface_analyzer import AnalysisOptions, analyze_file, analyze_xyz, compare_plane_results


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


if __name__ == "__main__":
    unittest.main()
