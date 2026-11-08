import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.matrix_sampling import MatrixSampler, numeric_matrix_row


class StreamingMatrixTests(unittest.TestCase):
    def test_sparse_lattice_keeps_late_rows_under_cap(self):
        sampler = MatrixSampler(200, 100, stride=10)
        row = np.full(200, np.nan)
        row[::10] = np.arange(20)
        for r in range(200):
            sampler.add(r, row)
        frame = sampler.frame(10, 20)
        self.assertLessEqual(len(frame), 100)
        self.assertGreaterEqual(frame['_matrix_row'].max(), 180)
        self.assertGreaterEqual(frame['_matrix_col'].max(), 180)
        np.testing.assert_allclose(frame.X, frame._matrix_col * .01)
        np.testing.assert_allclose(frame.Y, (199 - frame._matrix_row) * .02)

    def test_very_wide_matrix_does_not_stop_halfway_across(self):
        sampler = MatrixSampler(10001, 100, stride=1)
        for r in range(3):
            sampler.add(r, np.arange(10001, dtype=float))
        frame = sampler.frame(1, 1)
        self.assertLessEqual(len(frame), 100)
        self.assertGreater(frame._matrix_col.max(), 9500)

    def test_grid_merges_preserve_extrema_and_limit(self):
        sampler = MatrixSampler(31, 75, 'spatial_grid', grid_side=5)
        rng = np.random.default_rng(478)
        values = rng.normal(size=(301, 31))
        values[rng.random(values.shape) < .3] = np.nan
        values[300, 30], values[299, 29] = 1e6, -1e6
        for r, row in enumerate(values):
            sampler.add(r, row)
            self.assertLessEqual(len(sampler.cells), 25)
        frame = sampler.frame(10, 20)
        self.assertLessEqual(len(frame), 75)
        self.assertEqual(frame.Z.min(), np.nanmin(values))
        self.assertEqual(frame.Z.max(), np.nanmax(values))
        for p in frame.itertuples():
            self.assertEqual(p.Z, values[int(p[4]), int(p[5])])

    def test_fast_parser_rejects_partial_numbers_and_keeps_fallback_cases(self):
        for row in ('1,2x,3', '1,2 4,3', '1,,3', '1,"2",3', '1,NA,3', '1,2,3,4'):
            with self.subTest(row=row):
                self.assertIsNone(numeric_matrix_row(row, ',', 3))
        np.testing.assert_allclose(numeric_matrix_row(' 1e-3, -2.5, +3. ', ',', 3), [.001, -2.5, 3])
        for row in ('\t1\t2', '1\t\t2', '1\t2\t'):
            self.assertIsNone(numeric_matrix_row(row, '\t', 2))


class ImportWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.settings_dir.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = SurfaceAnalyzerPro()
        self.addCleanup(self.window.close)
        self.window.import_x_col = self.window.import_y_col = self.window.import_z_col = 0
        self.window.import_x_unit = self.window.import_y_unit = self.window.import_z_unit = 'auto'
        self.window.pitch_source = 'manual'
        self.window.height_matrix_pitch_x_um = 10
        self.window.height_matrix_pitch_y_um = 20
        self.window.large_text_import_limit = 100
        self.window.matrix_analysis_threshold = 200
        self.window.auto_sample_large_text = True
        self.window.large_text_stride_n = 10
        self.window.large_file_sample_method = 'file_position'

    def write(self, name, text):
        path = Path(self.directory.name) / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_matrix_import_parses_body_once_and_reports_pitch(self):
        self.window.input_layout_mode = 'height_matrix'
        path = self.write('matrix.csv', '\n'.join(','.join(str(1000 + r * 2.01 + c * .003) for c in range(200)) for r in range(200)))
        with patch.object(self.window, '_parse_height_matrix_line', wraps=self.window._parse_height_matrix_line) as parse:
            frame = self.window._read_table(path)
        self.assertEqual(parse.call_count, 200)
        self.assertEqual(self.window.import_info['matrix_parse_passes'], 1)
        self.assertEqual(self.window.import_info['source_valid_rows'], 40_000)
        self.assertLessEqual(len(frame), 100)
        self.assertGreaterEqual(frame._matrix_row.max(), 180)
        self.window._update_import_status_label()
        text = self.window.lbl_import_status.text()
        self.assertIn('X=10 / Y=20 µm/像素', text)
        self.assertIn('用户手动', text)
        self.assertIn('点间距 X=200 / Y=400', text)
        self.assertFalse(hasattr(self.window, 'lbl_pixel_pitch'))
        trace = '\n'.join(self.window._import_trace_text(self.window.import_info))
        self.assertIn('原始像素 X=10 / Y=20', trace)
        self.assertIn('点间距 X=200 / Y=400', trace)

    def test_z_unit_metadata_does_not_claim_xy_pixel_calibration(self):
        self.window.input_layout_mode = 'height_matrix'
        self.window.pitch_source = 'auto'
        path = self.write('units.csv', 'Z Unit,mm\n1,4,7,2\n2,5,8,3\n4,6,9,5\n')
        with self.assertRaisesRegex(ValueError, 'Auto 未识别到可靠.*Pitch'):
            self.window._read_table(path)

    def test_grid_import_preserves_late_extrema_in_single_pass(self):
        self.window.input_layout_mode = 'height_matrix'
        self.window.large_file_sample_method = 'spatial_grid'
        values = np.arange(600).reshape(30, 20)
        values[29, 19], values[29, 18] = 100000, -100000
        path = self.write('grid.csv', '\n'.join(','.join(map(str, row)) for row in values))
        frame = self.window._read_table(path)
        self.assertLessEqual(len(frame), 100)
        self.assertEqual((frame.Z.min(), frame.Z.max()), (-100000, 100000))
        self.assertTrue(self.window.import_info['extrema_preserved'])
        self.assertEqual(self.window.import_info['matrix_parse_passes'], 1)

    def test_async_ambiguous_table_is_kept_until_units_and_mapping_selected(self):
        self.window.input_layout_mode = 'point_table'
        path = self.write('unknown.csv', 'A,B,C,D\n0,0,1,9\n1,0,2,9\n0,1,3,9\n1,1,4,9\n')
        old = pd.DataFrame({'X': [8], 'Y': [8], 'Z': [8]})
        self.window.df_raw = old
        self.window.show()
        self.app.processEvents()

        def run(name, task, success, *args, **kwargs):
            success(task(lambda *a: None, threading.Event()))
            return True

        with patch.object(self.window, '_run_background_task', side_effect=run), \
                patch.object(QMessageBox, 'information'), patch.object(QMessageBox, 'critical') as error:
            self.assertTrue(self.window.load_path(path))
            error.assert_not_called()
        self.assertTrue(self.window.import_info['mapping_required'])
        self.assertIsNone(self.window.df_raw)
        self.assertEqual(list(self.window.absolute_raw_df.columns), ['A', 'B', 'C', 'D'])
        self.assertIsNone(self.window._import_dialog)
        for combo in (self.window.cb_x_col, self.window.cb_y_col, self.window.cb_z_col,
                      self.window.cb_x_unit, self.window.cb_y_unit, self.window.cb_z_unit):
            self.assertEqual(combo.currentIndex(), -1)
        self.window.cb_x_col.setCurrentText('A')
        self.window.cb_y_col.setCurrentText('B')
        self.window.cb_z_col.setCurrentText('C')
        with patch.object(QMessageBox, 'critical') as error:
            self.assertFalse(self.window.apply_mapping())
            error.assert_called_once()
        for combo in (self.window.cb_x_unit, self.window.cb_y_unit, self.window.cb_z_unit):
            combo.setCurrentText('µm')
        with patch.object(self.window, 'update_analysis'), patch.object(QMessageBox, 'critical') as error:
            self.assertTrue(self.window.apply_mapping())
            error.assert_not_called()
        self.assertFalse(self.window.import_info['mapping_required'])
        self.assertEqual(len(self.window.df_raw), 4)
        np.testing.assert_allclose(self.window.df_raw.Z, [.001, .002, .003, .004])

    def test_headerless_three_columns_require_confirmation(self):
        self.window.input_layout_mode = 'point_table'
        path = self.write('three.csv', '0,0,1\n1,0,2\n0,1,3\n')
        with patch.object(QMessageBox, 'information'), patch.object(QMessageBox, 'critical') as error:
            self.assertTrue(self.window.load_path(path))
            error.assert_not_called()
        self.assertTrue(self.window.import_info['mapping_required'])

    def test_auto_sampling_disabled_keeps_all_points(self):
        self.window.input_layout_mode = 'height_matrix'
        self.window.auto_sample_large_text = False
        path = self.write('full.csv', '\n'.join(','.join(str(1000 + r * 2.01 + c * .003) for c in range(30)) for r in range(30)))
        frame = self.window._read_table(path)
        self.assertEqual(len(frame), 900)
        self.assertFalse(self.window.import_info['sampled'])

    def test_sparse_full_import_reuses_points_without_second_parse(self):
        self.window.input_layout_mode = 'height_matrix'
        self.window.height_matrix_cols = 80
        self.window.large_text_threshold_mb = 1000
        values = np.full((80, 80), np.nan)
        values[:, 0] = 1.25
        path = self.write('sparse.csv', '\n'.join(
            ','.join('No Data' if np.isnan(z) else str(z) for z in row) for row in values))
        with patch.object(self.window, '_parse_height_matrix_line', wraps=self.window._parse_height_matrix_line) as parse:
            frame = self.window._read_table(path)
        self.assertEqual(len(frame), 80)
        self.assertFalse(self.window.import_info['sampled'])
        self.assertEqual(self.window.import_info['matrix_parse_passes'], 1)
        self.assertEqual(parse.call_count, 80)


if __name__ == '__main__':
    unittest.main()
