import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox
from unittest.mock import patch

from surface_analyzer.import_preflight import (
    ImportPreflightError, sniff_text_file, validate_selected_layout)
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.polynomial import fit_polynomial_surface
from surface_analyzer.workers import TaskCancelled
from surface_analyzer.app import SurfaceAnalyzerPro


class ImportPreflightTests(unittest.TestCase):
    def _file(self, directory, name, text):
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_physical_xyz_sniff_does_not_override_matrix_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory, "points.csv", "X,Y,Z\n" +
                              "\n".join(f"{i},{i+1},{i/10}" for i in range(30)))
            result = validate_selected_layout(path, "height_matrix")
            self.assertTrue(result["selection_authoritative"])

    def test_matrix_sniff_does_not_override_physical_xyz_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            text = "\n".join(",".join(str(row * 20 + col) for col in range(20))
                             for row in range(20))
            path = self._file(directory, "matrix.csv", text)
            result = validate_selected_layout(path, "point_table")
            self.assertTrue(result["selection_authoritative"])

    def test_pixel_xy_sniff_does_not_override_physical_xyz_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = ["PixelX,PixelY,Z"]
            rows += [f"{x},{y},{x+y}" for y in range(8) for x in range(8)]
            path = self._file(directory, "pixels.csv", "\n".join(rows))
            result = validate_selected_layout(path, "point_table")
            self.assertTrue(result["selection_authoritative"])

    def test_wide_xyz_uses_semantic_columns_instead_of_matrix_width(self):
        with tempfile.TemporaryDirectory() as directory:
            header = ["Record", "Time_s", "X (mm)", "Quality", "Y Coordinate",
                      "Temperature", "Height (um)", "Signal", "Zone", "Gain"]
            rows = []
            for index in range(40):
                rows.append([index, index * 0.1, index * 0.02, 99,
                             (index % 8) * 0.03, 23.5, 400 + index * 0.01,
                             1200, index % 4, 2])
            text = "Instrument,ArrayScanner\nBatch,ABC-001\n" + ",".join(header) + "\n"
            text += "\n".join(",".join(map(str, row)) for row in rows)
            path = self._file(directory, "wide_xyz.csv", text)

            result = sniff_text_file(path)
            self.assertEqual(result["kind"], "Physical XYZ point table")
            self.assertEqual(result["xyz_mapping"], {"x": 2, "y": 4, "z": 6})
            self.assertEqual(result["xyz_valid_ratio"], 1.0)
            validate_selected_layout(path, "point_table")
            self.assertTrue(validate_selected_layout(
                path, "height_matrix")["selection_authoritative"])

    def test_wide_commented_xyz_header_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            header = "# Index,Status,X_mm,Aux1,Y_mm,Aux2,Z_um,Aux3\n"
            body = "\n".join(
                f"{i},1,{i * .1},7,{i * .2},8,{500 + i},9" for i in range(20))
            path = self._file(directory, "commented.dat", header + body)
            result = sniff_text_file(path)
            self.assertEqual(result["kind"], "Physical XYZ point table")
            self.assertEqual(result["xyz_mapping"], {"x": 2, "y": 4, "z": 6})

    def test_empty_header_only_and_nonnumeric_fail_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, text in (("empty.csv", ""), ("header.csv", "X,Y,Z\n"),
                               ("words.csv", "A,B,C\n" + "foo,bar,baz\n" * 100)):
                path = self._file(directory, name, text)
                with self.subTest(name=name), self.assertRaises(ImportPreflightError):
                    validate_selected_layout(path, "point_table")

    def test_unstable_columns_are_left_to_selected_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [",".join(str(i) for i in range(width))
                    for width in ([3, 8, 4, 11, 5, 9] * 20)]
            path = self._file(directory, "unstable.csv", "\n".join(rows))
            result = validate_selected_layout(path, "height_matrix")
            self.assertTrue(result["selection_authoritative"])

    def test_large_wrong_file_is_bounded_and_cancellable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory, "large.dat", "not,numeric,data\n" * 200_000)
            started = time.perf_counter()
            with self.assertRaises(ImportPreflightError):
                validate_selected_layout(path, "point_table")
            self.assertLess(time.perf_counter() - started, 1.5)
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(TaskCancelled):
                sniff_text_file(path, cancel)


