"""V5.0.8 acceptance: actual main copy, shared batch, VIG missing regions."""
import importlib.util
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PyQt6.QtWidgets import QDialog, QMessageBox
from tests.test_v507 import Base
from surface_analyzer.mixins.gap import GapAnalysisMixin
from surface_analyzer.composite_dialog import CompositeSaveDialog

class Main508(Base):
    def test_gap_auto_main_copy_and_main_export_trace(self):
        self.load('总成.csv', .8+.001*self.x);self.w.set_memory_slot('stack')
        self.load('单片1.csv',.4+.0001*self.y);self.w.set_memory_slot('base1')
        w=self.w;p=GapAnalysisMixin._compute_gap_payload(w.data_stack,w.data_base1,None,.01,lambda *a:None,__import__('threading').Event())
        original=p['z'].copy();w._apply_gap_payload(p)
        self.assertEqual(w.tabs.currentIndex(),0)
        self.assertIsNotNone(w.last_metrics)
        self.assertIs(w.gap_result,p);self.assertIsNone(w.absolute_raw_df)
        self.assertFalse(hasattr(w,'gap_result_figure'))
        np.testing.assert_allclose(w.df_raw['Z'],original)
        w.df_raw.loc[0,'Z']+=1
        np.testing.assert_array_equal(p['z'],original)
        lines=w._import_trace_text(w.import_info)
        self.assertTrue(any('膜厚' in x and '来源' in x for x in lines))
        self.assertIsNone(w._slot_single_recipe_snapshot())
        self.assertTrue(any('容差' in line for line in lines))

    def test_example_batch_freezes_roles_without_library_or_manual_mask(self):
        self.load('总成.csv',.8);self.w.set_memory_slot('stack')
        self.load('单片1.csv',.4);self.w.set_memory_slot('base1')
        w=self.w;w.gap_result={'example':True}
        original=deepcopy(w.data_stack['single_recipe'])
        def accept(dialog):
            dialog.validate();return QDialog.DialogCode.Accepted
        with patch.object(CompositeSaveDialog,'exec',accept),patch.object(w,'open_composite_run',return_value=True) as run:
            self.assertTrue(w.batch_from_example('thickness'))
        recipe=run.call_args.args[0]
        self.assertEqual(recipe['roles']['stack']['manual_deletion'],{})
        self.assertEqual(w.data_stack['single_recipe'],original)
        self.assertEqual(recipe['roles']['stack']['units'],original['units'])

class Vig508(Base):
    def test_slit_counts_and_invalid_spacing_are_traceable(self):
        spec=importlib.util.spec_from_file_location('engine508',Path(__file__).resolve().parents[1]/'extensions/vig-image/engine.py')
        e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
        x,y=np.meshgrid([-3,-2.5,-2,2,2.5,3],[-2,-1,0,1,2]);x,y=x.ravel(),y.ravel()
        def rec(a,b,z):return dict(x=a,y=b,z=np.full(len(a),z),name='slits',n=len(a))
        two=rec(x,y,.6);three=rec(x,y,.95);electrodes=dict(up=150.,mid=150.,low=150.)
        good=e.compute_vig_image(two,three,electrodes,tolerance_mm=.01)
        self.assertEqual(good['stage_counts']['success'],30)
        missing=e.compute_vig_image(two,rec(x[x<0],y[x<0],.95),electrodes,tolerance_mm=.01)
        self.assertEqual(missing['stage_counts']['unmatched'],15)
        self.assertEqual(sum(d['status']=='unmatched' for d in missing['diagnostics']),15)
        three['z'][x>0]=.7
        invalid=e.compute_vig_image(two,three,electrodes,tolerance_mm=.01)
        self.assertEqual(invalid['stage_counts']['invalid_input'],15)
        self.assertEqual(invalid['n_errors'],15)
        self.assertTrue(all(d['x']>0 for d in invalid['errors']))
        self.assertEqual(invalid['stage_counts']['success'],15)
        self.assertTrue(all(d['stage']=='input' for d in invalid['errors']))
