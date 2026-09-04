import os
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QGroupBox, QLabel, QToolButton, QWidget,
)

from surface_analyzer import AnalysisOptions, analyze_file, analyze_xyz, compare_plane_results
from surface_analyzer import APP_VERSION
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.mixins.data_io import DataIOMixin
from surface_analyzer.mixins.roi import ROIMixin
from surface_analyzer.mixins.reporting import ReportingMixin
from surface_analyzer.mixins.gap import GapAnalysisMixin
from surface_analyzer.plotting import surface_box_aspect
from surface_analyzer.polynomial import fit_polynomial_surface, evaluate_polynomial_surface
from surface_analyzer.workers import sha256_file


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

    def test_keyence_style_height_matrix_skips_metadata_and_coordinate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keyence_vr_height.csv"
            lines = [
                "KEYENCE VR-3000,基恩士三维轮廓导出",
                "测量模式,表面",
                "1,2,3",
                "4,5,6",
                "7,8,9",
                "校准参数区,结束",
                "横向间距[um],12.5",
                "纵向间距[um],15.0",
                "高度单位,um",
                "无效值,-999.999",
                "," + ",".join(str(i) for i in range(10)) + ",",
            ]
            for row in range(10):
                values = [100.0 + row * 10 + column for column in range(10)]
                if row == 4:
                    values[6] = -999.999
                lines.append(
                    str(row) + "," + ",".join(f"{value:.3f}" for value in values) + ",")
            path.write_text("\n".join(lines) + "\n", encoding="gbk")

            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "height_matrix"
            window.pitch_source = "auto"
            frame = window._read_table(path)
            self.assertTrue(window.import_info["height_matrix"])
            self.assertEqual(window.import_info["matrix_rows"], 10)
            self.assertEqual(window.import_info["matrix_cols"], 10)
            self.assertEqual(window.import_info["matrix_data_start_row"], 12)
            self.assertEqual(window.import_info["layout_candidate_count"], 2)
            self.assertTrue(window.import_info["matrix_coordinate_header"])
            self.assertEqual(window.import_info["matrix_invalid_values"], [-999.999])
            self.assertAlmostEqual(window.import_info["matrix_pitch_x_um"], 12.5)
            self.assertAlmostEqual(window.import_info["matrix_pitch_y_um"], 15.0)
            self.assertEqual(window.import_info["matrix_z_unit"], "µm")
            self.assertEqual(len(frame), 99)
            self.assertNotIn(-999.999, frame["Z"].to_numpy())
            self.assertIn("跳过前置说明: 11 行", window.last_import_note)
            window.close()

    def test_v460_recipe_persists_unified_search_start_row(self):
        window = SurfaceAnalyzerPro()
        window.import_search_start_row = 123
        window.input_layout_mode = "height_matrix"
        recipe = window._current_recipe_dict()
        self.assertEqual(APP_VERSION, "V4.6.3")
        self.assertEqual(recipe["schema_version"], 7)
        self.assertEqual(recipe["input"]["search_start_row"], 123)
        self.assertNotIn("data_start_row", recipe["input"])
        self.assertNotIn("matrix_start_row", recipe["large_file"])
        self.assertEqual(recipe["large_file"]["sampling_pitch_x_um"],
                         recipe["large_file"]["matrix_pitch_x_um"])
        self.assertEqual(recipe["input"]["layout_mode"], "height_matrix")
        window.close()

    def test_v440_zygo_xyz_uses_fixed_camera_field_and_manual_pitch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zygo_demo.xyz"
            path.write_text("\n".join([
                "Zygo XYZ Data File - Format 1",
                '0 3 0 0 "Tue Aug 25 16:59:23 2026"',
                "0 0 3 3 1 255",
                "10 20 3 3",
                "",
                "",
                "",
                "0 0 6.328e-07 0.5 1 0 5.067e-5 1787677163",
                '3 2 0 0 0 666666 0 ""',
                "0 0 1 33 0 0 0.1 60.4318 2 50",
                "0 1 100 0 0 0 0 0 0 0",
                '0 ""',
                "1 5 0",
                "#",
                "10 20 No Data",
                "11\t20\t2.0",
                "12   20   3.0",
                "10 21 4.0",
                "11 21 No Data",
                "12 21 6.0",
                "10 22 7.0",
                "11 22 8.0",
                "12 22 No Data",
                "#",
            ]) + "\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "zygo_xyz"
            window.height_matrix_pitch_x_um = 100.0
            window.height_matrix_pitch_y_um = 200.0
            frame = window._read_table(path)
            self.assertEqual(window.import_info["source_format"],
                             "Zygo XYZ Data File - Format 1")
            self.assertAlmostEqual(window.import_info["detected_camera_res_um"], 50.67)
            self.assertEqual(window.import_info["missing_points"], 3)
            self.assertEqual(window.import_info["bad_rows"], 0)
            self.assertEqual(window.import_info["matrix_rows"], 3)
            self.assertEqual(window.import_info["matrix_cols"], 3)
            self.assertEqual(len(frame), 9)
            self.assertAlmostEqual(frame.loc[frame["_matrix_col"] == 12, "X"].iloc[0], 0.2)
            self.assertAlmostEqual(frame.loc[frame["_matrix_row"] == 21, "Y"].iloc[0], 0.2)
            self.assertTrue(frame["Z"].isna().sum() == 3)
            window.close()

    def test_v440_point_table_blocks_zygo_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zygo.xyz"
            path.write_text("Zygo XYZ Data File - Format 1\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            with self.assertRaisesRegex(ValueError, "Zygo XYZ"):
                window._read_table(path)
            window.close()

    def test_v440_precitec_dat_keeps_fields_and_maps_physical_xyz(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.dat"
            path.write_text("\n".join([
                "Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;",
                "ScanProgram: <PrecitecFSSExplorer>;",
                "Gain Correction X:1.00000000, Y:1.00000000;",
                "#Object: AreaScan; PointsPerLine: 3; NumberOfLines: 2; PercentileFilter: 50.00",
                "#Attention: Encoder X/Y values are external axis corrected values",
                "# real scanner position (X/Y, ENC X/Y) - external axis position",
                "#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]",
                "1;2;-15848;-16353;240.38;3.99;-44.6810;-43.3014;",
                "1;2;-15849;-16329;241.00;4.16;-44.6158;-43.3037;",
                "1;2;-15849;-16304;bad;3.39;-44.5478;-43.3060;",
                "1;2;-15847;-16271;242.00;3.44;-44.5000;-43.3000;",
            ]) + "\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            self.assertTrue(window.load_path(path))
            self.assertEqual(window.import_info["source_format"],
                             "Precitec FSS Explorer SCAN PATH DATA")
            self.assertEqual(window.cb_x_col.currentText(), "X Pos [mm]")
            self.assertEqual(window.cb_y_col.currentText(), "Y Pos [mm]")
            self.assertEqual(window.cb_z_col.currentText(), "Thickness 1")
            self.assertEqual(window.cb_x_unit.currentText(), "mm")
            self.assertEqual(window.cb_y_unit.currentText(), "mm")
            self.assertEqual(window.cb_z_unit.currentText(), "µm")
            self.assertIn("Intensity", window.absolute_raw_df.columns)
            self.assertEqual(window.import_info["expected_points"], 6)
            self.assertEqual(window.import_info["source_record_rows"], 4)
            self.assertEqual(window.import_info["bad_rows"], 1)
            self.assertAlmostEqual(window.df_raw["Z"].iloc[0], 0.24038)
            self.assertTrue(window.import_info["completeness_warning"])
            trace = ReportingMixin._import_trace_text(window.import_info)
            self.assertTrue(any("Thickness 1" in line and "厚度分布" in line for line in trace))
            self.assertTrue(any("预期/实际 6/4" in line for line in trace))
            window.close()

    def test_v440_recipe_reads_old_pitch_aliases(self):
        window = SurfaceAnalyzerPro()
        recipe = window._current_recipe_dict()
        recipe["large_file"].pop("sampling_pitch_x_um")
        recipe["large_file"].pop("sampling_pitch_y_um")
        recipe["large_file"]["matrix_pitch_x_um"] = 21.5
        recipe["large_file"]["matrix_pitch_y_um"] = 22.5
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
            window.apply_recipe(recipe, remap_current_data=False)
        self.assertAlmostEqual(window.height_matrix_pitch_x_um, 21.5)
        self.assertAlmostEqual(window.height_matrix_pitch_y_um, 22.5)
        window.close()

    def test_v440_device_stream_sampling_uses_real_xyz_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.dat"
            records = []
            for row in range(5):
                for column in range(6):
                    records.append(
                        f"1;2;{-15000 + row};{-16000 + column};{200 + row + column};3.5;"
                        f"{-40 + column * 0.1};{-30 + row * 0.1};")
            path.write_text("\n".join([
                "Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;",
                "#Object: AreaScan; PointsPerLine: 6; NumberOfLines: 5;",
                "#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]",
                *records,
            ]) + "\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            window.large_text_threshold_mb = 0
            window.large_text_import_limit = 12
            window.large_file_sample_method = "spatial_grid"
            frame = window._read_table(path)
            self.assertTrue(window.import_info["sampled"])
            self.assertEqual(window.import_info["sample_method_key"], "spatial_grid")
            self.assertLessEqual(len(frame), 12)
            self.assertTrue(pd.to_numeric(frame["X Pos [mm]"]).between(-40, -39.5).all())
            self.assertTrue(pd.to_numeric(frame["Thickness 1"]).between(200, 209).all())
            self.assertEqual(window.import_info["source_valid_rows"], 30)
            window.close()

    def test_v440_import_dialog_keeps_legacy_and_adds_pixel_layout(self):
        window = SurfaceAnalyzerPro()
        observed = {}

        def inspect_dialog(dialog):
            layout_group = next(group for group in dialog.findChildren(QGroupBox)
                                if group.title() == "文件数据布局")
            layout_combo = layout_group.layout().itemAtPosition(0, 1).widget()
            strategy_group = next(group for group in dialog.findChildren(QGroupBox)
                                  if group.title() == "Zeiss / TXT / ASC / XYZ 大文件策略")
            grid = strategy_group.layout()
            pitch_x = grid.itemAtPosition(8, 1).widget()
            pitch_y = grid.itemAtPosition(9, 1).widget()
            matrix_unit = grid.itemAtPosition(10, 1).widget()
            labels = [label.text() for label in strategy_group.findChildren(QLabel)]
            layout_labels = [label.text() for label in layout_group.findChildren(QLabel)]
            search_start = layout_group.layout().itemAtPosition(2, 1).widget()
            observed["layouts"] = [layout_combo.itemData(i) for i in range(layout_combo.count())]
            observed["labels"] = labels
            observed["layout_labels"] = layout_labels
            layout_combo.setCurrentIndex(layout_combo.findData("point_table"))
            observed["point_pitch"] = (pitch_x.isEnabled(), pitch_y.isEnabled())
            layout_combo.setCurrentIndex(layout_combo.findData("pixel_xy"))
            observed["pixel_pitch"] = (pitch_x.isEnabled(), pitch_y.isEnabled())
            observed["pixel_matrix_unit"] = matrix_unit.isEnabled()
            layout_combo.setCurrentIndex(layout_combo.findData("zygo_xyz"))
            observed["zygo_search_enabled"] = search_start.isEnabled()
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", inspect_dialog):
            window.show_bigfile_settings_dialog()
        self.assertEqual(
            observed["layouts"],
            ["point_table", "pixel_xy", "height_matrix", "zygo_xyz"])
        self.assertIn("X 采样间距 (µm/点):", observed["labels"])
        self.assertIn("Y 采样间距 (µm/点):", observed["labels"])
        self.assertIn("正文搜索起始行:", observed["layout_labels"])
        self.assertEqual(observed["point_pitch"], (False, False))
        self.assertEqual(observed["pixel_pitch"], (True, True))
        self.assertFalse(observed["pixel_matrix_unit"])
        self.assertFalse(observed["zygo_search_enabled"])
        window.close()

    def test_advanced_import_settings_are_collapsed_by_default(self):
        window = SurfaceAnalyzerPro()
        observed = {}

        def inspect_dialog(dialog):
            advanced = next(group for group in dialog.findChildren(QGroupBox)
                            if group.title() == "高级解析覆盖")
            body = advanced.findChild(QWidget, "advancedParsingBody")
            toggle = advanced.findChild(QToolButton)
            observed["hidden"] = body.isHidden()
            observed["has_toggle"] = toggle is not None
            if toggle is not None:
                toggle.click()
            observed["hidden_after_expand"] = body.isHidden()
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", inspect_dialog):
            window.show_bigfile_settings_dialog()
        self.assertTrue(observed["hidden"])
        self.assertTrue(observed["has_toggle"])
        self.assertFalse(observed["hidden_after_expand"])
        window.close()

    def test_v430_origin_tile_tracks_pipeline_state(self):
        window = SurfaceAnalyzerPro()
        self.assertEqual(window.pose_origin_tile.objectName(), "poseTile")
        window.transform_pipeline = ["ORIGIN(0,0)"]
        window._update_pipeline_label()
        self.assertEqual(window.pose_origin_tile.objectName(), "poseTileActive")
        window.close()

    def test_v430_gap_worker_keeps_subtraction_semantics(self):
        x = np.array([0.0, 1.0, 0.0, 1.0] * 3)
        y = np.repeat([0.0, 1.0, 2.0], 4)
        base = {'x': x, 'y': y, 'z': np.full(12, 1.0), 'name': 'base', 'sampled': False,
                'metric_quality': {'extrema_preserved': True}}
        stack = {'x': x, 'y': y, 'z': np.full(12, 1.25), 'name': 'stack', 'sampled': False,
                 'metric_quality': {'extrema_preserved': True}}
        result = GapAnalysisMixin._compute_gap_payload(
            stack, base, None, 1e-9, lambda value, text: None, threading.Event())
        np.testing.assert_allclose(result['z'], 0.25)
        self.assertEqual(len(result['z']), 12)

    def test_v430_streaming_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(b"surface-rxy" * 1000)
            progress = []
            value = sha256_file(path, lambda percent, text: progress.append(percent), threading.Event())
            self.assertEqual(len(value), 64)
            self.assertEqual(progress[-1], 100)

    def test_recipe_large_file_settings_are_clamped(self):
        window = SurfaceAnalyzerPro()
        recipe = window._current_recipe_dict()
        recipe["large_file"].update({
            "grid_count": 999999,
            "stride_n": 10**12,
            "threshold_mb": -20,
            "import_limit": 10**12,
            "display_limit": -1,
            "matrix_pitch_x_um": 1e20,
            "matrix_pitch_y_um": -5,
            "sampling_pitch_x_um": 1e20,
            "sampling_pitch_y_um": -5,
            "matrix_start_row": 999999,
        })
        recipe["input"]["search_start_row"] = 99_999_999
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
            window.apply_recipe(recipe, remap_current_data=False)
        self.assertEqual(window.large_text_grid_count, 2000)
        self.assertEqual(window.large_text_stride_n, 1000000)
        self.assertEqual(window.large_text_threshold_mb, 1)
        self.assertEqual(window.large_text_import_limit, 5000000)
        self.assertEqual(window.display_point_limit, 5000)
        self.assertEqual(window.height_matrix_pitch_x_um, 1e6)
        self.assertEqual(window.height_matrix_pitch_y_um, 0.0001)
        self.assertEqual(window.import_search_start_row, 10_000_000)
        window.close()

    def test_matrix_legacy_missing_values_do_not_remove_real_deep_values(self):
        values = np.array([-1200.0, -1000.0, -999.999, -999.5, 1.0])
        masked = DataIOMixin._mask_matrix_missing_values(values)
        self.assertTrue(np.isfinite(masked[0]))
        self.assertTrue(np.isnan(masked[1]))
        self.assertTrue(np.isnan(masked[2]))
        self.assertTrue(np.isfinite(masked[3]))

    def test_batch_report_paths_do_not_overwrite_duplicate_stems(self):
        with tempfile.TemporaryDirectory() as directory:
            reserved = set()
            first = ReportingMixin._unique_batch_report_path(directory, "a/sample.csv", reserved)
            second = ReportingMixin._unique_batch_report_path(directory, "b/sample.csv", reserved)
            self.assertEqual(first.name, "result_sample.png")
            self.assertEqual(second.name, "result_sample_2.png")

    def test_3d_aspect_preserves_xy_geometry_and_bounds_flat_z(self):
        x = np.array([0.0, 12.0])
        y = np.array([0.0, 3.0])
        z = np.array([0.100, 0.130])
        aspect = surface_box_aspect(x, y, z)
        self.assertAlmostEqual(aspect[0], 1.0)
        self.assertAlmostEqual(aspect[1], 0.25)
        self.assertAlmostEqual(aspect[2], 0.18)

    def test_3d_aspect_allows_a_more_readable_gui_z_floor(self):
        aspect = surface_box_aspect(
            [0.0, 0.30], [0.0, 0.25], [0.000024, 0.001526],
            min_z_ratio=0.28)
        self.assertAlmostEqual(aspect[0], 1.0)
        self.assertAlmostEqual(aspect[1], 0.25 / 0.30)
        self.assertAlmostEqual(aspect[2], 0.28)

    def test_3d_aspect_uses_real_z_ratio_when_large_enough(self):
        aspect = surface_box_aspect([0.0, 10.0], [0.0, 5.0], [0.0, 2.0])
        self.assertEqual(aspect, (1.0, 0.5, 0.2))

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
            source.cb_surface_display.setCurrentIndex(
                source.cb_surface_display.findData("residual_1"))
            source._on_surface_display_changed()
            counts.append(self._select_and_delete(source, "YZ", 3.5, -0.001, 4.5, 0.001))
            self.assertEqual(counts, [4, 5, 4])
            recipe = source._current_recipe_dict()
            json.dumps(recipe, ensure_ascii=False)
            expected_mask = source.manual_mask.copy()
            self.assertEqual(recipe["schema_version"], 7)
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

    def test_point_table_mode_does_not_use_matrix_width_heuristic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide_point_table.csv"
            rows = [",".join(f"C{i}" for i in range(10))]
            for row in range(12):
                rows.append(",".join(str(row * 10 + col) for col in range(10)))
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            frame = window._read_table(path)
            self.assertFalse(window.import_info["height_matrix"])
            self.assertEqual(frame.shape, (12, 10))
            window.close()

    def test_semicolon_point_table_keeps_text_fields_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe_export.txt"
            path.write_text(
                "Probe;CHRocodile\nScan speed;10 mm/s\n"
                "X;Y;Z;Intensity;Probe;Quality\n"
                "0;0;1.001;52000;P-A;Valid\n"
                "1;0;1.002;51900;P-A;Valid\n"
                "0;1;1.003;51800;P-A;Valid\n"
                "1;1;1.004;51700;P-A;Valid\n",
                encoding="utf-8",
            )
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            frame = window._read_table(path)
            self.assertEqual(list(frame.columns), ["X", "Y", "Z", "Intensity", "Probe", "Quality"])
            self.assertEqual(frame.iloc[0]["Probe"], "P-A")
            self.assertEqual(len(frame), 4)
            self.assertEqual(window.import_info["metadata"]["Probe"], "CHRocodile")
            window.close()

    def test_excel_single_physical_column_is_split_into_xyz_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packed_probe.xlsx"
            values = [
                "Probe;CHRocodile", "Scan speed;10 mm/s",
                "X;Y;Z;Intensity;Probe;Quality",
                "0;0;1.001;52000;P-A;Valid",
                "1;0;1.002;51900;P-A;Valid",
                "0;1;1.003;51800;P-A;Valid",
                "1;1;1.004;51700;P-A;Valid",
            ]
            pd.DataFrame({"A": values}).to_excel(path, header=False, index=False)
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "point_table"
            frame = window._read_table(path)
            self.assertEqual(list(frame.columns), ["X", "Y", "Z", "Intensity", "Probe", "Quality"])
            self.assertTrue(window.import_info["packed_single_column"])
            self.assertEqual(window.import_info["metadata"]["Probe"], "CHRocodile")
            self.assertEqual(len(frame), 4)
            window.close()

    def test_explicit_excel_height_matrix_skips_metadata_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "height_matrix.xlsx"
            raw = pd.DataFrame([
                ["Instrument", "Demo", None],
                ["Mode", "Surface", None],
                [1.0, 2.0, 3.0],
                [4.0, 5.0, 6.0],
                [7.0, 8.0, 9.0],
            ])
            raw.to_excel(path, header=False, index=False)
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = "height_matrix"
            frame = window._read_table(path)
            self.assertTrue(window.import_info["height_matrix"])
            self.assertEqual(window.import_info["matrix_rows"], 3)
            self.assertEqual(window.import_info["matrix_cols"], 3)
            self.assertEqual(len(frame), 9)
            window.close()

    def test_high_order_residual_separates_second_and_third_order_shape(self):
        x, y = np.meshgrid(np.linspace(-10, 10, 31), np.linspace(-8, 8, 27))
        z = (1.0 + 2e-5 * x - 3e-5 * y + 2e-5 * x ** 2 - 1.5e-5 * x * y
             + 8e-6 * y ** 2 + 1.2e-6 * x ** 3 - 8e-7 * x * y ** 2)
        xf, yf, zf = x.ravel(), y.ravel(), z.ravel()
        model2 = fit_polynomial_surface(xf, yf, zf, 2)
        model3 = fit_polynomial_surface(xf, yf, zf, 3)
        residual2 = zf - evaluate_polynomial_surface(model2, xf, yf)
        residual3 = zf - evaluate_polynomial_surface(model3, xf, yf)
        self.assertGreater(np.ptp(residual2), 1e-4)
        self.assertLess(np.ptp(residual3), 1e-10)
        self.assertLess(model3["residual_pv_um"], model2["residual_pv_um"])


if __name__ == "__main__":
    unittest.main()