class SigmaResidualModelTests(unittest.TestCase):
    @staticmethod
    def _grid(side=55):
        axis = np.linspace(-1.0, 1.0, side)
        return np.meshgrid(axis, axis)

    def test_plane_outliers_removed_for_all_orders(self):
        x, y = self._grid()
        x, y = x.ravel(), y.ravel()
        rng = np.random.default_rng(7)
        z = 0.02 * x - 0.01 * y + rng.normal(0, 2e-5, len(x))
        outliers = np.arange(0, len(x), 173)
        z[outliers] += 0.02
        for order in ("order1", "order2", "order3"):
            keep, summary = AnalysisMixin.sigma_clip_filter(
                x, y, z, residual_order=order, return_summary=True)
            with self.subTest(order=order):
                self.assertLess(int(keep[outliers].sum()), len(outliers) // 4)
                self.assertGreater(int(keep.sum()), len(x) * 0.95)
                metrics = AnalysisMixin.compute_plane_metrics(x[keep], y[keep], z[keep])
                self.assertAlmostEqual(metrics["rx"], -10_000, delta=300)
                self.assertAlmostEqual(metrics["ry"], -20_000, delta=300)
                self.assertEqual(summary["requested_model"], order)

    def test_quadratic_model_preserves_curvature_better_than_plane(self):
        x, y = self._grid()
        x, y = x.ravel(), y.ravel()
        z = 0.025 * (x*x + 0.6*y*y)
        z[::211] += 0.08
        keep1 = AnalysisMixin.sigma_clip_filter(x, y, z, sigma_k=2.0,
                                                 residual_order="order1")
        keep2 = AnalysisMixin.sigma_clip_filter(x, y, z, sigma_k=2.0,
                                                 residual_order="order2")
        self.assertGreater(int(keep2.sum()), int(keep1.sum()) + 100)
        self.assertLess(int(keep2[::211].sum()), 3)

    def test_cubic_model_preserves_cubic_trend(self):
        x, y = self._grid()
        x, y = x.ravel(), y.ravel()
        z = 0.015*x**3 - 0.011*x*y*y + 0.004*y
        z[::197] += 0.06
        keep3 = AnalysisMixin.sigma_clip_filter(x, y, z, residual_order="order3")
        self.assertGreater(int(keep3.sum()), int(len(x) * 0.97))
        self.assertLess(int(keep3[::197].sum()), 3)

    def test_third_order_can_explain_slow_bump(self):
        x, y = self._grid()
        x, y = x.ravel(), y.ravel()
        z = 0.012*x**3 + 0.003*y
        slow = x > 0.55
        z[slow] += 0.003 * (x[slow] - 0.55)
        model1 = fit_polynomial_surface(x, y, z, 1)
        model3 = fit_polynomial_surface(x, y, z, 3)
        self.assertLess(model3["residual_rms_um"], model1["residual_rms_um"])
        self.assertLess(model3["residual_pv_um"], model1["residual_pv_um"])

    def test_metrics_definition_is_independent_of_filter_model(self):
        x, y = self._grid(20)
        x, y = x.ravel(), y.ravel()
        z = 0.01*x - 0.02*y + 0.002*x*x
        common = np.ones(len(z), dtype=bool); common[::47] = False
        expected = AnalysisMixin.compute_plane_metrics(x[common], y[common], z[common])
        for _model in ("order1", "order2", "order3"):
            actual = AnalysisMixin.compute_plane_metrics(x[common], y[common], z[common])
            for key in ("mean_z", "rms", "pv", "ttv", "rx", "ry"):
                self.assertEqual(expected[key], actual[key])

    def test_stability_check_and_fallback_are_traceable(self):
        rng = np.random.default_rng(11)
        x = rng.uniform(-1, 1, 8)
        y = rng.uniform(-1, 1, 8)
        z = x + y
        _, summary = AnalysisMixin.sigma_clip_filter(
            x, y, z, residual_order="order3", return_summary=True)
        self.assertEqual(summary["actual_model"], "order2")
        self.assertTrue(summary["fallback_reason"])
        with self.assertRaisesRegex(ValueError, "覆盖不足"):
            fit_polynomial_surface(np.ones(20), np.arange(20), np.arange(20), 3,
                                   check_stability=True)


class ImportTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wide_xyz_import_maps_semantic_columns_after_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide_measurement.dat"
            header = ["Record", "Time_s", "X (mm)", "Quality", "Y Coordinate",
                      "Temperature", "Height (um)", "Signal", "Zone", "Gain"]
            rows = [
                [i, i * 0.1, i * 0.02, 99, (i % 8) * 0.03, 23.5,
                 400 + i * 0.01, 1200, i % 4, 2]
                for i in range(40)
            ]
            text = "Instrument,ArrayScanner\nBatch,ABC-001\n" + ",".join(header) + "\n"
            text += "\n".join(",".join(map(str, row)) for row in rows)
            path.write_text(text, encoding="utf-8")

            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            window.input_layout_mode = "point_table"
            frame = window._read_table(path)
            self.assertEqual(len(frame), 40)
            self.assertEqual(window.import_info["header_auto_mapping"],
                             {"x": 2, "y": 4, "z": 6})
            self.assertEqual(list(frame.columns)[2], "X (mm)")
            self.assertEqual(list(frame.columns)[4], "Y Coordinate")
            self.assertEqual(list(frame.columns)[6], "Height (um)")

    def test_failed_visible_import_preserves_previous_dataset_and_results(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.csv"
            valid.write_text("X,Y,Z\n0,0,1\n1,0,1.1\n0,1,1.2\n1,1,1.3\n",
                             encoding="utf-8")
            wrong = Path(directory) / "wrong.csv"
            wrong.write_text("X,Y,Z\n0,0,1\n1,0,2\n0,1,3\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            window.import_z_unit = "mm"
            with patch.object(QMessageBox, "information",
                              return_value=QMessageBox.StandardButton.Ok):
                self.assertTrue(window.load_path(valid))
            old_df = window.df_raw
            old_absolute = window.absolute_raw_df
            old_metrics = dict(window.last_metrics)
            old_active = window.active_idx.copy()
            window.roi_enabled = True
            window.roi_shapes = [{"type": "circle", "cx": 0.5, "cy": 0.5,
                                  "radius": 10.0, "enabled": True}]
            old_roi = [dict(item) for item in window.roi_shapes]

            window.input_layout_mode = "height_matrix"
            window.show()
            self.assertTrue(window.load_path(wrong))
            import_dialog = window._import_dialog
            deadline = time.monotonic() + 5.0
            while window._task_thread is not None and time.monotonic() < deadline:
                self.app.processEvents()
            self.assertIsNone(window._task_thread)
            self.assertIs(window.df_raw, old_df)
            self.assertIs(window.absolute_raw_df, old_absolute)
            np.testing.assert_array_equal(window.active_idx, old_active)
            self.assertEqual(window.last_metrics, old_metrics)
            self.assertEqual(window.roi_shapes, old_roi)
            if import_dialog is not None:
                import_dialog.close()
                import_dialog.deleteLater()
            window.close()
            self.app.processEvents()

    def test_failed_first_import_does_not_poison_next_valid_import(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong = Path(directory) / "wrong.csv"
            wrong.write_text("X,Y,Z\n0,0,1\n1,0,2\n0,1,3\n", encoding="utf-8")
            valid = Path(directory) / "valid.csv"
            valid.write_text(
                "X,Y,Z\n0,0,1\n1,0,1.1\n0,1,1.2\n1,1,1.3\n",
                encoding="utf-8")
            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            window.import_z_unit = "mm"
            window.show()
            window.input_layout_mode = "height_matrix"
            self.assertTrue(window.load_path(wrong))
            first_dialog = window._import_dialog
            deadline = time.monotonic() + 5.0
            while window._task_thread is not None and time.monotonic() < deadline:
                self.app.processEvents()
            self.assertIsNone(window._task_thread)
            self.assertFalse(hasattr(window, '_parallel_revision'))
            if first_dialog is not None:
                first_dialog.close(); first_dialog.deleteLater()

            window.input_layout_mode = "point_table"
            with patch.object(QMessageBox, "information",
                              return_value=QMessageBox.StandardButton.Ok):
                self.assertTrue(window.load_path(valid))
                second_dialog = window._import_dialog
                deadline = time.monotonic() + 5.0
                while window._task_thread is not None and time.monotonic() < deadline:
                    self.app.processEvents()
            self.assertIsNone(window._task_thread)
            self.assertIsNotNone(window.df_raw)
            self.assertEqual(len(window.df_raw), 4)
            self.assertIsInstance(window._parallel_revision, int)
            if second_dialog is not None:
                second_dialog.close(); second_dialog.deleteLater()
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
