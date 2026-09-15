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

    def test_physical_xyz_rejected_as_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory, "points.csv", "X,Y,Z\n" +
                              "\n".join(f"{i},{i+1},{i/10}" for i in range(30)))
            with self.assertRaisesRegex(ImportPreflightError, "Physical XYZ"):
                validate_selected_layout(path, "height_matrix")

    def test_matrix_rejected_as_physical_xyz(self):
        with tempfile.TemporaryDirectory() as directory:
            text = "\n".join(",".join(str(row * 20 + col) for col in range(20))
                             for row in range(20))
            path = self._file(directory, "matrix.csv", text)
            with self.assertRaisesRegex(ImportPreflightError, "Z Matrix"):
                validate_selected_layout(path, "point_table")

    def test_pixel_xy_rejected_as_physical_xyz(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = ["PixelX,PixelY,Z"]
            rows += [f"{x},{y},{x+y}" for y in range(8) for x in range(8)]
            path = self._file(directory, "pixels.csv", "\n".join(rows))
            with self.assertRaisesRegex(ImportPreflightError, "Pixel XY"):
                validate_selected_layout(path, "point_table")

    def test_empty_header_only_and_nonnumeric_fail_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, text in (("empty.csv", ""), ("header.csv", "X,Y,Z\n"),
                               ("words.csv", "A,B,C\n" + "foo,bar,baz\n" * 100)):
                path = self._file(directory, name, text)
                with self.subTest(name=name), self.assertRaises(ImportPreflightError):
                    validate_selected_layout(path, "point_table")

    def test_unstable_columns_fail_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [",".join(str(i) for i in range(width))
                    for width in ([3, 8, 4, 11, 5, 9] * 20)]
            path = self._file(directory, "unstable.csv", "\n".join(rows))
            with self.assertRaises(ImportPreflightError):
                validate_selected_layout(path, "height_matrix")

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


if __name__ == "__main__":
    unittest.main()
