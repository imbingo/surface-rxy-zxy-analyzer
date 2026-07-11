import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer import AnalysisOptions, analyze_file, analyze_xyz, compare_plane_results
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.mixins.data_io import DataIOMixin
from surface_analyzer.mixins.roi import ROIMixin


class _RoiHarness(ROIMixin, AnalysisMixin):
    pass


class V4ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

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

    @staticmethod
    def _select_and_delete(window, view, x1, y1, x2, y2):
        window.on_select(
            SimpleNamespace(xdata=x1, ydata=y1),
            SimpleNamespace(xdata=x2, ydata=y2),
            view,
        )
        selected = int(window.temp_selected_mask.sum())
        window.apply_manual_deletion()
        return selected

    def test_recipe_replays_manual_deletions_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual_delete.csv"
            rows = ["X,Y,Z"]
            for y in range(5):
                for x in range(5):
                    rows.append(f"{x},{y},{1.0 + x * 0.001 + y * 0.002}")
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            source = SurfaceAnalyzerPro()
            self.assertTrue(source.load_path(path))
            source.transform_pipeline = ["CW90"]
            source.update_analysis()
            counts = []
            counts.append(self._select_and_delete(source, "XY", 0.5, 0.5, 2.5, 2.5))
            counts.append(self._select_and_delete(source, "XZ", 3.5, 0.0009, 4.5, 0.0011))
            source.chk_detrend_display.setChecked(True)
            source._on_detrend_display_changed()
            counts.append(self._select_and_delete(source, "YZ", 3.5, -0.001, 4.5, 0.001))
            self.assertEqual(counts, [4, 5, 4])
            recipe = source._current_recipe_dict()
            json.dumps(recipe, ensure_ascii=False)
            expected_mask = source.manual_mask.copy()
            self.assertEqual(recipe["schema_version"], 2)
            self.assertEqual(len(recipe["manual_deletion"]["operations"]), 3)
            self.assertEqual(len(recipe["manual_deletion"]["source_sha256"]), 64)
            self.assertTrue(all(op["transform_pipeline"] == ["CW90"]
                                for op in recipe["manual_deletion"]["operations"]))
            self.assertEqual(recipe["manual_deletion"]["operations"][-1]["display_mode"], "detrended_um")
            source.close()

            replay = SurfaceAnalyzerPro()
            self.assertTrue(replay.load_path(path))
            with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
                 patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
                replay.apply_recipe(recipe, remap_current_data=True)
            self.assertTrue(np.array_equal(replay.manual_mask, expected_mask))
            self.assertEqual(len(replay.manual_delete_operations), 3)
            replay.close()

            path.write_text(path.read_text(encoding="utf-8").replace("0,0,1.0", "0,0,1.0001"),
                            encoding="utf-8")
            changed = SurfaceAnalyzerPro()
            self.assertTrue(changed.load_path(path))
            with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
                 patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
                changed.apply_recipe(recipe, remap_current_data=True)
            self.assertEqual(int((~changed.manual_mask).sum()), 0)
            self.assertEqual(changed.manual_delete_operations, [])
            changed.close()


if __name__ == "__main__":
    unittest.main()
