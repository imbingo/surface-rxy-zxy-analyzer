import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import csv
import io
import unittest
from unittest.mock import patch
from dataclasses import replace
import numpy as np
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.adjustment import Plane, SupportPoint, AdjustmentConfig, calculate_adjustment
from surface_analyzer.adjustment_panel import default_fixture, validate_fixture
from surface_analyzer.mixins.analysis import AnalysisMixin


class AdjustmentPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        mock=patch('surface_analyzer.mixins.recipe.QMessageBox.information')
        mock.start();self.addCleanup(mock.stop)
        self.w=SurfaceAnalyzerPro();self.addCleanup(self.w.close)
        self.p=self.w.adjustment_panel

    def ready(self):
        x=np.array([-10.,10.,-10.,10.]);y=np.array([-10.,-10.,10.,10.])
        def rec(a,b,c):
            z=a*x+b*y+c
            return {'x':x,'y':y,'z':z,'metrics':AnalysisMixin.compute_plane_metrics(x,y,z),
                    'name':'demo','n':4,'import_strategy':'full','pipeline':'原始状态'}
        self.w.parallel_base=rec(.0001,-.0002,.45)
        self.w.parallel_measure=rec(-.0002,.0001,.46)
        self.w._update_parallel_ui()
        self.w._apply_parallel_result(self.w._compute_parallel_result())

    def test_calculate_export_copy_and_edit_invalidation(self):
        self.assertFalse(self.p.calculate_button.isEnabled())
        self.ready();self.p.calculate();self.assertIsNotNone(self.p.result)
        rows=list(csv.reader(io.StringIO(self.p.csv_text())))
        self.assertTrue(any('ReferenceOriginX_mm' in r for r in rows))
        self.assertTrue(any('SupportCoplanarity_pv_um' in r for r in rows))
        self.p.copy_result();self.assertIn('理论调整',QApplication.clipboard().text())
        self.p.supports.item(0,1).setText('-79')
        self.assertIsNone(self.p.result);self.assertFalse(self.p.export_button.isEnabled())
        with self.assertRaises(ValueError):self.p.csv_text()

    def test_swap_invalidation_and_sign(self):
        self.ready();self.p.calculate()
        h=[p.theoretical_adjustment_um for p in self.p.result.support_results]
        self.w.swap_parallel_surfaces();self.assertIsNone(self.p.result)
        self.assertFalse(self.p.calculate_button.isEnabled())
        self.w._apply_parallel_result(self.w._compute_parallel_result());self.p.calculate()
        np.testing.assert_allclose(h,[-p.theoretical_adjustment_um for p in self.p.result.support_results])

    def test_recipe_roundtrip_and_no_results(self):
        self.p.template(4);self.p.quant.setCurrentIndex(1)
        self.p.origin.setCurrentIndex(2);self.p.x0.setValue(12)
        self.p.target.setCurrentIndex(1);self.p.rx.setValue(20)
        self.p.supports.item(0,6).setText('2.5')
        self.ready();self.p.calculate()
        recipe=self.w._current_recipe_dict();saved=recipe['adjustment_config']
        self.assertNotIn('support_results',saved)
        self.w.apply_recipe(recipe,remap_current_data=False)
        self.assertEqual(saved,self.p.fixture())
        self.assertIsNone(self.p.result);self.assertIsNone(self.w.parallel_base)
        self.assertFalse(self.p.calculate_button.isEnabled())
        old=dict(recipe);old.pop('adjustment_config');old['schema_version']=7
        self.w.apply_recipe(old,remap_current_data=False)
        self.assertEqual(self.p.fixture(),default_fixture())

    def test_invalid_fixture_does_not_mutate(self):
        before=self.p.fixture();bad=self.w._current_recipe_dict()
        bad['adjustment_config']['quantization']['default_step_um']=0
        with self.assertRaises(ValueError):self.w.apply_recipe(bad,remap_current_data=False)
        self.assertEqual(before,self.p.fixture())

    def test_templates_custom_and_duplicate_guard(self):
        self.ready();self.p.template(4);self.p.add_point()
        self.p.supports.item(4,1).setText('20');self.p.supports.item(4,2).setText('30')
        self.p.calculate();self.assertEqual(len(self.p.result.support_results),5)
        self.p.supports.selectRow(0);self.p.duplicate();self.p.calculate()
        self.assertIsNone(self.p.result);self.assertIn('重复',self.p.status.text())
        self.p.clear();self.p.calculate();self.assertIsNone(self.p.result)

    def test_coplanarity_quantized_and_warning(self):
        points=tuple(SupportPoint(str(i),x,y,100,0,500,s) for i,(x,y,s) in enumerate([
            (-1,-1,5),(1,-1,5),(-1,1,5),(1,1,7)]))
        plain=calculate_adjustment(Plane(0,0,0),Plane(.001,.002,.0173),points)
        self.assertLess(plain.coplanarity.pv_um,1e-8)
        quant=calculate_adjustment(Plane(0,0,0),Plane(.001,.002,.0173),points,
                                  AdjustmentConfig(quantization='fixed_step',coplanarity_warn_um=.01))
        residual=np.array(quant.coplanarity.residuals_um)
        self.assertAlmostEqual(quant.coplanarity.pv_um,np.ptp(residual))
        self.assertAlmostEqual(quant.coplanarity.rms_um,np.sqrt(np.mean(residual**2)))
        self.assertEqual(quant.coplanarity.worst_point,points[int(np.argmax(abs(residual)))].name)
        self.assertTrue(quant.coplanarity.warning)
        self.assertTrue(any('虚接触' in text for text in quant.warnings))

    def test_replaced_record_rejects_inflight_result(self):
        self.ready();callbacks=[]
        with patch.object(self.w,'_run_background_task',side_effect=lambda name,fn,cb: callbacks.append(cb)):
            self.w.calculate_parallelism()
        result=self.w._compute_parallel_result()
        self.w.swap_parallel_surfaces()
        callbacks[0](result)
        self.assertIsNone(self.w.parallel_result)
        self.assertFalse(self.p.calculate_button.isEnabled())

    def test_main_roi_update_invalidates_adjustment(self):
        import pandas as pd
        self.ready();self.p.calculate()
        record=self.w.parallel_measure
        self.w.df_raw=pd.DataFrame({'X':record['x'],'Y':record['y'],'Z':record['z']})
        self.w.manual_mask=np.ones(4,dtype=bool)
        self.w.temp_selected_mask=np.zeros(4,dtype=bool)
        self.w.update_analysis()
        self.assertIsNone(self.p.result)
        self.assertIsNone(self.w.parallel_result)
        self.assertFalse(self.p.export_button.isEnabled())


if __name__=='__main__':unittest.main()
