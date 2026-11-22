"""Targeted composite workflow checks; no unbounded full-suite invocation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication,QDialog,QMessageBox
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.composite_recipe import (build_composite,validate_composite,required_roles,
    validate_group,scan_groups,COMPOSITE_TYPE)
from surface_analyzer.composite_runner import (execute_group,run_groups,process_group,
    save_snapshot,load_snapshot)
from surface_analyzer.recipe_library import RecipeLibrary,read_recipe,validate_recipe,atomic_json
from surface_analyzer.workers import TaskCancelled


class CompositeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.windows=[];self.w=self.window()
        self.single=self.w._current_recipe_dict()
        self.single['column_mapping']=dict(x_col='X',y_col='Y',z_col='Z')
        self.single['units']=dict(x_unit='mm',y_unit='mm',z_unit='mm')
        self.single['input'].update(encoding='utf-8-sig',pitch_source='auto')
        self.single['large_file']['auto_sample']=False
        self.single['roi']=dict(enabled=False,shapes=[])
        self.single['manual_deletion']['operations']=[]
        self.x,self.y=np.meshgrid(np.arange(6.),np.arange(5.));self.x=self.x.ravel();self.y=self.y.ravel()
        self.files={}
        for role,z in [('stack',2.),('base1',.4),('base2',.2),('base',1.),('measure',1.7)]:
            path=self.root/(role+'.csv')
            pd.DataFrame(dict(X=self.x,Y=self.y,Z=z+.001*self.x-.002*self.y+.00001*self.x*self.y)).to_csv(path,index=False)
            self.files[role]=str(path)

    def window(self):
        settings=QSettings(str(self.root/f'settings{len(getattr(self,"windows",[]))}.ini'),QSettings.Format.IniFormat)
        w=SurfaceAnalyzerPro(settings=settings);self.windows.append(w);self.addCleanup(w.close);return w

    def recipe(self,module='thickness',base2=False,mode='none'):
        roles=('base','measure') if module=='parallelism' else (('stack','base1','base2') if base2 else ('stack','base1'))
        records={r:dict(single_recipe=deepcopy(self.single)) for r in roles}
        reg={} if module=='parallelism' else dict(mode=mode,tolerance_mm=.05,offsets={r:[0.,0.] for r in roles[1:]})
        return build_composite(module,records,'Test composite',include_base2=base2,registration=reg,
                               export=dict(csv=True,report=False),app_version=self.w.APP_VERSION)

    def group(self,recipe,name='A',files=None):
        return dict(sample_id=name,selected=True,files=files or {r:self.files[r] for r in required_roles(recipe)})

    def manual_record(self,path,single=None,gap=False):
        w=self.window();w._automation_mode=True
        with patch.object(QMessageBox,'information',return_value=QMessageBox.StandardButton.Ok):
            w.apply_recipe(deepcopy(single or self.single),show_message=False)
            self.assertTrue(w.load_path(path))
        self.assertIsNotNone(w.last_metrics)
        return w._current_gap_record() if gap else w._current_parallel_record()

    def test_independent_slot_snapshots_are_deep_and_swapping_keeps_identity(self):
        w=self.w;w._automation_mode=True
        with patch.object(QMessageBox,'information',return_value=QMessageBox.StandardButton.Ok):
            w.apply_recipe(self.single,show_message=False);self.assertTrue(w.load_path(self.files['base']))
        w.set_parallel_surface('base');first=deepcopy(w.parallel_base['single_recipe'])
        w.transform_pipeline=['ROT180'];w.update_analysis();w.set_parallel_surface('measure')
        w.roi_shapes.append(dict(type='rect',view='XY',cx=0,cy=0,width=2,height=2))
        self.assertEqual(w.parallel_base['single_recipe'],first)
        self.assertEqual(w.parallel_measure['single_recipe']['transform_pipeline'],['ROT180'])
        w.swap_parallel_surfaces();self.assertEqual(w.parallel_measure['single_recipe'],first)
        self.assertTrue(w._composite_save_buttons['parallelism'].isEnabled())

    def test_old_slots_cannot_be_silently_filled_from_current_ui(self):
        self.w.parallel_base={'name':'old'};self.w.parallel_measure={'name':'old'}
        self.w._refresh_composite_save_buttons()
        self.assertFalse(self.w._composite_save_buttons['parallelism'].isEnabled())
        with self.assertRaisesRegex(ValueError,'重新写入'):self.w._compose_recipe('parallelism','bad')

    def test_library_roundtrip_keeps_types_and_old_single_recipe(self):
        lib=RecipeLibrary(self.root/'library');a=lib.save(self.single,'single')
        for module in ('thickness','parallelism'):
            data=self.recipe(module);p=lib.save(data,module);loaded=read_recipe(p)
            self.assertEqual(loaded['roles'],data['roles']);self.assertEqual(loaded['schema_version'],1)
        self.assertEqual(read_recipe(a)['schema_version'],9)
        entries,errors=lib.entries();self.assertFalse(errors)
        self.assertEqual({e['kind'] for e in entries},{'单层 Recipe','膜厚组合 Recipe','平行度组合 Recipe'})

    def test_unknown_versions_invalid_settings_validate_before_workspace_change(self):
        for mutate in (lambda r:r.update(schema_version=99),lambda r:r['roles'].pop('base1'),
                       lambda r:r['registration'].update(tolerance_mm=-1),
                       lambda r:r['roles']['base1']['filter'].update(mode_index=99)):
            recipe=self.recipe();mutate(recipe);sentinel=object();self.w.parallel_result=sentinel
            with self.assertRaises(ValueError):self.w.open_composite_run(recipe)
            self.assertIs(self.w.parallel_result,sentinel)

    def test_recipe_contains_no_measurement_points_or_required_historical_paths(self):
        recipe=self.recipe(base2=True);text=json.dumps(recipe)
        self.assertNotIn(str(self.root),text);self.assertNotIn('point_records',text)
        self.assertEqual(set(recipe['roles']),{'stack','base1','base2'})

    def test_missing_ambiguous_conflict_and_case_insensitive_roles(self):
        recipe=self.recipe();root=self.root/'batch';folder=root/'A001';folder.mkdir(parents=True)
        recipe['matching']['keywords']={'stack':'STACK','base1':'base1'}
        (folder/'a_stack.csv').write_text('x');(folder/'a_base1.CSV').write_text('x')
        (folder/'ignore.csv').write_text('x');(folder/'a_stack.png').write_text('x')
        nested=folder/'nested';nested.mkdir();(nested/'other_stack.csv').write_text('x')
        groups=scan_groups(root,recipe);self.assertEqual(len(groups),1);self.assertFalse(groups[0]['errors'])
        (folder/'b_stack.csv').write_text('x');groups=scan_groups(root,recipe)
        self.assertTrue(any('歧义' in e for e in groups[0]['errors']))
        (folder/'a_base1.CSV').unlink();groups=scan_groups(root,recipe)
        self.assertTrue(any('缺失' in e for e in groups[0]['errors']))
        groups[0]['files']={r:[str(folder/'a_stack.csv')] for r in required_roles(recipe)}
        self.assertTrue(any('冲突' in e for e in validate_group(groups[0],recipe)))

    def test_required_base2_does_not_downgrade(self):
        recipe=self.recipe(base2=True);files={k:v for k,v in self.files.items() if k in ('stack','base1')}
        with self.assertRaisesRegex(ValueError,'单片 2'):execute_group(recipe,files,self.root/'missing')
        self.assertTrue(recipe['include_base2'])

    def test_parallel_automation_equals_manual_snapshots_with_different_recipes(self):
        recipe=self.recipe('parallelism');recipe['roles']['measure']['transform_pipeline']=['ROT180']
        expected_records={r:self.manual_record(self.files[r],recipe['roles'][r]) for r in required_roles(recipe)}
        expected=self.w._compute_parallel_result_from_records(expected_records['base'],expected_records['measure'])
        result=self.isolated_execute(recipe,{r:self.files[r] for r in required_roles(recipe)},self.root/'parallel')
        snapshot=load_snapshot(self.root/'parallel')
        for key,value in expected.items():self.assertAlmostEqual(snapshot['result'][key],value,places=10)
        for r in required_roles(recipe):np.testing.assert_array_equal(snapshot['records'][r]['z'],expected_records[r]['z'])
        self.assertAlmostEqual(result['metrics']['drx_urad'],expected['drx'])

    def test_two_and_three_layer_gap_equal_manual_processed_points(self):
        for base2 in (False,True):
            recipe=self.recipe(base2=base2)
            # Gaussian output must be what is written to both manual and automatic slots.
            recipe['roles']['stack']['filter'].update(mode_index=4,gaussian_sigma_mm=.6)
            roles=required_roles(recipe);manual={r:self.manual_record(self.files[r],recipe['roles'][r],gap=True) for r in roles}
            expected=self.w._compute_gap_payload(manual['stack'],manual['base1'],manual.get('base2'),.05,lambda *a:None,threading.Event())
            folder=self.root/('three' if base2 else 'two')
            self.isolated_execute(recipe,{r:self.files[r] for r in roles},folder)
            actual=load_snapshot(folder)['result']
            np.testing.assert_allclose(actual['z'],expected['z'],rtol=0,atol=1e-14)
            np.testing.assert_array_equal(actual['details']['base1_index'],expected['details']['base1_index'])
            self.assertEqual(actual['layers']['base2'] is not None,base2)
            self.assertEqual(pd.read_csv(folder/'result.csv').shape[0],len(expected['z']))

    def isolated_execute(self,recipe,files,folder):
        folder.mkdir(parents=True,exist_ok=True)
        return process_group(recipe,files,folder,lambda *args:None,threading.Event(),timeout=60)

    def test_auto_registration_is_recomputed_for_each_new_group(self):
        recipe=self.recipe(mode='auto');roles=required_roles(recipe)
        for i,shift in enumerate((.02,-.03)):
            moving=self.root/f'moving{i}.csv'
            frame=pd.read_csv(self.files['base1']);frame.X+=shift;frame.to_csv(moving,index=False)
            folder=self.root/f'auto{i}';result=self.isolated_execute(recipe,dict(stack=self.files['stack'],base1=str(moving)),folder)
            self.assertAlmostEqual(result['registration_results']['base1']['offset_x'],-shift,places=9)
            self.assertAlmostEqual(recipe['registration']['offsets']['base1'][0],0)

    def test_failed_auto_registration_is_failure_not_stale_fixed_result(self):
        recipe=self.recipe(mode='auto');recipe['registration']['offsets']['base1']=[100.,100.]
        with self.assertRaisesRegex(ValueError,'自动配准未接受'):
            self.isolated_execute(recipe,{r:self.files[r] for r in required_roles(recipe)},self.root/'bad_auto')
        self.assertFalse((self.root/'bad_auto'/'snapshot.json').exists())

    def test_two_real_child_process_groups_do_not_share_slots_or_results(self):
        recipe=self.recipe('parallelism');groups=[]
        for i,height in enumerate((1.7,2.2)):
            paths={}
            for role in required_roles(recipe):
                path=self.root/f'{i}_{role}.csv';frame=pd.read_csv(self.files[role])
                if role=='measure':frame.Z+=height-1.7
                frame.to_csv(path,index=False);paths[role]=str(path)
            groups.append(self.group(recipe,f'A00{i}',paths))
        payload=run_groups(recipe,groups,self.root/'output',timeout=60)
        self.assertEqual([r['status'] for r in payload['results']],['success','success'])
        self.assertAlmostEqual(payload['results'][0]['metrics']['step_height_mm'],.7,places=9)
        self.assertAlmostEqual(payload['results'][1]['metrics']['step_height_mm'],1.2,places=9)
        self.assertNotEqual(payload['results'][0]['output_dir'],payload['results'][1]['output_dir'])
        self.assertTrue(Path(payload['summary']).is_file())
        audit=json.loads((Path(payload['results'][0]['output_dir'])/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(len(audit['inputs']['base']['sha256']),64)
        self.assertEqual(audit['inputs']['base']['single_recipe'],recipe['roles']['base'])

    def test_failure_continue_cancel_and_not_run_have_no_previous_metrics(self):
        recipe=self.recipe('parallelism');groups=[]
        for i in range(4):
            files={}
            for role in required_roles(recipe):
                path=self.root/f'{i}-{role}.csv';path.write_bytes(Path(self.files[role]).read_bytes());files[role]=str(path)
            groups.append(self.group(recipe,str(i),files))
        calls=[]
        def executor(recipe,files,folder,progress,cancel,**kwargs):
            index=len(calls);calls.append(index)
            if index==0:
                save_snapshot(folder,dict(module='parallelism',records={},result={}))
                return dict(metrics={'angle_urad':123.},outputs={})
            if index==1:raise ValueError('intentional failure')
            raise TaskCancelled()
        payload=run_groups(recipe,groups,self.root/'cancelout',executor=executor)
        self.assertEqual([r['status'] for r in payload['results']],['success','failed','cancelled','not_run'])
        self.assertEqual(calls,[0,1,2]);self.assertIsNone(payload['snapshot'])
        summary=pd.read_csv(payload['summary']);self.assertTrue(summary.angle_urad.iloc[1:].isna().all())
        self.assertTrue(all(not r['metrics'] for r in payload['results'][1:]))

    def test_pre_cancel_and_duplicate_files_never_start_executor(self):
        recipe=self.recipe('parallelism');group=self.group(recipe)
        def unexpected(*args,**kwargs):self.fail('executor must not start')
        event=threading.Event();event.set()
        result=run_groups(recipe,[group],self.root/'precancel',cancel=event,executor=unexpected)
        self.assertEqual(result['results'][0]['status'],'not_run')
        result=run_groups(recipe,[group,deepcopy(group)],self.root/'reuse',executor=unexpected)
        self.assertEqual([r['status'] for r in result['results']],['skipped','skipped'])

    def test_run_dialog_explicit_roles_and_base2_visibility(self):
        from surface_analyzer.composite_dialog import CompositeRunDialog
        for enabled in (False,True):
            d=CompositeRunDialog(self.w,self.recipe(base2=enabled));self.addCleanup(d.close)
            self.assertEqual('base2' in d.roles,enabled)
            self.assertTrue(all(not paths for paths in d.current_groups()[0]['files'].values()))

    def test_deletion_hash_mismatch_respects_preselected_policy(self):
        recipe=self.recipe('parallelism')
        operation=self.w._build_manual_delete_operation('XY',0.,0.,.1,.1)
        operation['source_sha256']='0'*64
        recipe['roles']['base']['manual_deletion'].update(operations=[operation],source_sha256='0'*64,sample_signature={})
        files={r:self.files[r] for r in required_roles(recipe)}
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):
            self.isolated_execute(recipe,files,self.root/'deletion_fail')
        recipe['policy']['deletion_mismatch']='skip_marked'
        result=self.isolated_execute(recipe,files,self.root/'deletion_skip')
        self.assertTrue(any('hash_mismatch' in warning for warning in result['warnings']))
        self.assertEqual(result['inputs']['base']['deletion_replay']['status'],'hash_mismatch')
        self.assertEqual(load_snapshot(self.root/'deletion_skip')['records']['base']['n'],30)

    def test_estimated_quality_is_rejected_or_propagated_by_policy(self):
        recipe=self.recipe('parallelism');files={}
        x,y=np.meshgrid(np.arange(101.),np.arange(101.))
        for role,z in (('base',1.),('measure',2.)):
            path=self.root/(role+'_large.csv')
            pd.DataFrame(dict(X=x.ravel(),Y=y.ravel(),Z=z+.001*x.ravel()+.002*y.ravel())).to_csv(path,index=False)
            files[role]=str(path)
            recipe['roles'][role]['large_file'].update(auto_sample=True,import_limit=10000,threshold_mb=4096)
        with self.assertRaisesRegex(ValueError,'质量策略拒绝'):
            self.isolated_execute(recipe,files,self.root/'quality_fail')
        recipe['policy']['estimated']='allow_marked'
        result=self.isolated_execute(recipe,files,self.root/'quality_marked')
        self.assertTrue(all(q['estimated'] for q in result['quality'].values()))
        self.assertTrue(result['warnings'])
        self.assertTrue(load_snapshot(self.root/'quality_marked')['result']['estimated'])

    def test_report_export_uses_existing_module_renderers(self):
        for module in ('parallelism','thickness'):
            recipe=self.recipe(module);recipe['export']['report']=True
            folder=self.root/('report_'+module)
            result=self.isolated_execute(recipe,{r:self.files[r] for r in required_roles(recipe)},folder)
            self.assertTrue(Path(result['outputs']['report']).stat().st_size>10000)
            self.assertTrue(Path(result['outputs']['csv']).is_file())

    def test_actual_child_cancel_terminates_without_commit(self):
        recipe=self.recipe('parallelism');event=threading.Event();folder=self.root/'actual_cancel';folder.mkdir()
        def progress(value,text):event.set()
        with self.assertRaises(TaskCancelled):
            process_group(recipe,{r:self.files[r] for r in required_roles(recipe)},folder,progress,event,timeout=60)
        self.assertTrue(event.is_set())

    def test_gui_background_run_responds_and_delivers_fresh_result(self):
        import time
        from PyQt6.QtCore import QTimer
        recipe=self.recipe('parallelism');heartbeat=[]
        timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:heartbeat.append(1));timer.start()
        self.addCleanup(timer.stop)
        self.assertTrue(self.w.start_composite_run(recipe,[self.group(recipe)],str(self.root/'gui'),timeout=60))
        self.assertFalse(self.w.centralWidget().isEnabled())
        deadline=time.monotonic()+30
        while self.w._task_thread is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.w._task_thread)
        self.assertTrue(self.w.centralWidget().isEnabled())
        self.assertGreater(len(heartbeat),10)
        self.assertEqual(self.w.last_composite_run['results'][0]['status'],'success')
        self.assertAlmostEqual(self.w.parallel_result['step_height'],.7,places=9)
        self.w._composite_results_dialog.close()

    def test_missing_source_column_fails_instead_of_semantic_remapping(self):
        recipe=self.recipe('parallelism');recipe['roles']['base']['column_mapping']['z_col']='does_not_exist'
        with self.assertRaisesRegex(ValueError,'所需源列'):
            self.isolated_execute(recipe,{r:self.files[r] for r in required_roles(recipe)},self.root/'wrong_column')

    def test_no_ready_group_does_not_modify_workspace(self):
        sentinel=object();self.w.parallel_result=sentinel
        with self.assertRaisesRegex(ValueError,'工作区未修改'):
            self.w.start_composite_run(self.recipe('parallelism'),[],str(self.root/'not_started'))
        self.assertIs(self.w.parallel_result,sentinel)

    def test_group_process_timeout_releases_owned_process_and_files(self):
        recipe=self.recipe('parallelism');folder=self.root/'timeout';folder.mkdir()
        with self.assertRaises(TimeoutError):
            process_group(recipe,{r:self.files[r] for r in required_roles(recipe)},folder,lambda *a:None,threading.Event(),timeout=.01)
        # On Windows this fails if only the venv launcher was stopped.
        (folder/'worker.log').unlink()

    def test_computed_dataset_is_not_saved_as_replayable_file_recipe(self):
        record=self.manual_record(self.files['base'])
        w=self.windows[-1];w.absolute_raw_df=None
        computed=w._current_parallel_record()
        self.assertIsNotNone(computed)
        self.assertIsNone(computed['single_recipe'])
        with self.assertRaisesRegex(ValueError,'参数快照'):
            build_composite('parallelism',dict(base=computed,measure=record),'unsupported derived input')

    def test_manual_gap_write_with_insufficient_points_keeps_slot_and_reports(self):
        self.manual_record(self.files['stack'],gap=True)
        w=self.windows[-1];w.active_idx=np.array([0,1])
        with patch.object(QMessageBox,'warning') as warning:
            w.set_memory_slot('stack')
        warning.assert_called_once();self.assertIsNone(w.data_stack)

    def test_progress_dialog_reject_cancels_and_restores_gui(self):
        import time
        recipe=self.recipe('parallelism')
        self.assertTrue(self.w.start_composite_run(recipe,[self.group(recipe)],str(self.root/'gui_cancel'),timeout=60))
        self.w._composite_progress[0].reject()
        deadline=time.monotonic()+20
        while self.w._task_thread is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.w._task_thread)
        self.assertTrue(self.w.last_composite_run['cancelled'])
        self.assertIsNone(self.w.parallel_result)
        self.assertTrue(self.w.centralWidget().isEnabled())
        self.w._composite_results_dialog.close()

    def test_empty_sample_id_is_skipped_by_executor_not_only_ui(self):
        recipe=self.recipe('parallelism');group=self.group(recipe,name='')
        def unexpected(*args,**kwargs):self.fail('invalid row must not execute')
        result=run_groups(recipe,[group],self.root/'empty_id',executor=unexpected)
        self.assertEqual(result['results'][0]['status'],'skipped')
        self.assertIn('样品编号',result['results'][0]['reason'])

    def test_recent_index_failure_does_not_report_started_job_as_failed(self):
        self.w._local_recipe_library=RecipeLibrary(self.root/'recent_library')
        path=self.w._recipe_library().save(self.recipe('parallelism'),'recent')
        with patch.object(self.w,'open_composite_run',return_value=True), patch.object(self.w._recipe_library(),'mark_used',side_effect=OSError('read only')), patch.object(QMessageBox,'critical') as critical:
            self.assertTrue(self.w.load_library_recipe(path))
            critical.assert_not_called()

    def test_noop_snapshot_serialization_preserves_arrays_without_pickle(self):
        save_snapshot(self.root,dict(z=np.arange(10.),details={'index':np.array([1,4])},quality=False))
        data=load_snapshot(self.root);np.testing.assert_array_equal(data['details']['index'],[1,4]);self.assertFalse(data['quality'])


if __name__=='__main__':unittest.main()
