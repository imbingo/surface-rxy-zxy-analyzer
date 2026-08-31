import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.workers import TaskCancelled


class V455MatrixAndTraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.window.input_layout_mode = "height_matrix"
        self.window.height_matrix_pitch_x_um = 10.0
        self.window.height_matrix_pitch_y_um = 20.0
        self.window.height_matrix_z_unit = "µm"

    def tearDown(self):
        self.window.close()

    def _read_text_matrix(self, text, suffix=".tsv", **settings):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / f"matrix{suffix}"
        path.write_text(text, encoding="utf-8")
        for key, value in settings.items():
            setattr(self.window, key, value)
        return path, self.window._read_table(path)

    def test_fixed_grid_preserves_leading_internal_and_trailing_missing_cells(self):
        _, leading = self._read_text_matrix(
            "\t\t-70\t-50\n\t\t-71\t-51\n\t\t-72\t-52\n")
        self.assertEqual(self.window.import_info["matrix_cols"], 4)
        self.assertEqual(sorted(leading["_matrix_col"].unique().tolist()), [2, 3])
        self.assertEqual(
            int(leading.loc[np.isclose(leading["Z"], -70), "_matrix_col"].iloc[0]), 2)

        _, internal = self._read_text_matrix("1\t\t3\n4\t\t6\n7\t\t9\n")
        self.assertEqual(self.window.import_info["matrix_cols"], 3)
        self.assertEqual(sorted(internal["_matrix_col"].unique().tolist()), [0, 2])

        _, trailing = self._read_text_matrix(
            "1\t2\t\n3\t4\t\n5\t6\t\n", height_matrix_cols=5)
        self.assertEqual(self.window.import_info["matrix_cols"], 5)
        self.assertEqual(self.window.import_info["source_matrix_positions"], 15)
        self.assertEqual(sorted(trailing["_matrix_col"].unique().tolist()), [0, 1])

    def test_metadata_free_tab_and_csv_infer_stable_width_with_holes(self):
        for suffix, text in (
                (".tsv", "1\t\t3\n4\t5\t6\n7\t\t9\n"),
                (".csv", "1,,3\n4,5,6\n7,,9\n")):
            with self.subTest(suffix=suffix):
                self.window.height_matrix_cols = 0
                _, frame = self._read_text_matrix(text, suffix=suffix)
                self.assertEqual(self.window.import_info["matrix_rows"], 3)
                self.assertEqual(self.window.import_info["matrix_cols"], 3)
                self.assertEqual(self.window.import_info["original_valid_points"], 7)
                self.assertEqual(len(frame), 7)

    def test_ambiguous_whitespace_matrix_requires_manual_column_count(self):
        text = "1.0    2.0      3.0\n4.0  5.0    6.0\n7.0      8.0 9.0\n"
        with self.assertRaisesRegex(ValueError, "无法唯一确定 Z Matrix 列宽"):
            self._read_text_matrix(text, suffix=".txt")

    def test_keyence_metadata_and_blank_header_lines(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "keyence.csv"
        path.write_text(
            "测量仪器的型号,VR-6000\n文件类型,ImageDataCsv\n\n"
            "XY 校准,47.242 μm\n水平,3066\n\n垂直,2571\n"
            "输出图像数据,高度\n单位,μm\n\n高度\n",
            encoding="utf-8",
        )
        meta = self.window._scan_height_matrix_metadata(path, "utf-8")
        self.assertEqual(meta["source_format"], "Keyence VR ImageDataCsv")
        self.assertEqual(meta["expected_rows"], 2571)
        self.assertEqual(meta["expected_cols"], 3066)
        self.assertAlmostEqual(meta["pitch_x_um"], 47.242)
        self.assertAlmostEqual(meta["pitch_y_um"], 47.242)
        self.assertEqual(meta["z_unit"], "µm")
        self.assertIsNotNone(meta["height_marker_line"])

    def test_matrix_geometry_is_independent_from_valid_point_count(self):
        _, frame = self._read_text_matrix(
            "1,,3,4,5\n6,7,,9,10\n11,12,13,,15\n16,,18,19,20\n",
            suffix=".csv",
        )
        info = self.window.import_info
        self.assertEqual((info["matrix_rows"], info["matrix_cols"]), (4, 5))
        self.assertEqual(info["source_matrix_positions"], 20)
        self.assertEqual(info["original_valid_points"], 16)
        self.assertEqual(len(frame), 16)

    def test_large_matrix_triggers_by_file_size_or_valid_point_count(self):
        text = "\n".join("\t".join(str(row * 10 + col) for col in range(10))
                         for row in range(10)) + "\n"
        _, frame = self._read_text_matrix(
            text, large_text_threshold_mb=0.000001,
            matrix_analysis_threshold=1_000_000,
            large_text_import_limit=16, large_file_sample_method="file_position")
        self.assertTrue(self.window.import_info["sampled"])
        self.assertIn("文件大小", self.window.import_info["notes"])
        self.assertLessEqual(len(frame), 16)

        self.window.large_text_threshold_mb = 1000.0
        self.window.matrix_analysis_threshold = 20
        _, frame = self._read_text_matrix(
            text, large_text_import_limit=16, large_file_sample_method="file_position")
        self.assertTrue(self.window.import_info["sampled"])
        self.assertIn("有效点数", self.window.import_info["notes"])
        self.assertLessEqual(len(frame), 16)

    def test_small_matrix_is_full_import(self):
        _, frame = self._read_text_matrix(
            "1,2,3\n4,5,6\n7,8,9\n", suffix=".csv",
            large_text_threshold_mb=1000.0, matrix_analysis_threshold=1000)
        self.assertFalse(self.window.import_info["sampled"])
        self.assertEqual(self.window.import_info["analysis_points"], 9)
        self.assertEqual(len(frame), 9)

    def test_excel_large_matrix_samples_before_full_xyz_expansion(self):
        raw = pd.DataFrame(np.arange(400, dtype=float).reshape(20, 20))
        self.window.matrix_analysis_threshold = 50
        self.window.large_text_import_limit = 36
        self.window.large_file_sample_method = "file_position"
        with patch.object(self.window, "_height_matrix_dataframe",
                          side_effect=AssertionError("full XYZ expansion must not run")):
            frame = self.window._read_excel_height_matrix("matrix.xlsx", raw)
        self.assertTrue(self.window.import_info["sampled"])
        self.assertLessEqual(len(frame), 36)
        self.assertEqual(self.window.import_info["source_matrix_positions"], 400)

    def test_sampling_preserves_original_matrix_topology(self):
        text = "\n".join("\t".join(str(row * 12 + col) for col in range(12))
                         for row in range(12)) + "\n"
        _, frame = self._read_text_matrix(
            text, matrix_analysis_threshold=10, large_text_threshold_mb=1000.0,
            large_text_import_limit=20, large_text_stride_n=3,
            large_file_sample_method="file_position")
        rows = sorted(frame["_matrix_row"].unique().tolist())
        cols = sorted(frame["_matrix_col"].unique().tolist())
        self.assertTrue(all(value % 3 == 0 for value in rows + cols))
        self.assertGreater(max(rows), len(rows) - 1)
        self.assertGreater(max(cols), len(cols) - 1)

    def test_matrix_progress_reaches_completion_and_cancel_restores_previous_data(self):
        path, _ = self._read_text_matrix("1\t2\t3\n4\t5\t6\n7\t8\t9\n")
        previous_df = pd.DataFrame({"Z": [1.0, 1.0, 1.0], "X": [0.0, 1.0, 0.0], "Y": [0.0, 0.0, 1.0]})
        self.window.df_raw = previous_df.copy()
        previous_info = {"strategy": "previous", "height_matrix": False}
        self.window.import_info = dict(previous_info)
        self.window.show()
        self.qt_app.processEvents()
        progress_values = []

        def run_success(name, task, on_success, on_error=None, on_cancel=None, **kwargs):
            payload = task(lambda value, message: progress_values.append(int(value)), threading.Event())
            on_success(payload)
            progress_values.append(self.window.task_progress.value())
            return True

        with patch.object(self.window, "_run_background_task", side_effect=run_success):
            self.assertTrue(self.window.load_path(path))
        self.assertTrue(any(0 < value < 100 for value in progress_values))
        self.assertEqual(progress_values[-1], 100)

        loaded_df = self.window.df_raw.copy()
        loaded_info = dict(self.window.import_info)

        def run_cancel(name, task, on_success, on_error=None, on_cancel=None, **kwargs):
            event = threading.Event()
            event.set()
            with self.assertRaises(TaskCancelled):
                task(lambda value, message: None, event)
            on_cancel()
            return True

        with patch.object(self.window, "_run_background_task", side_effect=run_cancel):
            self.assertTrue(self.window.load_path(path))
        pd.testing.assert_frame_equal(self.window.df_raw, loaded_df)
        self.assertEqual(self.window.import_info, loaded_info)

    def test_drop_event_delegates_to_the_same_load_path(self):
        class Url:
            def __init__(self, value): self.value = value
            def toLocalFile(self): return self.value

        class Mime:
            def __init__(self, value): self.value = value
            def hasUrls(self): return True
            def urls(self): return [Url(self.value)]

        class Event:
            def __init__(self, value): self.value = value; self.accepted = False
            def mimeData(self): return Mime(self.value)
            def acceptProposedAction(self): self.accepted = True

        event = Event("C:/demo/matrix.csv")
        with patch.object(self.window, "_is_supported_drop_file", return_value=True), \
                patch.object(self.window, "load_path", return_value=True) as load_path:
            self.window.dropEvent(event)
        load_path.assert_called_once_with("C:/demo/matrix.csv")
        self.assertTrue(event.accepted)

    @staticmethod
    def _parallel_record(name, pipeline, filter_name, offset=0.0):
        x = np.array([0.0, 1.0, 0.0, 1.0])
        y = np.array([0.0, 0.0, 1.0, 1.0])
        a, b, c = 1e-5 + offset, 2e-5 - offset, 0.25 + offset
        z = a * x + b * y + c
        metrics = {
            "a": a, "b": b, "c": c,
            "rx": float(np.arctan(b) * 1e6), "ry": float(np.arctan(-a) * 1e6),
            "rms": 0.0, "pv": 0.0,
            "ttv": float(np.ptp(z) * 1000), "mean_z": float(np.mean(z)),
        }
        return {
            "x": x, "y": y, "z": z, "metrics": metrics, "name": name, "n": len(z),
            "filter": filter_name, "pipeline": pipeline,
            "import_strategy": f"导入-{name}", "sampled": name == "measure",
            "metric_quality": {"label": f"质量-{name}", "estimated": False},
            "import_info": {"strategy": f"导入-{name}"},
        }

    def test_parallel_record_is_snapshot(self):
        self.window.df_raw = pd.DataFrame({
            "Z": [0.25, 0.25001, 0.25002, 0.25003],
            "X": [0.0, 1.0, 0.0, 1.0], "Y": [0.0, 0.0, 1.0, 1.0],
        })
        self.window.manual_mask = np.ones(4, dtype=bool)
        self.window.active_idx = np.arange(4)
        self.window.current_source_name = "base.csv"
        self.window.transform_pipeline = ["ROT180"]
        self.window.import_info = {"strategy": "原始导入", "nested": {"value": 1}}
        record = self.window._current_parallel_record()
        self.window.transform_pipeline.append("FLIPX")
        self.window.import_info["nested"]["value"] = 2
        self.assertEqual(record["pipeline"], "ROT180")
        self.assertEqual(record["import_info"]["nested"]["value"], 1)

    def test_parallel_copy_csv_and_png_keep_independent_traces(self):
        base = self._parallel_record("base", "ROT180", "MAD")
        measure = self._parallel_record("measure", "FLIPX", "邻域", offset=1e-6)
        self.window.parallel_base = base
        self.window.parallel_measure = measure
        self.window.parallel_result = self.window._compute_parallel_result()
        copied = self.window._parallel_result_text()
        for expected in ("基准处理: ROT180", "测量处理: FLIPX", "基准滤波: MAD", "测量滤波: 邻域"):
            self.assertIn(expected, copied)

        figure = self.window._render_parallel_report_figure()
        figure_text = "\n".join(text.get_text() for axis in figure.axes for text in axis.texts)
        self.assertIn("基准处理  ROT180", figure_text)
        self.assertIn("测量处理  FLIPX", figure_text)

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        csv_path = Path(directory.name) / "parallel.csv"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(csv_path), "CSV")), \
                patch.object(QMessageBox, "critical"):
            self.window.export_parallel_csv()
        exported = csv_path.read_text(encoding="utf-8-sig")
        for expected in (
                "# 基准面处理: ROT180", "# 测量面处理: FLIPX",
                "# 基准面滤波: MAD", "# 测量面滤波: 邻域",
                "# 基准面参与拟合点数: 4", "# 测量面参与拟合点数: 4"):
            self.assertIn(expected, exported)

    def test_parallel_numerical_definitions_are_unchanged(self):
        base = self._parallel_record("base", "原始状态", "关闭")
        measure = self._parallel_record("measure", "原始状态", "关闭", offset=3e-6)
        result = self.window._compute_parallel_result_from_records(base, measure)
        expected_drx = measure["metrics"]["rx"] - base["metrics"]["rx"]
        expected_dry = measure["metrics"]["ry"] - base["metrics"]["ry"]
        self.assertAlmostEqual(result["drx"], expected_drx, places=12)
        self.assertAlmostEqual(result["dry"], expected_dry, places=12)
        self.assertAlmostEqual(result["angle"], np.hypot(expected_drx, expected_dry), places=12)
        ref_x = (np.mean(base["x"]) + np.mean(measure["x"])) / 2
        ref_y = (np.mean(base["y"]) + np.mean(measure["y"])) / 2
        expected_step = (
            measure["metrics"]["a"] * ref_x + measure["metrics"]["b"] * ref_y + measure["metrics"]["c"]
            - base["metrics"]["a"] * ref_x - base["metrics"]["b"] * ref_y - base["metrics"]["c"])
        self.assertAlmostEqual(result["step_height"], expected_step, places=12)


if __name__ == "__main__":
    unittest.main()
