import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro


class V461MatrixRowsAndSelectionOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def _matrix_path(self, declared_rows=2571, actual_rows=2569):
        path = Path(self.directory.name) / "rows.csv"
        body = "\n".join(
            f'"{row * row + 0.25}","{row * row + 0.75}","{row * row + 1.25}"'
            for row in range(actual_rows)
        )
        path.write_text(
            f'"\u5782\u76f4","{declared_rows}"\n"\u6c34\u5e73","3"\n"\u9ad8\u5ea6"\n{body}\n',
            encoding="utf-8",
        )
        return path

    def _read_text_matrix(self, search_start_row=0, manual_rows=0):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.input_layout_mode = "height_matrix"
        window.import_search_start_row = search_start_row
        window.height_matrix_rows = manual_rows
        window.auto_sample_large_text = False
        frame = window._read_table(self._matrix_path())
        return window, frame

    def test_text_metadata_row_mismatch_is_warning_with_manual_start(self):
        window, frame = self._read_text_matrix(search_start_row=4)
        self.assertEqual(window.import_info["matrix_rows"], 2569)
        self.assertEqual(window.import_info["declared_rows"], 2571)
        self.assertEqual(window.import_info["actual_rows"], 2569)
        self.assertTrue(window.import_info["row_count_mismatch"])
        self.assertIsNone(window.import_info["manual_expected_rows"])
        self.assertIn("已按实际矩阵正文导入", window.import_info["notes"])
        self.assertEqual(len(frame), 2569 * 3)

    def test_text_metadata_row_mismatch_is_warning_in_auto_mode(self):
        window, _ = self._read_text_matrix(search_start_row=0)
        self.assertEqual(window.import_info["matrix_rows"], 2569)
        self.assertTrue(window.import_info["row_count_mismatch"])

    def test_manual_matrix_rows_remains_a_hard_constraint(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.input_layout_mode = "height_matrix"
        window.import_search_start_row = 4
        window.height_matrix_rows = 2571
        window.auto_sample_large_text = False
        with self.assertRaisesRegex(ValueError, "用户指定 2,571.*实际识别 2,569"):
            window._read_table(self._matrix_path())

    def test_excel_metadata_row_mismatch_is_warning(self):
        path = Path(self.directory.name) / "rows.xlsx"
        pd.DataFrame([
            ["垂直", 5, None],
            ["水平", 3, None],
            ["高度", None, None],
            [1, 2, 3],
            [4, 5, 6],
            [7, 8, 9],
            [10, 11, 12],
        ]).to_excel(path, header=False, index=False)
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.input_layout_mode = "height_matrix"
        window.height_matrix_rows = 0
        window.auto_sample_large_text = False
        frame = window._read_table(path)
        self.assertEqual(window.import_info["matrix_rows"], 4)
        self.assertEqual(window.import_info["declared_rows"], 5)
        self.assertEqual(window.import_info["actual_rows"], 4)
        self.assertTrue(window.import_info["row_count_mismatch"])
        self.assertEqual(len(frame), 12)

    def test_fully_missing_logical_matrix_row_keeps_y_geometry(self):
        path = Path(self.directory.name) / "missing-row.csv"
        path.write_text(
            '"\u5782\u76f4","4"\n"\u6c34\u5e73","3"\n"\u9ad8\u5ea6"\n'
            '"1","2","3"\n"","",""\n"4","5","6"\n"7","8","9"\n',
            encoding="utf-8",
        )
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.input_layout_mode = "height_matrix"
        window.auto_sample_large_text = False
        frame = window._read_table(path)
        self.assertEqual(window.import_info["matrix_rows"], 4)
        self.assertNotIn(1, set(frame["_matrix_row"]))
        self.assertIn(2, set(frame["_matrix_row"]))

    @staticmethod
    def _selection_window(side=8):
        yy, xx = np.mgrid[0:side, 0:side]
        x = xx.ravel().astype(float)
        y = yy.ravel().astype(float)
        z = 1.0 + 0.001 * x + 0.002 * y
        window = SurfaceAnalyzerPro()
        window.input_layout_mode = "point_table"
        window.df_raw = pd.DataFrame({"X": x, "Y": y, "Z": z})
        window.manual_mask = np.ones(len(x), dtype=bool)
        window.temp_selected_mask = np.zeros(len(x), dtype=bool)
        window.roi_shapes = []
        window.roi_enabled = False
        window.selection_mode = "delete"
        window.update_analysis()
        return window, x, y, z

    @staticmethod
    def _limits(window):
        return (
            window.canvas.ax_xy.get_xlim(), window.canvas.ax_xy.get_ylim(),
            window.canvas.ax_xz.get_xlim(), window.canvas.ax_xz.get_ylim(),
            window.canvas.ax_yz.get_xlim(), window.canvas.ax_yz.get_ylim(),
            window.canvas.ax3d.get_xlim3d(), window.canvas.ax3d.get_ylim3d(),
            window.canvas.ax3d.get_zlim3d(),
            (window.canvas.ax3d.elev, window.canvas.ax3d.azim),
        )

    def test_xy_xz_yz_selection_share_one_mask_and_four_overlays(self):
        window, _, _, _ = self._selection_window()
        self.addCleanup(window.close)
        window.canvas.ax_xy.set_xlim(0.5, 5.5); window.canvas.ax_xy.set_ylim(0.4, 5.4)
        window.canvas.ax_xz.set_xlim(0.6, 5.6); window.canvas.ax_xz.set_ylim(0.999, 1.03)
        window.canvas.ax_yz.set_xlim(0.7, 5.7); window.canvas.ax_yz.set_ylim(0.999, 1.03)
        window.canvas.ax3d.set_xlim3d(0.3, 5.3); window.canvas.ax3d.set_ylim3d(0.2, 5.2)
        window.canvas.ax3d.set_zlim3d(0.999, 1.03); window.canvas.ax3d.view_init(22, -41)
        before_limits = self._limits(window)
        before_active = window.active_idx.copy()
        before_manual = window.manual_mask.copy()
        before_rois = copy.deepcopy(window.roi_shapes)
        before_metrics = copy.deepcopy(window.last_metrics)

        boxes = {
            "XY": ((1.0, 1.0), (3.0, 3.0)),
            "XZ": ((1.0, 0.99), (3.0, 1.05)),
            "YZ": ((1.0, 0.99), (3.0, 1.05)),
        }
        for view, (start, end) in boxes.items():
            with self.subTest(view=view):
                window.on_select(SimpleNamespace(xdata=start[0], ydata=start[1]),
                                 SimpleNamespace(xdata=end[0], ydata=end[1]), view)
                selected_idx = np.flatnonzero(window.temp_selected_mask)
                self.assertGreater(len(selected_idx), 0)
                np.testing.assert_array_equal(
                    window._last_temp_selection_display_indices, selected_idx)
                self.assertEqual(
                    set(window._temp_selection_overlay_artists), {"XY", "XZ", "YZ", "3D"})
                self.assertGreater(
                    window._temp_selection_overlay_artists["XY"].get_zorder(), 2)

        np.testing.assert_array_equal(window.active_idx, before_active)
        np.testing.assert_array_equal(window.manual_mask, before_manual)
        self.assertEqual(window.roi_shapes, before_rois)
        self.assertEqual(window.last_metrics, before_metrics)
        after_limits = self._limits(window)
        for old, new in zip(before_limits, after_limits):
            np.testing.assert_allclose(old, new)

        window.cancel_temp_selection()
        self.assertEqual(int(window.temp_selected_mask.sum()), 0)
        self.assertEqual(window._temp_selection_overlay_artists, {})

    def test_selection_overlay_sampling_is_display_only_and_3d_uses_plot_z(self):
        window, x, y, z = self._selection_window(side=20)
        self.addCleanup(window.close)
        window.display_point_limit = 37
        window.temp_selected_mask[:] = True
        original_mask = window.temp_selected_mask.copy()
        plotted_z = z * 1000.0 + 7.0
        with patch.object(window, "_get_plot_z", return_value=(plotted_z, "display Z", "display")):
            window.update_plots_only()

        np.testing.assert_array_equal(window.temp_selected_mask, original_mask)
        self.assertEqual(int(window.temp_selected_mask.sum()), len(x))
        display_idx = window._last_temp_selection_display_indices
        self.assertEqual(len(display_idx), 37)
        self.assertEqual(set(window._temp_selection_overlay_artists), {"XY", "XZ", "YZ", "3D"})
        x3d, y3d, z3d = window._temp_selection_overlay_artists["3D"]._offsets3d
        np.testing.assert_allclose(np.asarray(x3d), x[display_idx])
        np.testing.assert_allclose(np.asarray(y3d), y[display_idx])
        np.testing.assert_allclose(np.asarray(z3d), plotted_z[display_idx])
        for view in ("XY", "XZ", "YZ", "3D"):
            artist = window._temp_selection_overlay_artists[view]
            self.assertEqual(artist.get_label(), "_temp_selection_overlay")


if __name__ == "__main__":
    unittest.main()
