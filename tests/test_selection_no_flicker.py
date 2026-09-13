import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from surface_analyzer.app import SurfaceAnalyzerPro


class SelectionNoFlickerTests(unittest.TestCase):
    def test_selection_and_cancel_keep_base_artists_cache_and_view(self):
        app = QApplication.instance() or QApplication([])
        w = SurfaceAnalyzerPro()
        try:
            x,y = np.meshgrid(np.linspace(-15,15,301),np.linspace(-15,15,301))
            x,y = x.ravel(),y.ravel()
            w.df_raw = pd.DataFrame(dict(X=x,Y=y,Z=.45+x*.0001+y*.00002))
            w._df_version += 1
            w.manual_mask = np.ones(len(x),bool)
            w.temp_selected_mask = np.zeros(len(x),bool)
            w.resize(1366,768)
            w.show()
            w.update_analysis()
            def ready():
                end = time.monotonic()+10
                while time.monotonic()<end:
                    QTest.qWait(30)
                    if w.xy_raster.result is not None and w.xy_raster.future is None and not w.xy_raster.timer.isActive():
                        return
                self.fail('display timed out')
            ready()
            metrics = w.last_metrics
            for mode in (0,1):
                w.canvas.xy_mode.setCurrentIndex(mode)
                ready()
                c = w.xy_raster
                base = c.scatter if mode else c.image
                generation, result, grid = c.generation,c.result,c.scan_grid
                limits = w.canvas.ax_xy.get_xlim(),w.canvas.ax_xy.get_ylim()
                frames = []
                cid = w.canvas.ax_xy.figure.canvas.mpl_connect(
                    'draw_event',lambda event: frames.append(base.get_visible() and base.axes is w.canvas.ax_xy))
                with patch.object(c,'reset',side_effect=AssertionError('selection reset source')), \
                     patch.object(w,'draw_plots',side_effect=AssertionError('selection rebuilt plots')):
                    for view, bounds in (('XY',(-2,-2,2,2)),('XZ',(-2,.449,2,.451)),('YZ',(-2,.449,2,.451))):
                        a,b,d,e = bounds
                        w.on_select(SimpleNamespace(xdata=a,ydata=b),SimpleNamespace(xdata=d,ydata=e),view)
                        app.processEvents()
                        self.assertTrue(w._temp_selection_overlay_artists)
                        w.cancel_temp_selection()
                        app.processEvents()
                        self.assertFalse(w._temp_selection_overlay_artists)
                self.assertTrue(frames)
                self.assertTrue(all(frames))
                self.assertIs(c.result,result)
                self.assertEqual(c.generation,generation)
                self.assertEqual(c.scan_grid,grid)
                self.assertIs(w.last_metrics,metrics)
                np.testing.assert_equal((w.canvas.ax_xy.get_xlim(),w.canvas.ax_xy.get_ylim()),limits)
                w.canvas.ax_xy.figure.canvas.mpl_disconnect(cid)
            w.canvas.xy_mode.setCurrentIndex(0)
            ready()
            original_grid = w.xy_raster.scan_grid
            w.canvas.set_focused_view('XY')
            ready()
            self.assertTrue(w.xy_raster.key()[4])
            self.assertEqual(w.xy_raster.scan_grid,original_grid)
            self.assertIn('细节',w._xy_raster_status)
        finally:
            w.close()
