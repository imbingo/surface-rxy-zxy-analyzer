import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.smart_roi import build_adaptive_topology
from surface_analyzer.workers import TaskCancelled


class V455UnifiedImportHotfixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = SurfaceAnalyzerPro()
        self.window.import_encoding = "auto"
        self.window.import_delimiter = "auto"
        self.window.import_search_start_row = 0
        self.window.import_x_col = 0
        self.window.import_y_col = 0
        self.window.import_z_col = 0
        self.window.import_x_unit = "auto"
        self.window.import_y_unit = "auto"
        self.window.import_z_unit = "auto"

    def tearDown(self):
        self.window.close()
        self.directory.cleanup()

    def _path(self, name, text, encoding="utf-8"):
        path = Path(self.directory.name) / name
        path.write_text(text, encoding=encoding)
        return path

    def _load(self, path):
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
                patch.object(QMessageBox, "critical", return_value=QMessageBox.StandardButton.Ok):
            return self.window.load_path(path)

    def test_xyz_three_columns_default_but_extra_columns_require_mapping(self):
        self.window.input_layout_mode = "point_table"
        self.window.import_z_unit = "mm"
        simple = self._path("simple.xyz", "0 0 1\n1 0 2\n0 1 3\n")
        self.assertTrue(self._load(simple))
        np.testing.assert_allclose(self.window.df_raw[["X", "Y", "Z"]].to_numpy(), [
            [0.0, 0.0, 1.0], [1.0, 0.0, 2.0], [0.0, 1.0, 3.0]])

        ambiguous = self._path(
            "ambiguous.dat",
            "0;0;1;P1\n1;0;2;P2\n0;1;3;P3\n")
        self.assertTrue(self._load(ambiguous))
        self.assertIsNone(self.window.df_raw)
        self.assertTrue(self.window.import_info["mapping_required"])
        self.assertEqual(list(self.window.absolute_raw_df.columns),
                         ["Col1", "Col2", "Col3", "Col4"])

    def test_semantic_xyz_keeps_auxiliary_text_and_utf16(self):
        self.window.input_layout_mode = "point_table"
        path = self._path(
            "aux.tsv",
            "# X [µm]\tY [μm]\tThickness [um]\tProbe\tStatus\n"
            "0\t0\t250\tP-1\tGOOD\n"
            "1000\t0\t251\tP-2\tGOOD\n"
            "0\t1000\t252\tP-3\tWARN\n",
            encoding="utf-16")
        self.assertTrue(self._load(path))
        self.assertIn("Probe", self.window.absolute_raw_df.columns)
        self.assertIn("Status", self.window.absolute_raw_df.columns)
        self.assertEqual(self.window.import_info["header_confidence"], "semantic")
        np.testing.assert_allclose(self.window.df_raw["X"], [0.0, 1.0, 0.0])
        np.testing.assert_allclose(self.window.df_raw["Z"], [0.250, 0.251, 0.252])

    def test_generic_pixel_xy_converts_pitch_origin_and_preserves_topology(self):
        self.window.input_layout_mode = "pixel_xy"
        self.window.height_matrix_pitch_x_um = 50.0
        self.window.height_matrix_pitch_y_um = 100.0
        self.window.pixel_origin_x = 10.0
        self.window.pixel_origin_y = 20.0
        path = self._path(
            "pixel.csv",
            "PixelX,PixelY,Z [um],Quality\n"
            "10,20,250,ok\n11,20,251,ok\n10,21,252,ok\n11,21,253,ok\n")
        self.assertTrue(self._load(path))
        np.testing.assert_allclose(self.window.df_raw["X"], [0.0, 0.05, 0.0, 0.05])
        np.testing.assert_allclose(self.window.df_raw["Y"], [0.0, 0.0, 0.1, 0.1])
        np.testing.assert_allclose(self.window.df_raw["Z"], [0.250, 0.251, 0.252, 0.253])
        np.testing.assert_array_equal(self.window.df_raw["_matrix_col"], [10, 11, 10, 11])
        np.testing.assert_array_equal(self.window.df_raw["_topology_row"], [20, 20, 21, 21])
        self.assertEqual(self.window.import_info["input_semantics"], "pixel_xy")

        whitespace = self._path(
            "pixel_missing.xyz",
            "PixelX PixelY Z(um)\n"
            "10 20 250\n11 20 251\n12 20 No Data\n"
            "10 21 252\n11 21 253\n")
        self.assertTrue(self._load(whitespace))
        self.assertEqual(self.window.import_info["missing_points"], 1)
        self.assertEqual(self.window.import_info["matrix_cols"], 3)
        self.assertEqual(self.window.import_info["matrix_rows"], 2)
        self.assertNotIn(12, self.window.df_raw["_matrix_col"].tolist())

    def test_stride_topology_keeps_raw_holes_and_builds_sampled_neighbors(self):
        self.window.input_layout_mode = "height_matrix"
        self.window.height_matrix_pitch_x_um = 10.0
        self.window.height_matrix_pitch_y_um = 10.0
        self.window.height_matrix_z_unit = "mm"
        self.window.matrix_analysis_threshold = 4
        self.window.large_text_threshold_mb = 1000.0
        self.window.large_text_import_limit = 16
        self.window.large_text_stride_n = 2
        self.window.large_file_sample_method = "file_position"
        rows = []
        for row in range(8):
            values = [str(row * 8 + col) for col in range(8)]
            if row == 4:
                values[4] = "No Data"
            rows.append(",".join(values))
        frame = self.window._read_table(self._path("matrix.csv", "\n".join(rows) + "\n"))
        self.assertIn("_matrix_row", frame)
        self.assertIn("_topology_row", frame)
        self.assertGreater(int(frame["_matrix_row"].max()), int(frame["_topology_row"].max()))
        topology = build_adaptive_topology(
            frame["X"], frame["Y"],
            (frame["_topology_row"].to_numpy(), frame["_topology_col"].to_numpy()))
        self.assertEqual(topology["method"], "matrix8")
        self.assertGreater(topology["health"]["edge_count"], 0)

    def test_xyz_import_progress_can_cancel_before_state_is_committed(self):
        self.window.input_layout_mode = "point_table"
        path = self._path(
            "cancel.csv",
            "X,Y,Z\n" + "\n".join(f"{i},{i % 7},{0.25 + i * 1e-8}" for i in range(1000)))
        cancel_event = threading.Event()
        progress_values = []

        def progress(value, _message):
            progress_values.append(value)
            cancel_event.set()

        with self.assertRaises(TaskCancelled):
            self.window._read_table(path, progress=progress, cancel_event=cancel_event)
        self.assertTrue(progress_values)

    def test_golden_xyz_pixel_and_matrix_normalize_to_same_surface(self):
        x, y = np.meshgrid(np.arange(5, dtype=float), np.arange(4, dtype=float))
        z = 0.25 + 1e-5 * x + 2e-5 * y + 3e-6 * x * y

        xyz_path = Path(self.directory.name) / "golden_xyz.csv"
        xyz_path.write_text(
            "X(mm),Y(mm),Z(mm)\n" + "\n".join(
                f"{xx},{yy},{zz:.12f}" for xx, yy, zz in zip(x.ravel(), y.ravel(), z.ravel())) + "\n",
            encoding="utf-8")
        pixel_path = Path(self.directory.name) / "golden_pixel.csv"
        pixel_path.write_text(
            "PixelX,PixelY,Z(mm)\n" + "\n".join(
                f"{int(xx)},{int(yy)},{zz:.12f}" for xx, yy, zz in zip(x.ravel(), y.ravel(), z.ravel())) + "\n",
            encoding="utf-8")
        matrix_path = Path(self.directory.name) / "golden_matrix.csv"
        matrix_path.write_text(
            # Z Matrix 的历史坐标定义以文件末行为 Y=0，因此包装时反向写行。
            "\n".join(",".join(f"{value:.12f}" for value in row) for row in z[::-1]) + "\n",
            encoding="utf-8")

        outputs = []
        for mode, path in (("point_table", xyz_path), ("pixel_xy", pixel_path),
                           ("height_matrix", matrix_path)):
            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            window.input_layout_mode = mode
            window.height_matrix_pitch_x_um = 1000.0
            window.height_matrix_pitch_y_um = 1000.0
            window.height_matrix_z_unit = "mm"
            window.import_z_unit = "auto"
            with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
                self.assertTrue(window.load_path(path))
            ordered = window.df_raw.sort_values(["Y", "X"])
            canonical = ordered[["X", "Y", "Z"]].to_numpy()
            metrics = window.compute_plane_metrics(
                canonical[:, 0], canonical[:, 1], canonical[:, 2])
            outputs.append((canonical, metrics))

        for canonical, metrics in outputs[1:]:
            np.testing.assert_allclose(canonical, outputs[0][0], atol=1e-12)
            for key in ("mean_z", "pv", "ttv", "rms", "rx", "ry"):
                self.assertAlmostEqual(metrics[key], outputs[0][1][key], places=9)


if __name__ == "__main__":
    unittest.main()
