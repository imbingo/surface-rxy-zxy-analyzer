import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.delimited_text import detect_delimiter, tokenize_delimited_line
from surface_analyzer.file_io import load_xyz_points


class V460QuotedCsvAndSearchStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QSettings("SurfaceRxyZxyAnalyzer", "SurfaceAnalyzer")
        self.had_saved_layout = self.settings.contains("input_layout_mode")
        self.saved_layout = self.settings.value("input_layout_mode")
        self.window = SurfaceAnalyzerPro()
        self.window.import_encoding = "auto"
        self.window.import_delimiter = "auto"
        self.window.import_search_start_row = 0
        self.window.auto_sample_large_text = False

    def tearDown(self):
        self.window.close()
        if self.had_saved_layout:
            self.settings.setValue("input_layout_mode", self.saved_layout)
        else:
            self.settings.remove("input_layout_mode")
        self.settings.sync()
        self.directory.cleanup()

    def write_text(self, name, text, encoding="utf-8"):
        path = Path(self.directory.name) / name
        path.write_text(text, encoding=encoding, newline="")
        return path

    def read_matrix(self, text, name="matrix.csv"):
        self.window.input_layout_mode = "height_matrix"
        return self.window._read_table(self.write_text(name, text))

    def test_shared_tokenizer_unquotes_numbers_missing_and_embedded_delimiter(self):
        self.assertEqual(
            tokenize_delimited_line('"8.030","7.630","","-48.970"\r\n', ','),
            ["8.030", "7.630", "", "-48.970"],
        )
        self.assertEqual(
            tokenize_delimited_line('"1";"text,with,comma";"3"', ';'),
            ["1", "text,with,comma", "3"],
        )
        self.assertEqual(tokenize_delimited_line('"a""b","2"', ','), ['a"b', '2'])
        self.assertEqual(detect_delimiter('"1";"text,with,comma";"3"'), ';')

    def test_quoted_pure_matrix(self):
        frame = self.read_matrix(
            '"1.000","2.000","3.000"\r\n'
            '"4.000","5.000","6.000"\r\n'
            '"7.000","8.000","9.000"\r\n')
        self.assertEqual(self.window.import_info["matrix_rows"], 3)
        self.assertEqual(self.window.import_info["matrix_cols"], 3)
        self.assertEqual(len(frame), 9)

    def test_quoted_missing_cells_keep_raster_positions(self):
        frame = self.read_matrix(
            '"","","1.000","2.000"\n'
            '"","3.000","","4.000"\n'
            '"5.000","","6.000",""\n')
        self.assertEqual((self.window.import_info["matrix_rows"],
                          self.window.import_info["matrix_cols"]), (3, 4))
        positions = set(zip(frame["_matrix_row"], frame["_matrix_col"]))
        self.assertEqual(positions, {(0, 2), (0, 3), (1, 1), (1, 3), (2, 0), (2, 2)})

    def test_confirmed_matrix_keeps_fully_missing_logical_row(self):
        frame = self.read_matrix(
            '"文件类型","ImageDataCsv"\n'
            '"水平","3"\n'
            '"垂直","4"\n'
            '"高度"\n'
            '"1","2","3"\n'
            '"","",""\n'
            '"4","5","6"\n'
            '"7","8","9"\n')
        self.assertEqual(self.window.import_info["matrix_rows"], 4)
        self.assertEqual(self.window.import_info["matrix_cols"], 3)
        self.assertNotIn(1, set(frame["_matrix_row"]))
        self.assertEqual(len(frame), 9)

    def test_quoted_keyence_metadata_sparse_leading_rows_and_search_lower_bound(self):
        self.window.input_layout_mode = "height_matrix"
        self.window.pitch_source = "auto"
        self.window.import_search_start_row = 10
        path = self.write_text(
            "keyence.csv",
            '"测量日期","2026/3/30 15:33"\r\n'
            '"机型","VR-6000"\r\n'
            '"文件类型","ImageDataCsv"\r\n'
            '"XY 校准","47.242","μm"\r\n'
            '"输出图像数据","高度"\r\n'
            '"水平","6"\r\n'
            '"垂直","5"\r\n'
            '"单位","μm"\r\n'
            '"基准数据名称",""\r\n'
            '\r\n'
            '"高度"\r\n'
            '"","","","","1.0","2.0"\r\n'
            '"","","","3.0","4.0","5.0"\r\n'
            '"1.0","2.0","3.0","4.0","5.0","6.0"\r\n'
            '"7.0","8.0","9.0","10.0","11.0","12.0"\r\n'
            '"13.0","14.0","15.0","16.0","",""\r\n',
            encoding="gbk",
        )
        frame = self.window._read_table(path)
        info = self.window.import_info
        self.assertEqual(info["source_format"], "Keyence VR ImageDataCsv")
        self.assertEqual((info["matrix_rows"], info["matrix_cols"]), (5, 6))
        self.assertEqual(info["matrix_data_start_row"], 12)
        self.assertAlmostEqual(info["matrix_pitch_x_um"], 47.242)
        self.assertAlmostEqual(info["matrix_pitch_y_um"], 47.242)
        self.assertEqual(info["matrix_z_unit"], "µm")
        self.assertIn((0, 4), set(zip(frame["_matrix_row"], frame["_matrix_col"])))

    def test_search_lower_bound_applies_to_xyz_and_pixel_xy(self):
        xyz = self.write_text(
            "quoted_xyz.csv",
            '"999","999","999"\nmetadata\n'
            '"1","2","3"\n"4","5","6"\n"7","8","9"\n')
        self.window.input_layout_mode = "point_table"
        self.window.import_search_start_row = 3
        xyz_frame = self.window._read_table(xyz)
        self.assertEqual(len(xyz_frame), 3)
        self.assertNotIn("999", set(xyz_frame["Col1"]))

        pixel = self.write_text(
            "quoted_pixel.csv",
            '"99","99","99"\nmetadata\n'
            '"0","0","1.0"\n"1","0","2.0"\n'
            '"0","1",""\n"1","1","4.0"\n')
        self.window.input_layout_mode = "pixel_xy"
        self.window.import_search_start_row = 3
        pixel_frame = self.window._read_table(pixel)
        self.assertEqual(len(pixel_frame), 4)
        self.assertEqual(pixel_frame.iloc[2, 2], "")

    def test_recipe_schema7_migrates_both_legacy_start_rows(self):
        matrix_recipe = self.window._current_recipe_dict()
        matrix_recipe["schema_version"] = 6
        matrix_recipe["input"].pop("search_start_row", None)
        matrix_recipe["input"].update({"layout_mode": "height_matrix", "data_start_row": 15})
        matrix_recipe["large_file"]["matrix_start_row"] = 23
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"):
            self.window.apply_recipe(matrix_recipe, remap_current_data=False)
        self.assertEqual(self.window.import_search_start_row, 23)
        saved = self.window._current_recipe_dict()
        self.assertEqual(saved["schema_version"], 7)
        self.assertEqual(saved["input"]["search_start_row"], 23)
        self.assertNotIn("data_start_row", saved["input"])
        self.assertNotIn("matrix_start_row", saved["large_file"])

        point_recipe = self.window._current_recipe_dict()
        point_recipe["schema_version"] = 6
        point_recipe["input"].pop("search_start_row", None)
        point_recipe["input"].update({"layout_mode": "point_table", "data_start_row": 15})
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"):
            self.window.apply_recipe(point_recipe, remap_current_data=False)
        self.assertEqual(self.window.import_search_start_row, 15)

        synonym_recipe = self.window._current_recipe_dict()
        synonym_recipe["schema_version"] = 6
        synonym_recipe["input"].pop("search_start_row", None)
        synonym_recipe["input"].update({
            "layout_mode": "height_matrix",
            "height_matrix_start_row": 19,
            "data_start_row": 15,
        })
        synonym_recipe["large_file"]["matrix_start_row"] = 0
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"):
            self.window.apply_recipe(synonym_recipe, remap_current_data=False)
        self.assertEqual(self.window.import_search_start_row, 19)

        future_recipe = self.window._current_recipe_dict()
        future_recipe["schema_version"] = 99
        with self.assertRaisesRegex(ValueError, "高于当前支持"):
            self.window.apply_recipe(future_recipe, remap_current_data=False)

    def test_search_lower_bound_applies_to_excel_rows(self):
        path = Path(self.directory.name) / "search_start.xlsx"
        pd.DataFrame([
            [999, 999, 999],
            ["metadata", None, None],
            [1, 2, 3],
            [4, 5, 6],
            [7, 8, 9],
        ]).to_excel(path, header=False, index=False)
        self.window.input_layout_mode = "point_table"
        self.window.import_search_start_row = 3
        frame = self.window._read_table(path)
        self.assertEqual(len(frame), 3)
        self.assertNotIn(999, set(frame["Col1"]))

    def test_quoted_extra_text_is_preserved_without_unsafe_auto_mapping(self):
        self.window.input_layout_mode = "point_table"
        frame = self.window._read_table(self.write_text(
            "quality.csv",
            '"1.0","2.0","3.0","OK"\n'
            '"4.0","5.0","6.0","NG"\n'
            '"7.0","8.0","9.0","OK"\n'))
        self.assertEqual(list(frame["Col4"]), ["OK", "NG", "OK"])
        self.assertIsNone(self.window._infer_xyz_column_indices(list(frame.columns), 4))

    def test_expected_width_trims_only_missing_tail(self):
        frame = self.read_matrix(
            '"水平","4"\n"垂直","3"\n"高度"\n'
            '"1","2","3","4",""\n'
            '"5","6","7","8","",""\n'
            '"9","10","11","12"\n')
        self.assertEqual((self.window.import_info["matrix_rows"],
                          self.window.import_info["matrix_cols"]), (3, 4))
        self.assertEqual(len(frame), 12)

        bad = self.write_text(
            "bad_width.csv",
            '"水平","4"\n"垂直","3"\n"高度"\n'
            '"1","2","3","4","99"\n'
            '"5","6","7","8","99"\n'
            '"9","10","11","12","99"\n')
        with self.assertRaisesRegex(ValueError, "超出列包含非空数据"):
            self.window._read_table(bad)

    def test_quoted_matrix_keeps_streaming_stride_sampling(self):
        self.window.input_layout_mode = "height_matrix"
        self.window.auto_sample_large_text = True
        self.window.matrix_analysis_threshold = 20
        self.window.large_file_sample_method = "file_position"
        self.window.large_text_import_limit = 100
        rows = [','.join(f'"{row * 10 + col}.0"' for col in range(10))
                for row in range(10)]
        frame = self.window._read_table(
            self.write_text("quoted_large_matrix.csv", '\n'.join(rows) + '\n'))
        self.assertTrue(self.window.import_info["sampled"])
        self.assertEqual(self.window.import_info["sample_method_key"], "stride")
        self.assertEqual(self.window.import_info["source_matrix_positions"], 100)
        self.assertLessEqual(len(frame), 100)

    def test_quoted_pixel_large_path_uses_search_lower_bound(self):
        self.window.input_layout_mode = "pixel_xy"
        self.window.import_search_start_row = 3
        self.window.auto_sample_large_text = True
        self.window.large_text_threshold_mb = 0
        rows = ['"999","999","999"', 'metadata']
        rows.extend(f'"{col}","{row}","{row * 10 + col}.0"'
                    for row in range(4) for col in range(4))
        frame = self.window._read_table(
            self.write_text("quoted_large_pixel.csv", '\n'.join(rows) + '\n'))
        self.assertTrue(self.window.import_info["sampled"])
        self.assertEqual(self.window.import_info["source_valid_rows"], 16)
        self.assertEqual(len(frame), 16)
        self.assertNotIn(999, set(frame["_matrix_col"]))

    def test_public_api_reads_quoted_xyz(self):
        path = self.write_text(
            "api.csv",
            '"1.0","2.0","3.0"\n'
            '"4.0","5.0","6.0"\n'
            '"7.0","8.0","9.0"\n')
        loaded = load_xyz_points(path)
        np.testing.assert_allclose(loaded.x, [1.0, 4.0, 7.0])
        np.testing.assert_allclose(loaded.z, [3.0, 6.0, 9.0])


if __name__ == "__main__":
    unittest.main()
