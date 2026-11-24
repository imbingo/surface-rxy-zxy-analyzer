"""V5.0.7 directed acceptance; run with a suite-level timeout."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from copy import deepcopy
from itertools import product
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings, QMimeData, QUrl, QPoint, Qt
from PyQt6.QtGui import QDragEnterEvent, QPalette
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.composite_recipe import build_composite, required_roles, validate_composite, validate_group, scan_groups
from surface_analyzer.composite_dialog import CompositeRunDialog, CompositeSaveDialog
from surface_analyzer.composite_runner import execute_group, process_group, load_snapshot, run_groups
from surface_analyzer.workers import TaskCancelled
from surface_analyzer.display_metrics import display_metrics, judge_single, normalize_single_config
from surface_analyzer.judgment import JudgmentSpecDialog
from surface_analyzer.polynomial import fit_polynomial_surface, evaluate_polynomial_surface

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, LOCALAPPDATA=str(self.root)); self.env.start()
        self.settings = QSettings(str(self.root/'ui.ini'), QSettings.Format.IniFormat)
        self.prepare_extensions()
        self.w = SurfaceAnalyzerPro(settings=self.settings)
        self.w.set_theme_preference('light', persist=False)
        self.w._workflow_notice = lambda level,title,text: (_ for _ in ()).throw(ValueError(text)) if level == 'critical' else None
        self.w._confirm_estimated_metrics = lambda action: True
        self.w.import_x_unit = self.w.import_y_unit = self.w.import_z_unit = 'mm'
        self.w.auto_sample_large_text = False
        self.x, self.y = np.meshgrid(np.linspace(-3,3,4), np.linspace(-2,2,3))
        self.x, self.y = self.x.ravel(), self.y.ravel()

    def prepare_extensions(self): pass

    def tearDown(self):
        self.w.close(); self.w.deleteLater(); self.qt.processEvents()
        self.env.stop(); self.tmp.cleanup()

    def file(self, name, z):
        path = self.root/name
        pd.DataFrame(dict(X=self.x, Y=self.y, Z=np.broadcast_to(z,self.x.shape))).to_csv(path,index=False)
        return str(path)

    def load(self, name, z):
        path = self.file(name,z)
        self.assertTrue(self.w.load_path(path)); self.assertIsNotNone(self.w.last_metrics)
        return path


class MainAcceptance(Base):
    def prepare_extensions(self):
        shutil.copytree(ROOT/'extensions/sensofar-plux', self.root/'SurfaceRxyZxyAnalyzer/format_extensions/sensofar-plux')

    def test_plux_drop_recent_restart_disable_and_failure(self):
        w = self.w; path = self.root/'matrix.PLUX'
        xml = '<xml><GENERAL><IMAGE_SIZE_X>6</IMAGE_SIZE_X><IMAGE_SIZE_Y>5</IMAGE_SIZE_Y><FOV_X>2</FOV_X><FOV_Y>3</FOV_Y></GENERAL><LAYER_0><FILENAME_Z>LAYER_0.raw</FILENAME_Z></LAYER_0></xml>'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('index.xml',xml); z.writestr('LAYER_0.raw',np.arange(30,dtype='<f4').tobytes())
        mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = QDragEnterEvent(QPoint(30,30), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.dragEnterEvent(event); self.assertTrue(event.isAccepted())
        self.assertTrue(w.load_path(path)); self.assertEqual(w.recent_files[0],str(path.resolve()))
        self.assertTrue(any(a.toolTip()==str(path.resolve()) for a in w.recent_files_menu.actions()))
        self.assertEqual(self.settings.value('recent_files',[],type=list)[0],str(path.resolve()))
        w.load_path(path); self.assertEqual(w.recent_files.count(str(path.resolve())),1)
        restarted=SurfaceAnalyzerPro(settings=self.settings)
        try:self.assertEqual(restarted.recent_files[0],str(path.resolve()))
        finally:restarted.close();restarted.deleteLater()
        extension = self.root/'SurfaceRxyZxyAnalyzer/format_extensions/sensofar-plux'
        (extension/'disabled').touch(); self.assertFalse(w._is_supported_drop_file(path))
        (extension/'disabled').unlink()
        manifest=json.loads((extension/'extension.json').read_text()); manifest['maximum_app_version']='5.0.6'
        (extension/'extension.json').write_text(json.dumps(manifest)); self.assertFalse(w._is_supported_drop_file(path))
        manifest.pop('maximum_app_version');(extension/'extension.json').write_text(json.dumps(manifest))
        bad=self.root/'bad.plux';bad.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'zip file'):w.load_path(bad)
        self.assertNotIn(str(bad),w.recent_files)

    def test_startup_defaults_override_saved_frame_and_specs(self):
        self.settings.setValue('use_system_frame',True)
        self.settings.setValue('judgment/spec',json.dumps({'rx':1.}))
        other=SurfaceAnalyzerPro(settings=self.settings)
        try:
            self.assertFalse(other.use_system_frame)
            self.assertTrue(other.windowFlags() & Qt.WindowType.FramelessWindowHint)
            config=other._single_metric_config();self.assertEqual(len(config['visible']),6)
            self.assertTrue(all(v is None for v in config['limits'].values()))
            self.assertEqual(other.current_judgment('single')['verdict'],'unset')
            self.assertTrue(all(l.text()=='--' for l in (other.lbl_z,other.lbl_pv,other.lbl_std3)))
            other._set_system_frame_enabled(True); self.assertTrue(other.use_system_frame)
            other._set_system_frame_enabled(False); self.assertFalse(other.use_system_frame)
        finally:other.close();other.deleteLater()

    def test_all_display_metrics_and_acceptance_follow_selected_face(self):
        w=self.w
        self.x,self.y=np.meshgrid(np.linspace(-3,3,5),np.linspace(-2,2,5))
        self.x,self.y=self.x.ravel(),self.y.ravel()
        z=.4+.012*self.x-.007*self.y+.0002*self.x**2+.00004*self.y**3
        self.load('curved.csv',z)
        for mode in ('raw','residual_1','residual_2','residual_3'):
            order=int(mode[-1]) if mode!='raw' else None
            if order:w.high_order_models[order]=fit_polynomial_surface(*w._high_order_inputs,order)
            w.cb_surface_display.setCurrentIndex(w.cb_surface_display.findData(mode))
            xyz=w._high_order_inputs
            selected=xyz[2] if mode=='raw' else xyz[2]-evaluate_polynomial_surface(w.high_order_models[order],xyz[0],xyz[1])
            values=w._single_display_metrics
            self.assertAlmostEqual(values['std3'],3*np.std(selected,ddof=0)*1000,places=9)
            self.assertAlmostEqual(values['ttv'],np.ptp(selected)*1000,places=9)
            self.assertAlmostEqual(values['mean_z'],np.mean(selected),places=12)
            if order:self.assertAlmostEqual(values['pv'],np.ptp(selected)*1000,places=9)
            self.assertAlmostEqual(float(w.lbl_std3.text()),values['std3'],delta=max(1e-9,abs(values['std3'])*1e-5))
            config=normalize_single_config({'visible':['pv','std3'],'limits':{'std3':values['std3']+1,'rx':0}})
            w.set_single_metric_config(config);self.assertEqual(w.current_judgment('single')['verdict'],'pass')
        self.assertFalse(w.lbl_surface_residual_metrics.isVisible())
        spec=w._current_recipe_dict();self.assertEqual(spec['single_metrics'],w._single_metric_config())
        w.set_single_metric_config(normalize_single_config())
        w.apply_recipe(spec,show_message=False)
        self.assertEqual(w._single_metric_config(),spec['single_metrics'])

    def test_blank_zero_hidden_limits_and_dialog(self):
        w=self.w;config=normalize_single_config({'visible':['pv','std3'],'limits':{'pv':None,'std3':0,'rx':0}})
        self.assertEqual(judge_single({'std3':0,'rx':500},config)['verdict'],'pass')
        self.assertEqual(judge_single({'std3':.1},config)['verdict'],'fail')
        self.assertEqual(judge_single({},config)['verdict'],'pending')
        config['limits']['std3']=None;self.assertEqual(judge_single({},config)['verdict'],'unset')
        dialog=JudgmentSpecDialog(w,{},config)
        try:
            row=dialog._single_rows['std3'];self.assertTrue(row[0].isChecked());self.assertEqual(row[1].text(),'')
            row[1].setText('0');self.assertEqual(dialog.single_config()['limits']['std3'],0.)
            row[0].setChecked(False);self.assertIsNone(dialog.single_config()['limits']['std3'])
        finally:dialog.close()
        w.set_single_metric_config(config);w.resize(1500,900);w.show();self.qt.processEvents()
        visible=[c for c in w._metric_cards if not c.isHidden()]
        self.assertEqual(len(visible),3)
        self.assertTrue(all(c.width()>100 for c in visible))

    def test_gap_snapshot_views_and_five_graph_report_survive_new_single_data(self):
        from surface_analyzer.mixins.gap import GapAnalysisMixin
        def record(name,z):return dict(x=self.x.copy(),y=self.y.copy(),z=np.broadcast_to(z,self.x.shape).copy(),name=name,n=len(self.x),offset_x=0.,offset_y=0.)
        stack=record('总成_'+('long_name_'*20)+'.csv',.8+.001*self.x)
        base=record('base.csv',.4+.0001*self.y)
        payload=GapAnalysisMixin._compute_gap_payload(stack,base,None,.01,lambda *a:None,threading.Event())
        self.w.gap_result=payload;self.w._sync_gap_action_state()
        self.w.data_stack=record('UNRELATED',1000.);self.w.data_base1=None
        self.load('different.csv',5.+self.x*.001)
        self.w.gap_result=payload
        fig=self.w._render_gap_report_figure();fig.canvas.draw()
        views=[a for a in fig.axes if (a.get_gid() or '').startswith('result-')]
        self.assertEqual(len(views),4)
        self.assertTrue(any(a.get_title()=='XY 配准与容差匹配' for a in fig.axes))
        xy=next(a for a in views if a.get_gid()=='result-xy')
        np.testing.assert_allclose(xy.collections[0].get_array(),payload['z'])
        text='\n'.join(t.get_text() for a in fig.axes for t in a.texts)
        self.assertNotIn('UNRELATED',text);self.assertIn('匹配率',text)
        fig.clear()


class VigAcceptance(Base):
    def prepare_extensions(self):
        shutil.copytree(ROOT/'extensions/vig-image',self.root/'SurfaceRxyZxyAnalyzer/module_extensions/vig-image')

    def vig_setup(self):
        self.module=self.w._vig_composite_module()
        self.up=40+2*self.x;self.low=170+3*self.y
        self.files={}
        self.files['two']=self.load('两层.csv',(self.low+230)/1000);self.module.write_slot('two')
        self.single=deepcopy(self.module.slots['two']['single_recipe'])
        self.files['three']=self.load('三层.csv',(self.up+self.low+350)/1000);self.module.write_slot('three')
        for key,value in [('up',120.),('mid',150.),('low',80.)]:self.module.thickness_spins[key].setValue(value)

    def test_eight_electrode_modes_use_same_recipe_and_confirmation_table(self):
        self.vig_setup()
        for modes in product(('fixed','file'),repeat=3):
            for key,mode in zip(('up','mid','low'),modes):
                self.module.slots[key]=dict(single_recipe=deepcopy(self.single),name=key,n=len(self.x)) if mode=='file' else None
            recipe=self.w._compose_recipe('vig','VIG modes')
            expected=('two','three')+tuple(k for k,m in zip(('up','mid','low'),modes) if m=='file')
            self.assertEqual(required_roles(recipe),expected)
            self.assertEqual(set(recipe['roles']),set(expected));self.assertEqual(recipe['schema_version'],2)
            dialog=CompositeRunDialog(self.w,recipe)
            self.assertEqual(dialog.table.columnCount(),len(expected)+3);dialog.close()
            for role in expected[2:]:
                g=dict(files={r:self.files.get(r,'missing.csv') for r in expected})
                self.assertTrue(validate_group(g,recipe))
                self.assertEqual(recipe['electrodes'][role]['mode'],'file')
        self.module.clear_slot('up');self.module.clear_slot('mid');self.module.clear_slot('low')
        self.module.thickness_spins['up'].setValue(135.)
        self.assertIn('135',self.module.slot_labels['up'].text())
        recipe=self.w._compose_recipe('vig','stable')
        old=deepcopy(recipe['roles']['two'])
        self.w.add_rot180();self.assertEqual(self.w._compose_recipe('vig','again')['roles']['two'],old)
        dialog=CompositeSaveDialog(self.w,'vig');dialog.name.setText('Save');self.assertIsNotNone(dialog.recipe);dialog.close()

    def test_vig_image_copy_isolated_units_sign_and_no_trace_reexecution(self):
        self.vig_setup();recipe=self.w._compose_recipe('vig','manual')
        result=self.module.compute_snapshot(self.module.slots,recipe)
        self.module._apply_vig_payload(result);original=result['image'].copy()
        with patch.object(self.module,'compute_snapshot',side_effect=AssertionError('must not retrace')):
            self.assertTrue(self.module.load_secondary())
            np.testing.assert_allclose(self.w.df_raw.Z,original*.001)
            self.assertFalse(np.shares_memory(self.w.df_raw.Z.to_numpy(),result['image']))
            self.w.manual_mask[0]=False;self.w.update_analysis()
            self.assertEqual(len(self.w.active_idx),len(original)-1)
            self.w.cb_filter.setCurrentIndex(4);self.w.update_analysis()
            np.testing.assert_array_equal(result['image'],original)
        self.assertIsNone(self.w._slot_single_recipe_snapshot())
        self.assertTrue(self.w.import_info['memory_result'])
        self.assertEqual(self.w.tabs.currentIndex(),0)
        fig=self.module._render_vig_report_figure();fig.canvas.draw()
        views=[a for a in fig.axes if (a.get_gid() or '').startswith('result-')]
        self.assertEqual(len(views),4)
        np.testing.assert_allclose(views[1].collections[0].get_array(),original)
        fig.clear()

    def test_real_vig_composite_child_and_file_electrode_provenance(self):
        self.vig_setup()
        path=self.load('上电极.csv',.120);self.w.add_rot180();self.module.write_slot('up');self.files['up']=path
        self.w._set_export_origin_zero_active_points(True)
        recipe=self.w._compose_recipe('vig','child',export={'csv':True,'report':True})
        self.w._set_export_origin_zero_active_points(False)
        self.assertTrue(recipe['export']['zero_origin_active_points'])
        self.assertEqual(recipe['roles']['two']['transform_pipeline'],[])
        self.assertEqual(recipe['roles']['up']['transform_pipeline'],['ROT180'])
        output=self.root/'child';output.mkdir()
        result=process_group(recipe,{r:self.files[r] for r in required_roles(recipe)},output,lambda *a:None,threading.Event(),timeout=90)
        self.assertEqual(result['status'],'success');self.assertEqual(set(result['inputs']),{'two','three','up'})
        for role in required_roles(recipe):self.assertEqual(result['inputs'][role]['single_recipe'],recipe['roles'][role])
        self.assertTrue((output/'result.csv').is_file());self.assertTrue((output/'report.png').is_file())
        exported=pd.read_csv(output/'result.csv',comment='#')
        self.assertAlmostEqual(exported.X_mm.min(),0.);self.assertAlmostEqual(exported.Y_mm.min(),0.)
        saved=load_snapshot(output);self.assertEqual(saved['module'],'vig')
        manual=self.module.compute_snapshot(self.module.slots,recipe)
        np.testing.assert_allclose(saved['result']['image'],manual['image'])
        bad=deepcopy(recipe);bad['electrodes']['up']['value_um']=120.
        with self.assertRaises(ValueError):validate_composite(bad)

    def test_vig_batch_failure_continue_missing_roles_cancel_and_timeout(self):
        self.vig_setup()
        recipe=self.w._compose_recipe('vig','batch',export={'csv':False,'report':False})
        batch=self.root/'batch';batch.mkdir()
        for name in ('01-failed','02-good','03-missing'):
            folder=batch/name;folder.mkdir()
            for role,label in (('two','两层'),('three','三层')):
                if name=='03-missing' and role=='three':continue
                shutil.copyfile(self.files[role],folder/(label+'.csv'))
        (batch/'01-failed/两层.csv').write_text('X,Y,Z\n1,2,not-a-number\n')
        groups=scan_groups(batch,recipe)
        self.assertEqual(len(groups),3)
        payload=run_groups(recipe,groups,self.root/'batch-output',timeout=90)
        rows=payload['results']
        self.assertEqual([r['status'] for r in rows],['failed','success','skipped'])
        self.assertEqual(rows[0]['metrics'],{});self.assertEqual(rows[2]['metrics'],{})
        self.assertTrue(payload['snapshot']);self.assertEqual(payload['snapshot']['module'],'vig')
        for name,event,limit,error in [('cancel',threading.Event(),90,TaskCancelled),('timeout',threading.Event(),.01,TimeoutError)]:
            folder=self.root/name;folder.mkdir()
            if name=='cancel':event.set()
            with self.assertRaises(error):
                process_group(recipe,self.files,folder,lambda *a:None,event,timeout=limit)
            self.assertFalse((folder/'snapshot.json').exists())

    def test_partial_ray_failure_is_in_ui_csv_and_report(self):
        import sys
        self.vig_setup();recipe=self.w._compose_recipe('vig','errors')
        engine=sys.modules['_surface_module_vig-image'].engine
        original=engine.calculate_zimg2low_from_df
        def failure(frame,**kwargs):
            result=original(frame,**kwargs);result.loc[0,'image']=np.nan
            kwargs['on_error'](0,'injected ray failure');return result
        with patch.object(engine,'calculate_zimg2low_from_df',side_effect=failure):
            payload=self.module.compute_snapshot(self.module.slots,recipe)
        self.assertEqual(payload['n_errors'],1);self.assertEqual(payload['n_points'],11)
        self.module._apply_vig_payload(payload)
        self.assertIn('计算失败 1 点',self.module.hint_label.text())
        self.assertIn('injected ray failure','\n'.join(self.module._export_metadata()))
        self.module.write_csv(self.root/'failed.csv')
        self.assertIn('失败点数: 1',(self.root/'failed.csv').read_text(encoding='utf-8-sig'))
        fig=self.module._render_vig_report_figure();fig.canvas.draw()
        self.assertIn('injected ray failure','\n'.join(t.get_text() for a in fig.axes for t in a.texts));fig.clear()


if __name__=='__main__':unittest.main()
