import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from surface_analyzer.rendering.raster import build_xy_raster, raster_rgba


class RasterTests(unittest.TestCase):
    def test_edges_means_and_roi(self):
        r = build_xy_raster(np.array([0., .1, 1]), np.array([0., .1, 1]),
                            np.array([1., 3., 7]), (0, 1, 0, 1), (2, 2), [True, False, False])
        self.assertEqual(r.count.sum(), 3)
        self.assertEqual(r.z_mean[0, 0], 2)
        self.assertEqual(r.z_mean[1, 1], 7)
        self.assertEqual(r.roi_count.sum(), 1)
        self.assertTrue(np.isnan(r.z_mean[0, 1]))
        self.assertEqual(raster_rgba(r)[0, 1, 3], 0)
        self.assertEqual(raster_rgba(r, 'missing')[0, 1, 3], 1)

    def test_hole_and_irregular_density(self):
        rng = np.random.default_rng(42)
        x, y = rng.uniform(-1, 1, (2, 100000))
        keep = x*x+y*y > .2**2
        x, y = x[keep], y[keep]
        r = build_xy_raster(x, y, x+y, (-1, 1, -1, 1), (100, 100))
        self.assertEqual(r.count.sum(), len(x))
        self.assertEqual(r.count[45:55, 45:55].sum(), 0)
        self.assertEqual(raster_rgba(r, 'density')[50, 50, 3], 0)

    def test_viewport_and_input_unchanged(self):
        x = np.linspace(-1, 1, 1000)
        before = x.copy()
        r = build_xy_raster(x, x, x, (-.1, .1, -.1, .1), (3000, 3000))
        self.assertEqual(r.count.shape, (1200, 1200))
        self.assertEqual(r.visible_count, 100)
        np.testing.assert_array_equal(x, before)

    def test_gui_zoom_modes_seed_and_metrics(self):
        import pandas as pd
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtTest import QTest
        from surface_analyzer.app import SurfaceAnalyzerPro
        app = QApplication.instance() or QApplication([])
        w = SurfaceAnalyzerPro()
        try:
            x, y = np.meshgrid(np.linspace(-15, 15, 301), np.linspace(-15, 15, 301))
            x, y = x.ravel(), y.ravel()
            keep = x*x+y*y > 9
            x, y = x[keep], y[keep]
            z = .45 + 1e-5*x - 2e-5*y + .0001*np.sin(x)
            z[12345] += .02
            z[23456] -= .015
            w.df_raw = pd.DataFrame(dict(X=x, Y=y, Z=z))
            w.current_source_name = 'Synthetic missing-region XYZ'
            w.lbl_source.setText('当前数据: Synthetic missing-region XYZ（测试）')
            for combo,label in ((w.cb_x_col,'X'),(w.cb_y_col,'Y'),(w.cb_z_col,'Z')):
                combo.addItem(label)
            w.cb_z_unit.setCurrentText('mm')
            w._df_version += 1
            w.manual_mask = np.ones(len(x), dtype=bool)
            w.temp_selected_mask = np.zeros(len(x), dtype=bool)
            w.import_info = dict(import_rows=len(x), sampled=False, mapped_finite_points=len(x))
            w.resize(1366, 768)
            w.show()
            w.update_analysis()
            def wait_render():
                deadline = time.monotonic()+8
                while (w.xy_raster.result is None or w.xy_raster.timer.isActive() or w.xy_raster.future is not None) and time.monotonic()<deadline:
                    QTest.qWait(30)
                self.assertIsNotNone(w.xy_raster.result)
                self.assertFalse(w.xy_raster.timer.isActive())
            wait_render()
            self.assertEqual(w.xy_raster.result.source_count, len(x))
            self.assertEqual(w.xy_raster.result.count.sum(), len(x))
            self.assertTrue({12345,23456}.issubset(w._last_detail_plot_indices))
            metrics, active = w.last_metrics, w.active_idx.copy()
            with patch.object(w, 'update_analysis', side_effect=AssertionError('display called analysis')):
                w.canvas.ax_xy.set_xlim(-5, 5)
                w.canvas.ax_xy.set_ylim(-5, 5)
                wait_render()
                self.assertEqual(w.xy_raster.result.extent, (-5., 5., -5., 5.))
                self.assertLess(w.xy_raster.result.visible_count, len(x))
                for mode in (1, 2, 0):
                    w.canvas.xy_mode.setCurrentIndex(mode)
                    wait_render()
                    self.assertIs(w.last_metrics, metrics)
                    np.testing.assert_array_equal(w.active_idx, active)
            w.selection_mode = 'roi_smart'
            index = 4321
            event = SimpleNamespace(button=1, dblclick=False, inaxes=w.canvas.ax_xy,
                                    xdata=x[index], ydata=y[index])
            with patch.object(w, 'add_smart_face_roi_from_seed') as seed:
                w.on_canvas_click(event)
                self.assertEqual(seed.call_args.kwargs['seed_index'], index)
            # A completed old request must not overwrite the accepted view.
            from concurrent.futures import Future
            accepted = w.xy_raster.result
            old = Future()
            old.set_result(build_xy_raster(x, y, z, (-15, 15, -15, 15), (10, 10)))
            w.xy_raster.future = old
            w.xy_raster.job_generation = w.xy_raster.generation - 1
            w.xy_raster.job_key = w.xy_raster.key()
            w.xy_raster.finish()
            self.assertIs(w.xy_raster.result, accepted)
            if os.environ.get('SURFACE_RASTER_SCREENSHOT'):
                from pathlib import Path
                output = Path(os.environ['SURFACE_RASTER_SCREENSHOT'])
                for width, height in ((1366,768), (1600,900), (1920,1080), (900,640)):
                    w.resize(width, height)
                    app.processEvents()
                    wait_render()
                    w.grab().save(str(output.with_name(f'{output.stem}_{width}.png')))
                w.resize(1366,768)
                for mode in (1, 2):
                    w.canvas.xy_mode.setCurrentIndex(mode)
                    wait_render()
                    w.grab().save(str(output.with_name(f'{output.stem}_mode{mode}.png')))
            # ROI gray occupancy uses every ROI source point, not its scatter sample.
            w.xy_raster.reset()
            w.xy_raster.bind(x, y, z, x < 0, 'height')
            wait_render()
            r = w.xy_raster.result
            visible = (x >= r.extent[0]) & (x <= r.extent[1]) & (y >= r.extent[2]) & (y <= r.extent[3])
            self.assertEqual(r.roi_count.sum(), np.sum(visible & (x < 0)))
            self.assertEqual(np.count_nonzero(w.xy_raster.overlay.get_array()[...,3]), np.count_nonzero(r.roi_count))
            # Reports use the same full-input raster and spatial LOD, and do
            # not mutate the existing authoritative metric dictionary.
            fig = w._render_report_figure('Synthetic XYZ / missing region', x,y,z,
                    w.active_idx,w.compute_plane_metrics(x[w.active_idx],y[w.active_idx],z[w.active_idx]),0,'原始状态','关闭',w.import_info,
                    overview_idx=np.arange(len(x)),render_config={'xy_mode':'height'})
            self.assertTrue(any(len(ax.images) == 2 for ax in fig.axes))
            self.assertIs(w.last_metrics,metrics)
            if os.environ.get('SURFACE_RASTER_SCREENSHOT'):
                fig.savefig(str(output.with_name(f'{output.stem}_report.png')),dpi=110)
            import matplotlib.pyplot as plt
            plt.close(fig)
            from unittest.mock import patch as mock_patch
            recipe = w._current_recipe_dict()
            self.assertNotIn('cache',recipe['display'])
            recipe['display'].update(xy_mode='density',xy_raster_max_side=600)
            with mock_patch('surface_analyzer.mixins.recipe.QSettings'), mock_patch('surface_analyzer.mixins.recipe.QMessageBox.information'):
                w.apply_recipe(recipe,remap_current_data=False)
            self.assertEqual(w.canvas.xy_mode.currentData(),'density')
            self.assertEqual(w.canvas.xy_resolution.currentData(),600)
        finally:
            w.close()


if __name__ == '__main__':
    unittest.main()
