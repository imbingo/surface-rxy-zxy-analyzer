import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import threading
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt, QItemSelection, QItemSelectionModel, QSettings
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QInputDialog

from surface_analyzer.worksheet import Worksheet, letters, ERRORS
from surface_analyzer.worksheet_dialog import DataPreviewDialog
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.workers import TaskCancelled
from surface_analyzer.recipe_library import validate_recipe


def base(size=5):
    return pd.DataFrame(dict(X=np.arange(size,dtype=float),Y=np.arange(size)%2,
                             Up=np.arange(size)+1.,Down=np.arange(size)+10.))


class WorksheetTests(unittest.TestCase):
    def test_relative_absolute_mixed_and_scientific(self):
        sheet=Worksheet(base())
        col=sheet.insert(4,'Z','µm')
        for formula,expected in [('=C2+D2',[11,13,15,17,19]),
                                 ('=$C$2+D2',[11,12,13,14,15]),
                                 ('=C$2+$D2',[11,12,13,14,15]),
                                 ('=C2+1e-3',np.arange(5)+1.001)]:
            with self.subTest(formula=formula):
                sheet.edit(col['id'],0,formula,end=None)
                result=sheet.evaluate()
                np.testing.assert_allclose(result.Z,expected)
        self.assertEqual(letters(26),'AA')

    def test_insert_rename_and_delete_follow_identity(self):
        sheet=Worksheet(base())
        col=sheet.insert(4,'Z','µm')
        sheet.edit(col['id'],0,'=C2+D2',end=None)
        intermediate=sheet.insert(1,'extra','mm')
        self.assertEqual(sheet.formula(col['id'],1),'=(D3+E3)')
        sheet.rename(intermediate['id'],'offset')
        np.testing.assert_allclose(sheet.evaluate().Z,[11,13,15,17,19])
        dep=sheet.insert(6,'copy','µm')
        sheet.edit(dep['id'],0,'=F2',end=None)
        sheet.remove(col['id'])
        self.assertTrue(np.isnan(sheet.evaluate()['copy']).all())
        self.assertTrue((sheet.errors[dep['id']]==4).all())

    def test_fill_override_crossrow_and_cycle(self):
        sheet=Worksheet(base())
        col=sheet.insert(4,'Z','µm')
        sheet.edit(col['id'],0,'=C3')
        sheet.fill(col['id'],0,1,None)
        result=sheet.evaluate()
        np.testing.assert_allclose(result.Z.iloc[:4],[2,3,4,5])
        self.assertEqual(sheet.errors[col['id']][-1],4)
        sheet.edit(col['id'],2,'=1/0')
        sheet.evaluate()
        self.assertEqual(sheet.errors[col['id']][2],3)
        sheet.edit(col['id'],0,'=E2',end=None)
        sheet.evaluate()
        self.assertTrue((sheet.errors[col['id']]==5).all())

    def test_acyclic_self_reference_resolves(self):
        sheet=Worksheet(base(20))
        col=sheet.insert(4,'sequence','mm')
        sheet.edit(col['id'],0,'1')
        sheet.edit(col['id'],1,'=E2+1',end=None)
        np.testing.assert_allclose(sheet.evaluate().sequence,np.arange(1,21))

    def test_blank_bad_values_nonfinite_and_safe_parser(self):
        source=base()
        source.loc[1,'Up']=np.nan
        sheet=Worksheet(source)
        col=sheet.insert(4,'Z','µm')
        sheet.edit(col['id'],0,'=C2/0',end=None)
        sheet.evaluate()
        self.assertEqual(sheet.errors[col['id']][1],2)
        self.assertEqual(sheet.errors[col['id']][0],3)
        for formula in ('=__import__("os")','=C2.__class__','=C2**4','=SUM(C2:C5)'):
            with self.subTest(formula=formula),self.assertRaises(ValueError):
                sheet.parse(formula,0)

    def test_recipe_replays_by_source_name_after_reordering(self):
        sheet=Worksheet(base())
        col=sheet.insert(4,'Z','µm')
        sheet.edit(col['id'],0,'=C2+D2',end=None)
        snapshot=copy.deepcopy(sheet.data)
        result=Worksheet(base()[['Down','X','Y','Up']],snapshot).evaluate()
        np.testing.assert_allclose(result.Z,[11,13,15,17,19])
        with self.assertRaisesRegex(ValueError,'缺少源列'):
            Worksheet(base().drop(columns=['Down']),snapshot)
        self.assertEqual(len(snapshot['rules'][col['id']]),1)

    def test_legacy_and_million_row_fill_and_cancel(self):
        source=base(1_000_000)
        sheet=Worksheet(source,legacy=[dict(name='Z',expression='[Up]+[Down]',unit='µm')])
        col=sheet.columns[-1]
        self.assertEqual(len(sheet.data['rules'][col['id']]),1)
        result=sheet.evaluate()
        self.assertEqual(result.Z.iloc[-1],2_000_009)
        self.assertEqual(source.shape[1],4)
        event=threading.Event();event.set()
        with self.assertRaises(TaskCancelled):
            sheet.evaluate(event)


class WorksheetDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=SurfaceAnalyzerPro()
        self.window.absolute_raw_df=base(250)
        self.window.derived_columns=[]
        self.window.worksheet_data=None
        self.window.input_layout_mode='point_table'
        self.window.import_info={}
        self.window.current_source_name='up_down.csv'
        for cb,name in ((self.window.cb_x_col,'X'),(self.window.cb_y_col,'Y'),(self.window.cb_z_col,'Up')):
            cb.clear();cb.addItems(self.window.absolute_raw_df.columns);cb.setCurrentText(name)
        for cb in (self.window.cb_x_unit,self.window.cb_y_unit,self.window.cb_z_unit):
            cb.setCurrentText('mm')
        with patch.object(self.window,'update_analysis'):
            self.window.apply_mapping()
        self.dialog=DataPreviewDialog(self.window)
        self.dialog.resize(1120,720);self.dialog.show();self.app.processEvents()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.settle()
        self.dialog.allow_close=True;self.dialog.close();self.window.close()

    def settle(self):
        deadline=time.monotonic()+8
        while self.dialog.thread is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.003)
        self.assertIsNone(self.dialog.thread)
        self.app.processEvents()

    def prepare(self):
        d=self.dialog
        d.insert_column(4)
        identity=d.sheet.columns[4]['id']
        d.sheet.rename(identity,'Z_sum')
        d.refresh()
        return identity

    def test_point_select_formula_enter_escape_and_reference_colors(self):
        d=self.dialog;identity=self.prepare()
        target=d.table.model().index(0,4)
        d.table.setCurrentIndex(target)
        d.begin_edit(target,'=')
        d.insert_reference(d.table.model().index(0,2))
        d.formula.insertPlainText('+')
        d.insert_reference(d.table.model().index(0,3))
        self.assertEqual(d.formula.text(),'=C2+D2')
        self.assertEqual(d.table.model().references,[(0,2),(0,3)])
        QTest.keyClick(d.formula,Qt.Key.Key_Return);self.settle()
        self.assertEqual(d.sheet.values[identity][0],11)

    def test_reference_selection_across_pages_preserves_editing_target(self):
        d=self.dialog;identity=self.prepare()
        target=d.table.model().index(0,4)
        d.begin_edit(target,'=')
        d.turn(1)
        self.assertEqual(d.editing,(identity,0))
        d.insert_reference(d.table.model().index(0,2))
        self.assertEqual(d.formula.text(),'=C202')
        d.submit_formula();self.settle()
        self.assertEqual(d.sheet.values[identity][0],201)
        d.begin_edit(d.table.model().index(0,4),'=900')
        QTest.keyClick(d.formula,Qt.Key.Key_Escape)
        self.assertEqual(d.sheet.values[identity][0],201)

    def test_fill_all_across_pages_undo_redo_and_global_row_labels(self):
        d=self.dialog;identity=self.prepare()
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2'),True);self.settle()
        d.fill_all(d.table.model().index(0,4));self.settle()
        self.assertEqual(d.sheet.values[identity][-1],509)
        self.assertEqual(len(d.sheet.data['rules'][identity]),2)
        d.turn(1)
        self.assertEqual(d.table.model().headerData(0,Qt.Orientation.Vertical), '202')
        d.undo();self.settle()
        self.assertTrue(np.isnan(d.sheet.values[identity][-1]))
        d.redo();self.settle()
        self.assertEqual(d.sheet.values[identity][-1],509)

    def test_fill_handle_drag_double_click_and_ctrl_d(self):
        d=self.dialog;identity=self.prepare()
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2'),True);self.settle()
        d.table.setCurrentIndex(d.table.model().index(0,4));self.app.processEvents()
        handle=d.table.handle_rect().center()
        end=d.table.visualRect(d.table.model().index(2,4)).center()
        QTest.mousePress(d.table.viewport(),Qt.MouseButton.LeftButton,pos=handle)
        QTest.mouseRelease(d.table.viewport(),Qt.MouseButton.LeftButton,pos=end)
        self.settle()
        self.assertEqual(d.sheet.values[identity][2],15)
        d.table.setCurrentIndex(d.table.model().index(0,4));self.app.processEvents()
        QTest.mouseDClick(d.table.viewport(),Qt.MouseButton.LeftButton,pos=d.table.handle_rect().center())
        self.settle()
        self.assertEqual(d.sheet.values[identity][-1],509)

    def test_ctrl_d_multiple_columns_is_one_undo_command(self):
        d=self.dialog;identity=self.prepare()
        d.insert_column(5)
        other=d.sheet.columns[5]['id']
        def edit(sheet):
            sheet.edit(identity,0,'=C2+D2')
            sheet.edit(other,0,'=C2-D2')
        d.change(edit,True);self.settle()
        selection=QItemSelection(d.table.model().index(0,4),d.table.model().index(2,5))
        d.table.selectionModel().select(selection,QItemSelectionModel.SelectionFlag.ClearAndSelect)
        before=len(d.history)
        QTest.keyClick(d.table,Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier);self.settle()
        self.assertEqual(len(d.history),before+1)
        self.assertEqual(d.sheet.values[identity][2],15)
        self.assertEqual(d.sheet.values[other][2],-9)
        d.undo();self.settle()
        self.assertTrue(np.isnan(d.sheet.values[other][2]))

    def test_filtered_fill_targets_only_visible_rows_and_keeps_addresses(self):
        d=self.dialog;identity=self.prepare()
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2'),True);self.settle()
        d.states[:]='ROI / 滤波排除'
        d.states.iloc[::2]='参与计算'
        d.scope.setCurrentIndex(1)
        self.assertEqual(list(d.table.model().positions[:3]),[0,2,4])
        d.fill_rows(4,0,[1,2]);self.settle()
        self.assertEqual(d.sheet.values[identity][4],19)
        self.assertTrue(np.isnan(d.sheet.values[identity][1]))
        self.assertEqual(d.table.model().headerData(1,Qt.Orientation.Vertical),'4')

    def test_undo_insert_and_redo_preserve_column_identity(self):
        d=self.dialog;identity=self.prepare()
        d.table.setCurrentIndex(d.table.model().index(0,4))
        d.undo();self.settle()
        self.assertEqual(len(d.sheet.columns),4)
        d.redo();self.settle()
        self.assertEqual(d.sheet.columns[4]['id'],identity)

    def test_cancel_draft_computation_keeps_analysis_and_saved_sheet(self):
        d=self.dialog;identity=self.prepare()
        old=copy.deepcopy(d.sheet.data)
        original=self.window.df_raw
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2',end=None),True)
        d.cancel_task();self.settle()
        self.assertEqual(d.sheet.data,old)
        self.assertIs(self.window.df_raw,original)

    def test_apply_schema11_provenance_and_no_pitch_on_xyz(self):
        d=self.dialog;identity=self.prepare()
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2',end=None),True);self.settle()
        with patch.object(QInputDialog,'getItem',return_value=('µm',True)):
            d.set_target(identity)
        with patch.object(self.window,'update_analysis'):
            d.apply_analysis();self.settle()
        self.assertFalse(d.dirty)
        self.assertAlmostEqual(self.window.df_raw.Z.iloc[0],.011)
        recipe=self.window._current_recipe_dict()
        self.assertEqual(recipe['schema_version'],11)
        validate_recipe(recipe)
        with patch.object(self.window,'update_analysis'):
            self.window.apply_recipe(recipe,show_message=False)
        self.assertAlmostEqual(self.window.df_raw.Z.iloc[-1],.509)
        self.assertNotIn('解析器',d.summary.text())
        self.assertNotIn('Pitch',d.summary.text())

    def test_schema11_reload_new_file_and_batch_formula_replay(self):
        d=self.dialog;identity=self.prepare()
        d.change(lambda sheet:sheet.edit(identity,0,'=C2+D2',end=None),True);self.settle()
        with patch.object(QInputDialog,'getItem',return_value=('µm',True)):
            d.set_target(identity)
        with patch.object(self.window,'update_analysis'):
            d.apply_analysis();self.settle()
        recipe=self.window._current_recipe_dict()
        fresh=SurfaceAnalyzerPro()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'input.csv';base().to_csv(path,index=False)
                fresh.apply_recipe(recipe,show_message=False)
                with patch.object(QMessageBox,'information'),patch.object(fresh,'update_analysis'):
                    fresh.load_path(path)
                self.assertAlmostEqual(fresh.df_raw.Z.iloc[-1],.019)
                params=fresh._capture_batch_params()
                missing=Path(directory)/'missing.csv'
                base().drop(columns=['Down']).to_csv(missing,index=False)
                from matplotlib.figure import Figure
                with patch.object(fresh,'_render_report_figure',return_value=Figure()) as render:
                    result=fresh._run_batch([str(path),str(missing)],directory,params)
                self.assertEqual([r['status'] for r in result['results']],['ok','fail'])
                np.testing.assert_allclose(render.call_args.args[3],[.011,.013,.015,.017,.019])
                self.assertIn('derived_columns',render.call_args.args[9])
                self.assertTrue(Path(result['summary_path']).is_file())
        finally:
            fresh.close()

    def test_close_continue_editing_and_discard_do_not_commit(self):
        from PyQt6.QtWidgets import QMessageBox
        d=self.dialog;self.prepare()
        old=self.window.df_raw
        def choose(message):
            button=next(b for b in message.buttons() if b.text()=='继续编辑')
            button.click()
            return 0
        with patch.object(QMessageBox,'exec',choose):
            d.reject()
        self.assertTrue(d.isVisible())
        self.assertIs(self.window.df_raw,old)

    def test_recipe_cancel_precedes_any_workspace_mutation(self):
        recipe=self.window._current_recipe_dict()
        before=self.window.df_raw
        with patch.object(self.window,'_prepare_derived_frame',side_effect=TaskCancelled),patch.object(self.window,'clear_parallel_surfaces') as clear:
            with self.assertRaises(TaskCancelled):
                self.window.apply_recipe(recipe,show_message=False)
        clear.assert_not_called()
        self.assertIs(self.window.df_raw,before)


class PitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_auto_requires_both_axes_and_manual_preserves(self):
        w=SurfaceAnalyzerPro()
        try:
            w.pitch_source='auto'
            for fields in ([],['Pitch X'],['Pitch Y']):
                with self.subTest(fields=fields),self.assertRaisesRegex(ValueError,'未识别到可靠'):
                    w._resolve_matrix_pitch(dict(detected_fields=fields,pitch_x_um=1,pitch_y_um=2))
            self.assertEqual(w._resolve_matrix_pitch(dict(detected_fields=['Pitch X','Pitch Y'],pitch_x_um=1,pitch_y_um=2)),(1.,2.,'可靠文件metadata'))
            w.pitch_source='manual';w.height_matrix_pitch_x_um=24.2;w.height_matrix_pitch_y_um=24.3
            self.assertEqual(w._resolve_matrix_pitch({}),(24.2,24.3,'用户手动'))
        finally:
            w.close()

    def test_new_configuration_defaults_auto_even_with_saved_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat)
            settings.setValue('input_layout_mode','height_matrix')
            with patch('surface_analyzer.app.QSettings',return_value=settings):
                w=SurfaceAnalyzerPro()
            try:
                self.assertEqual(w.pitch_source,'auto')
            finally:
                w.close()

    def test_actual_text_and_excel_matrix_auto_and_xyz_metadata(self):
        w=SurfaceAnalyzerPro()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                w.input_layout_mode='height_matrix';w.pitch_source='auto'
                values=np.sin(np.arange(16)).reshape(4,4)
                data='\n'.join(','.join(map(str,row)) for row in values)+'\n'
                path=root/'matrix.csv'
                path.write_text('X Pitch (um),24.2\nY Pitch (um),24.3\n'+data)
                frame=w._read_table(path)
                self.assertAlmostEqual(frame.X.max(),.0726)
                self.assertAlmostEqual(frame.Y.max(),.0729)
                excel=root/'matrix.xlsx'
                rows=[['X Pitch (um)',24.2],['Y Pitch (um)',24.3]]+values.tolist()
                pd.DataFrame(rows).to_excel(excel,index=False,header=False)
                result=w._read_table(excel)
                self.assertAlmostEqual(result.X.max(),.0726)
                path.write_text('X Pitch (um),24.2\n'+data)
                with self.assertRaisesRegex(ValueError,'Y Pitch'):
                    w._read_table(path)
                w.input_layout_mode='point_table'
                path.write_text('X,Y,Z\n0,0,1\n1,0,2\n0,1,3\n')
                w._read_table(path)
                self.assertIsNone(w.import_info['sampling_pitch_x_um'])
                self.assertIsNone(w.import_info['sampling_pitch_y_um'])
        finally:
            w.close()


if __name__=='__main__':
    unittest.main()
