import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.plotting import display_color_limits

class SharedColorScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_flat_and_empty_ranges(self):
        self.assertIsNone(display_color_limits([np.nan,np.inf]))
        low,high=display_color_limits([2.,2.])
        self.assertLess(low,2.)
        self.assertGreater(high,2.)

    def test_roi_and_display_sampling_use_full_undeleted_range(self):
        w=SurfaceAnalyzerPro();self.addCleanup(w.close)
        w.cb_color_scale.setCurrentIndex(w.cb_color_scale.findData('global'))
        x,y=np.meshgrid(np.arange(3.),np.arange(3.));x=x.ravel();y=y.ravel()
        z=1.+x*.001+y*.002;z[-1]=100.
        w.df_raw=pd.DataFrame(dict(X=x,Y=y,Z=z));w._df_version+=1
        w.manual_mask=np.ones(9,bool);w.temp_selected_mask=np.zeros(9,bool)
        w.roi_enabled=True
        w.roi_shapes=[dict(id=1,type='rectangle',view='XY',cx=.5,cy=1.,width=1.5,height=3.,enabled=True)]
        w.update_analysis()
        self.assertLess(len(w.active_idx),9)
        self.assertEqual(w._shared_color_limits,(1.,100.))
        for axis in (w.canvas.ax3d,w.canvas.ax_xz,w.canvas.ax_yz):
            self.assertEqual(axis.collections[0].get_clim(),(1.,100.))
        self.assertEqual(w.xy_raster.z_limits,(1.,100.))
        metrics=dict(w.last_metrics)
        w.canvas.ax_xy.set_xlim(-.1,1.1)
        self.assertEqual(w._shared_color_limits,(1.,100.))
        w.update_plots_only(preserve_view=True)
        self.assertEqual(len(w.canvas.colorbar_figure.axes),1)
        self.assertEqual(w.canvas.shared_colorbar.mappable.get_clim(),(1.,100.))
        self.assertEqual(w.last_metrics,metrics)
        w.manual_mask[-1]=False
        w.update_analysis()
        self.assertLess(w._shared_color_limits[1],100.)

    def test_report_has_one_xy_colorbar_with_full_overview_range(self):
        w=SurfaceAnalyzerPro();self.addCleanup(w.close)
        x,y=np.meshgrid(np.arange(3.),np.arange(3.));x=x.ravel();y=y.ravel()
        z=1.+x*.001+y*.002
        active=np.arange(8)
        metrics=w.compute_plane_metrics(x[active],y[active],z[active])
        fig=w._render_report_figure('demo',x,y,z,active,metrics,0,'原始状态','关闭',{},overview_idx=np.arange(9))
        bars=[a._colorbar for a in fig.axes if hasattr(a,'_colorbar')]
        self.assertEqual(len(bars),1)
        self.assertEqual(bars[0].orientation,'vertical')
        self.assertEqual(bars[0].mappable.get_clim(),(float(z.min()),float(z.max())))
        fig.clear()

    def test_flat_colorbar_labels_actual_value_and_empty_hides_it(self):
        w=SurfaceAnalyzerPro();self.addCleanup(w.close)
        w.canvas.set_color_scale(display_color_limits([1.25377]),'Z (mm)',1.25377)
        w.canvas.colorbar_canvas.draw()
        self.assertEqual(w.canvas.shared_colorbar.get_ticks().tolist(),[1.25377])
        self.assertEqual(w.canvas.shared_colorbar.formatter(1.25377,0),'1.25377')
        w.canvas.set_color_scale(None,'Z (mm)')
        self.assertFalse(w.canvas.colorbar_axis.get_visible())
        self.assertEqual(len(w.canvas.colorbar_figure.axes),1)

    def test_flat_report_colorbar_shows_actual_height(self):
        w=SurfaceAnalyzerPro();self.addCleanup(w.close)
        x,y=np.meshgrid(np.arange(3.),np.arange(3.));x=x.ravel();y=y.ravel()
        z=np.full(len(x),1.25377)
        metrics=w.compute_plane_metrics(x,y,z)
        fig=w._render_report_figure('flat',x,y,z,np.arange(len(z)),metrics,0,'原始状态','关闭',{},overview_idx=np.arange(len(z)))
        bar=next(a._colorbar for a in fig.axes if hasattr(a,'_colorbar'))
        self.assertEqual(bar.get_ticks().tolist(),[1.25377])
        self.assertEqual(bar.formatter(1.25377,0),'1.25377')
        fig.clear()
