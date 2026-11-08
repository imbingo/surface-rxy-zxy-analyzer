import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.data_preview import (derive_columns, source_text_page,
    analysis_states, SOURCE_BYTE_LIMIT)
from surface_analyzer.workers import TaskCancelled
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.data_preview_dialog import DataPreviewDialog
from surface_analyzer.recipe_library import validate_recipe


def definition(name='Z_sum', expression='[Up] + [Down]', unit='µm'):
    return dict(name=name, expression=expression, unit=unit)


class PreviewServiceTests(unittest.TestCase):
    def test_formula_order_missing_and_source_immutability(self):
        source = pd.DataFrame(dict(Up=['2', '', 'a', '5'], Down=[3, 4, 5, 6]), index=[2, 9, 20, 90])
        original = source.copy()
        result = derive_columns(source, [definition(), definition('Z_mm', '[Z_sum] / 1000', 'mm')])
        pd.testing.assert_frame_equal(source, original)
        self.assertEqual(result.loc[2, 'Z_sum'], 5)
        self.assertEqual(result.loc[90, 'Z_mm'], .011)
        self.assertTrue(np.isnan(result.loc[9, 'Z_sum']))
        self.assertTrue(np.isnan(result.loc[20, 'Z_sum']))

    def test_division_by_zero_is_invalid_not_zero(self):
        result = derive_columns(pd.DataFrame(dict(Up=[1., 2.], Down=[0., 2.])), [definition(expression='[Up] / [Down]')])
        self.assertTrue(np.isnan(result.Z_sum.iloc[0]))
        self.assertEqual(result.Z_sum.iloc[1], 1)

    def test_unsafe_expressions_and_bad_dependencies_rejected(self):
        frame = pd.DataFrame(dict(Up=[1], Down=[2]))
        for expression in ('__import__("os").system("echo bad")', '[Up].sum()', '[Missing]+1', '[Up] ** 100000', '[Up][0]'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                derive_columns(frame, [definition(expression=expression)])
        with self.assertRaises(ValueError):
            derive_columns(frame, [definition('Up')])

    def test_cancel_and_byte_bounded_long_line(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(TaskCancelled):
            source_text_page('unused.csv', cancel=cancel)
        with self.assertRaises(TaskCancelled):
            derive_columns(pd.DataFrame(), [], cancel)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'large.csv'
            path.write_bytes(b'1'*(SOURCE_BYTE_LIMIT*3)+b'\n')
            lines, offset, eof = source_text_page(path)
            self.assertEqual(offset, SOURCE_BYTE_LIMIT)
            self.assertFalse(eof)
            self.assertEqual(len(lines[0]), SOURCE_BYTE_LIMIT)

    def test_text_paging_does_not_load_or_count_entire_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'data.csv'
            path.write_text(''.join(f'{i},2,3\n' for i in range(1000)))
            lines, offset, eof = source_text_page(path)
            self.assertEqual(len(lines), 200)
            self.assertFalse(eof)
            following, _, _ = source_text_page(path, offset)
            self.assertEqual(following[0], '200,2,3')

    def test_states_join_by_identity_not_position(self):
        frame = pd.DataFrame(dict(Z=range(5)), index=[10, 20, 40, 80, 90])
        owner = SimpleNamespace(df_raw=frame.loc[[10, 40, 80, 90]],
            manual_mask=np.array([True, False, True, True]), active_idx=np.array([0, 3]), last_metrics={})
        states = analysis_states(owner, frame)
        self.assertEqual(list(states), ['参与计算', '无效 / 未映射', '手动删除', 'ROI / 滤波排除', '参与计算'])


class PreviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.addCleanup(self.window.close)
        self.window.absolute_raw_df = pd.DataFrame(dict(X=[0., 1., 0., 1.], Y=[0., 0., 1., 1.],
            Up=[1., 2., 3., 4.], Down=[10., 11., 12., 13.]))
        self.window.current_source_name = 'test.csv'
        for combo, name in ((self.window.cb_x_col, 'X'), (self.window.cb_y_col, 'Y'), (self.window.cb_z_col, 'Up')):
            combo.clear()
            combo.addItems(list(self.window.absolute_raw_df.columns))
            combo.setCurrentText(name)
        for combo in (self.window.cb_x_unit, self.window.cb_y_unit, self.window.cb_z_unit):
            combo.setCurrentText('mm')
        self.window.input_layout_mode = 'point_table'
        self.window.import_info = {}
        with patch.object(self.window, 'update_analysis'):
            self.assertTrue(self.window.apply_mapping())

    def test_apply_formula_and_recipe_roundtrip(self):
        w = self.window
        definitions = [definition()]
        frame = derive_columns(w.absolute_raw_df, definitions)
        with patch.object(w, 'update_analysis'):
            self.assertTrue(w._commit_derived_preview(frame, definitions, 'Z_sum', 'µm'))
            recipe = w._current_recipe_dict()
            self.assertEqual(recipe['schema_version'], 10)
            validate_recipe(recipe)
            self.assertEqual(recipe['derived_columns'], definitions)
            w.apply_recipe(recipe, show_message=False)
        np.testing.assert_allclose(w.df_raw.Z, [.011, .013, .015, .017])
        self.assertEqual(w.cb_z_col.currentText(), 'Z_sum')
        self.assertIn('Z_sum', w.absolute_raw_df)

    def test_legacy_recipe_default_no_derived(self):
        recipe = self.window._current_recipe_dict()
        recipe.pop('derived_columns')
        with patch.object(self.window, 'update_analysis'):
            self.window.apply_recipe(recipe, show_message=False)
        self.assertEqual(self.window.derived_columns, [])

    def test_matrix_and_zygo_keep_physical_coordinates_and_topology(self):
        w = self.window
        for source in ('matrix', 'Zygo XYZ Data File - Format 1'):
            with self.subTest(source=source):
                w.absolute_raw_df = pd.DataFrame(dict(X=[0., .1, 0., .1], Y=[0., 0., .1, .1],
                    Z=[1., 2., 3., 4.], _matrix_row=[0, 0, 1, 1], _matrix_col=[0, 1, 0, 1]))
                w.derived_columns = []
                w.import_info = dict(source_format=source)
                w.cb_x_col.setCurrentText('X')
                w.cb_y_col.setCurrentText('Y')
                definitions = [definition('Z_corrected', '[Z] + 1')]
                frame = derive_columns(w.absolute_raw_df, definitions)
                with patch.object(w, 'update_analysis'):
                    self.assertTrue(w._commit_derived_preview(frame, definitions, 'Z_corrected', 'µm'))
                np.testing.assert_allclose(w.df_raw.Z, [.002, .003, .004, .005])
                np.testing.assert_allclose(w.df_raw.X, [0, .1, 0, .1])
                self.assertIn('_matrix_row', w.df_raw)
                dialog = DataPreviewDialog(w)
                self.assertTrue(dialog.tabs.tabText(1).startswith('网格位置'))
                self.assertIsNotNone(dialog.grid.model())
                dialog.close()

    def test_failed_formula_does_not_replace_dataset(self):
        w = self.window
        old_raw, old_mapped = w.absolute_raw_df, w.df_raw
        frame = derive_columns(w.absolute_raw_df, [definition(expression='[Up] / 0')])
        with patch.object(QMessageBox, 'warning'):
            self.assertFalse(w._commit_derived_preview(frame, [definition()], 'Z_sum', 'µm'))
        self.assertIs(w.absolute_raw_df, old_raw)
        self.assertIs(w.df_raw, old_mapped)

    def test_up_down_file_without_z_can_be_prepared_before_first_analysis(self):
        w = self.window
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'up_down.csv'
            w.absolute_raw_df.to_csv(path, index=False)
            with patch.object(QMessageBox, 'information'), patch.object(w, 'update_analysis'):
                w.load_path(path)
            self.assertTrue(w.import_info.get('mapping_required'))
            self.assertIsNone(w.df_raw)
            dialog = DataPreviewDialog(w)
            dialog.mapping_controls[0][0].setCurrentText('X')
            dialog.mapping_controls[1][0].setCurrentText('Y')
            for _, units in dialog.mapping_controls:
                units.setCurrentText('mm')
            dialog.insert_column(4)
            identity = dialog.sheet.columns[4]['id']
            dialog.sheet.rename(identity, 'Z_sum')
            dialog.sheet.edit(identity, 0, '=C2+D2', end=None)
            dialog.sheet.column(identity)['unit'] = 'µm'
            dialog.sheet.data['target_z'] = identity
            with patch.object(w, 'update_analysis'):
                dialog.apply_analysis()
                deadline = time.monotonic()+5
                while dialog.thread is not None and time.monotonic()<deadline:
                    self.app.processEvents()
                    time.sleep(.005)
            self.assertIsNone(dialog.thread)
            self.assertEqual(w.cb_z_col.currentText(), 'Z_sum')
            np.testing.assert_allclose(w.df_raw.Z, [.011, .013, .015, .017])
            self.assertNotIn('Z_sum', path.read_text())
            dialog.close()

    def test_mapping_failure_restores_columns_and_original_state(self):
        w = self.window
        old = w.absolute_raw_df
        frame = derive_columns(old, [definition()])
        with patch.object(w, 'apply_mapping', return_value=False):
            self.assertFalse(w._commit_derived_preview(frame, [definition()], 'Z_sum', 'µm'))
        self.assertIs(w.absolute_raw_df, old)
        self.assertEqual(w.cb_z_col.currentText(), 'Up')
        self.assertEqual(w.cb_z_col.findText('Z_sum'), -1)

    def test_source_preview_worker_paging_and_field_highlight(self):
        w = self.window
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.csv'
            w.absolute_raw_df.to_csv(path, index=False)
            w.import_info['source_path'] = str(path)
            dialog = DataPreviewDialog(w)
            dialog.read_source(0)
            deadline = time.monotonic()+5
            while dialog.thread is not None and time.monotonic()<deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(dialog.source_eof)
            self.assertFalse(dialog.raw_next.isEnabled())
            self.assertIn('X,Y,Up,Down', dialog.raw.toPlainText())
            self.assertEqual(dialog.table.model().roles['X'], 'X')
            dialog.close()

    def test_preview_async_formula_does_not_apply_and_close_is_safe(self):
        w = self.window
        old = w.df_raw
        dialog = DataPreviewDialog(w)
        dialog.insert_column(4)
        identity = dialog.sheet.columns[4]['id']
        dialog.change(lambda sheet: sheet.edit(identity, 0, '=C2+D2', end=None), compute=True)
        deadline = time.monotonic()+5
        while dialog.thread is not None and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(dialog.thread)
        self.assertIs(w.df_raw, old)
        self.assertEqual(dialog.sheet.values[identity][0], 11)
        self.assertEqual(len(dialog.table.model().positions), 4)
        dialog.allow_close = True
        dialog.close()


if __name__ == '__main__':
    unittest.main()
