import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import Future
from unittest.mock import patch
import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.plotting import color_scale_domains


class ColorDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.w=SurfaceAnalyzerPro()
        self.addCleanup(self.w.close)
        w=self.w
        x,y=np.meshgrid(np.linspace(-2,2,15),np.linspace(-2,2,15));x=x.ravel();y=y.ravel()
        z=np.where(x<0,.25+.0001*x*x+.0002*y,1.25+.001*y)
        z[-1]=100.
        w.df_raw=pd.DataFrame(dict(X=x,Y=y,Z=z))
        w.manual_mask=np.ones(len(z),bool);w.temp_selected_mask=np.zeros(len(z),bool);w._df_version+=1
        w.roi_enabled=True
        w.roi_shapes=[dict(id=1,type='rectangle',view='XY',cx=-1.2,cy=0.,width=1.5,height=4.2,enabled=True)]
        w.update_analysis()

    def test_filtered_spikes_update_gui_and_report_and_restore(self):
        w = self.w
        w.clear_rois(update=False)
        w.roi_enabled = False
        x, y = w.df_raw.X.to_numpy(), w.df_raw.Y.to_numpy()
        z = .25 + .0001*x + .0002*y
        z[-1] = 1.
        w.df_raw['Z'] = z
        w._df_version += 1
        w.spin_thresh.setValue(50.)
        w.update_analysis()
        self.assertEqual(w._shared_color_limits[1], 1.)
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                w.cb_filter.setCurrentIndex(mode)
                self.assertNotIn(len(z)-1, w.active_idx)
                expected = (float(z[w.active_idx].min()), float(z[w.active_idx].max()))
                self.assertEqual(w._shared_color_limits, expected)
                self.assertEqual(w.xy_raster.z_limits, expected)
                fig = w._render_report_figure(
                    'filtered', x, y, z, w.active_idx,
                    w.compute_plane_metrics(x[w.active_idx], y[w.active_idx], z[w.active_idx]),
                    w.n_filtered, '原始状态', w.cb_filter.currentText(), {},
                    overview_idx=np.flatnonzero(w.manual_mask))
                bars = [a._colorbar for a in fig.axes if hasattr(a, '_colorbar')]
                self.assertEqual(bars[0].mappable.get_clim(), expected)
                fig.clear()
        w.cb_filter.setCurrentIndex(0)
        self.assertEqual(w._shared_color_limits[1], 1.)

    def test_filter_exclusion_keeps_unanalysed_roi_context(self):
        x = np.arange(5.)
        z = np.array([0., 1., 100., 20., 500.])
        for mode in ('auto', 'global'):
            scales = color_scale_domains(
                x, x, z, np.arange(4), np.array([0, 1]), True, mode,
                analysis_indices=np.array([0, 1, 2]))
            self.assertEqual(scales['global_limits'], (0., 20.))
            self.assertEqual(scales['detail_limits'],
                             (0., 1.) if mode == 'auto' else (0., 20.))

    def test_roi_filter_updates_report_without_removing_outside_context(self):
        w = self.w
        z = w.df_raw.Z.to_numpy(copy=True)
        spike = int(w.active_idx[len(w.active_idx)//2])
        z[-1] = 2.
        z[spike] = 100.
        w.df_raw['Z'] = z
        w._df_version += 1
        w.cb_filter.setCurrentIndex(1)
        self.assertNotIn(spike, w.active_idx)
        self.assertEqual(w._shared_color_limits[1], 2.)
        self.assertLess(w._detail_color_limits[1], 1.)
        x, y, z = w.get_final_transformed_data(w.df_raw)
        mask = w._get_effective_roi_mask_cached(x, y, z, matrix_rc=None)
        for mode in ('auto', 'global'):
            fig = w._render_report_figure(
                'ROI-filter', x, y, z, w.active_idx,
                w.compute_plane_metrics(x[w.active_idx], y[w.active_idx], z[w.active_idx]),
                w.n_filtered, '原始状态', w.cb_filter.currentText(), {},
                overview_idx=np.flatnonzero(w.manual_mask), roi_mask_all=mask,
                render_config={'color_scale_mode': mode})
            bars = [a._colorbar for a in fig.axes if hasattr(a, '_colorbar')]
            self.assertEqual(bars[0].mappable.get_clim(), w._shared_color_limits)
            if mode == 'auto':
                self.assertEqual(bars[1].mappable.get_clim(), w._detail_color_limits)
            fig.clear()

    def test_auto_roi_local_clim_and_global_uniform_option(self):
        w=self.w
        z=w.df_raw.Z.to_numpy()
        expected=(float(z[w.active_idx].min()),float(z[w.active_idx].max()))
        self.assertEqual(w._shared_color_limits,(float(z.min()),100.))
        self.assertEqual(w._detail_color_limits,expected)
        for ax in (w.canvas.ax3d,w.canvas.ax_xz,w.canvas.ax_yz):
            self.assertEqual(ax.collections[0].get_clim(),expected)
        self.assertEqual(w.xy_raster.z_limits,w._shared_color_limits)
        self.assertEqual(w.canvas.detail_colorbar.mappable.get_clim(),expected)
        metrics=dict(w.last_metrics)
        w.cb_color_scale.setCurrentIndex(w.cb_color_scale.findData('global'))
        self.assertTrue(w.canvas.detail_colorbar_canvas.isHidden())
        self.assertEqual(w._detail_color_limits,w._shared_color_limits)
        self.assertEqual(w.last_metrics,metrics)

    def test_delete_restore_and_stale_raster_rejection(self):
        w=self.w
        old_generation=w.xy_raster.generation
        w.manual_mask[-1]=False;w.update_analysis()
        self.assertLess(w._shared_color_limits[1],2.)
        self.assertGreater(w._shared_color_limits[1],1.)
        future=Future();future.set_result(object())
        w.xy_raster.future=future;w.xy_raster.job_generation=old_generation
        with patch.object(w.xy_raster,'apply') as apply:
            w.xy_raster.finish()
        apply.assert_not_called()
        self.assertEqual(w.xy_raster.z_limits,w._shared_color_limits)
        w.manual_mask[:]=True;w.update_analysis()
        self.assertEqual(w._shared_color_limits[1],100.)
        self.assertLess(w._detail_color_limits[1],1.)

    def test_ranges_ignore_viewport_lod_and_use_current_display_quantity(self):
        w=self.w
        expected=w._detail_color_limits
        with patch.object(w,'_display_limit',return_value=15):
            w.update_plots_only()
        self.assertEqual(w._detail_color_limits,expected)
        w.canvas.ax_xy.set_xlim(-1.5,-1.)
        w.update_plots_only(preserve_view=True)
        self.assertEqual(w._detail_color_limits,expected)
        w.cb_surface_display.setCurrentIndex(w.cb_surface_display.findData('residual_1'))
        x,y,z=w.get_final_transformed_data(w.df_raw)
        shown,label,_=w._get_plot_z(x,y,z)
        self.assertIn('µm',label)
        self.assertEqual(w._detail_color_limits,(float(shown[w.active_idx].min()),float(shown[w.active_idx].max())))

    def test_two_report_bars_follow_same_domains_after_deletion(self):
        w=self.w
        w.manual_mask[-1]=False;w.update_analysis()
        x,y,z=w.get_final_transformed_data(w.df_raw)
        keep=w._get_effective_roi_mask_cached(x,y,z,matrix_rc=None)
        metrics=w.compute_plane_metrics(x[w.active_idx],y[w.active_idx],z[w.active_idx])
        for mode,count in (('auto',2),('global',1)):
            with self.subTest(mode=mode):
                fig=w._render_report_figure('layers',x,y,z,w.active_idx,metrics,0,'原始状态','关闭',{},
                    overview_idx=np.flatnonzero(w.manual_mask),roi_mask_all=keep,
                    render_config={'color_scale_mode':mode})
                bars=[a._colorbar for a in fig.axes if hasattr(a,'_colorbar')]
                self.assertEqual(len(bars),count)
                self.assertEqual(bars[0].mappable.get_clim(),w._shared_color_limits)
                if count==2:
                    self.assertEqual(bars[1].mappable.get_clim(),w._detail_color_limits)
                    self.assertIn('当前 ROI',bars[1].ax.get_ylabel())
                fig.canvas.draw()
                fig.clear()

    def test_flat_empty_and_deleted_domains(self):
        x=np.arange(4.);y=x.copy();z=np.array([1.,1.,20.,np.nan])
        result=color_scale_domains(x,y,z,[0,1,2],[0,1,3],True)
        self.assertEqual(result['global_limits'],(1.,20.))
        self.assertEqual(result['detail_flat'],1.)
        self.assertLess(result['detail_limits'][0],1.)
        result=color_scale_domains(x,y,z,[2],[0,1],True)
        self.assertIsNone(result['detail_limits'])
        self.w.canvas.set_color_scale(None,'当前 ROI · Z (mm)',detail=True)
        self.assertTrue(self.w.canvas.detail_colorbar_canvas.isHidden())
        self.assertEqual(len(self.w.canvas.detail_colorbar_figure.axes),1)

    def test_recipe_and_batch_capture_color_option(self):
        w=self.w
        w.cb_color_scale.setCurrentIndex(w.cb_color_scale.findData('global'))
        recipe=w._current_recipe_dict()
        self.assertEqual(recipe['display']['color_scale_mode'],'global')
        self.assertEqual(w._capture_batch_params()['render_config']['color_scale_mode'],'global')
        with patch.object(w,'update_analysis'):
            w.apply_recipe(recipe,show_message=False)
        self.assertEqual(w.cb_color_scale.currentData(),'global')


class PanelWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_default_widths_and_manual_memory_across_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=QSettings(str(Path(directory)/'ui.ini'),QSettings.Format.IniFormat)
            with patch('surface_analyzer.app.QSettings',return_value=settings):
                w=SurfaceAnalyzerPro()
            try:
                w.show()
                for width,expected in ((1366,320),(1600,360),(1920,400)):
                    w.resize(width,900)
                    for _ in range(4):self.app.processEvents()
                    self.assertAlmostEqual(w.main_splitter.sizes()[0],expected,delta=4)
                w.main_splitter.setSizes([430,1400]);w._remember_parameter_width(0,1)
                remembered=w.main_splitter.sizes()[0]
                w._set_parameters_collapsed(True)
                w.resize(1600,900);self.app.processEvents()
                w._set_parameters_collapsed(False)
                self.assertAlmostEqual(w.main_splitter.sizes()[0],remembered,delta=4)
                with patch('surface_analyzer.app.QSettings',return_value=settings):
                    second=SurfaceAnalyzerPro()
                try:
                    second.show();self.app.processEvents()
                    self.assertEqual(second._preferred_parameter_width,remembered)
                finally:second.close()
            finally:w.close()


if __name__=='__main__':unittest.main()
