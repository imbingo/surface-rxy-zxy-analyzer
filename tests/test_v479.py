import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from types import SimpleNamespace
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from mpl_toolkits.mplot3d import proj3d
from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.plotting import fit_surface_box_to_canvas, pad_surface_limits


class CanvasFitTests(unittest.TestCase):
    def scene(self, width=640, height=300, ratio=1.0):
        fig = Figure(figsize=(width/100, height/100), dpi=100)
        FigureCanvasAgg(fig)
        ax = fig.add_axes([0, 0, 1, 1], projection='3d')
        ax.set_proj_type('ortho'); ax.view_init(30, -135)
        x, y = np.meshgrid(np.linspace(-15, 15, 16), np.linspace(-15*ratio, 15*ratio, 16))
        z = .45 + .0001*x - .0002*y
        ax.plot_surface(x, y, z)
        ax.set_xlabel('X (mm)'); ax.set_ylabel('Y (mm)'); ax.set_zlabel('Z (mm)')
        pad_surface_limits(ax, x, y, z)
        return fig, ax, (x, y, z)

    @staticmethod
    def screen_box(ax):
        corners = np.array([(a,b,c) for a in ax.get_xlim3d() for b in ax.get_ylim3d() for c in ax.get_zlim3d()])
        projected = np.column_stack(proj3d.proj_transform(*corners.T, ax.get_proj())[:2])
        return ax.transData.transform(projected)

    def test_landscape_uses_actual_drawn_transform_and_fills_height(self):
        fig, ax, xyz = self.scene()
        limits = ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()
        fit_surface_box_to_canvas(ax, *xyz, min_z_ratio=.18)
        recorded = ax._surface_home_screen_bounds
        fig.canvas.draw()
        actual = self.screen_box(ax)
        np.testing.assert_allclose(recorded, (actual[:,0].min(),actual[:,0].max(),actual[:,1].min(),actual[:,1].max()))
        self.assertGreater(np.ptp(actual[:,1])/300, .75)
        self.assertTrue(ax._surface_home_fits)
        self.assertTrue(ax._surface_home_labels_fit)
        self.assertEqual(limits, (ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()))

    def test_high_dpi_matches_logical_scene_size(self):
        bounds = []
        for ratio in (1, 1.25, 1.5, 2):
            fig, ax, xyz = self.scene(360, 230)
            fig.canvas._set_device_pixel_ratio(ratio)
            fit_surface_box_to_canvas(ax, *xyz, min_z_ratio=.18)
            fig.canvas.draw()
            screen = self.screen_box(ax) / ratio
            bounds.append(np.ptp(screen, axis=0))
            self.assertTrue(ax._surface_home_labels_fit)
        for extent in bounds[1:]:
            np.testing.assert_allclose(extent, bounds[0], rtol=.04)

    def test_wide_view_does_not_clip_surface_to_square_axes(self):
        fig, ax, xyz = self.scene()
        fit_surface_box_to_canvas(ax, *xyz, min_z_ratio=.18)
        fig.canvas.draw()
        box = self.screen_box(ax)
        self.assertLess(box[:,0].min(), ax.bbox.x0)
        self.assertGreater(box[:,0].max(), ax.bbox.x1)
        for collection in ax.collections:
            np.testing.assert_allclose(collection.get_clip_box().extents, fig.bbox.extents)

    def test_resizing_and_anisotropic_shapes_keep_labels_and_frame_visible(self):
        for width, height, ratio in ((350,220,1), (500,260,1), (1100,800,1),
                                      (800,600,.1), (800,600,10)):
            with self.subTest(size=(width,height), ratio=ratio):
                fig, ax, xyz = self.scene(width,height,ratio)
                _, zoom = fit_surface_box_to_canvas(ax, *xyz, min_z_ratio=.18)
                self.assertTrue(ax._surface_home_fits)
                self.assertTrue(ax._surface_home_labels_fit)
                _, repeated = fit_surface_box_to_canvas(ax, *xyz, min_z_ratio=.18)
                self.assertAlmostEqual(zoom, repeated, places=4)


class MainWindowFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_focus_resize_home_and_import_details_location(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        window.resize(1366, 900); window.show(); self.app.processEvents()
        self.assertFalse(hasattr(window, 'lbl_pixel_pitch'))
        x,y = np.meshgrid(np.linspace(-15,15,20), np.linspace(-15,15,20))
        z = .45 + .0001*x - .0002*y
        window.df_raw = pd.DataFrame(dict(X=x.ravel(),Y=y.ravel(),Z=z.ravel()))
        window.manual_mask = np.ones(x.size,dtype=bool)
        window.temp_selected_mask = np.zeros(x.size,dtype=bool)
        window.update_analysis()
        metrics = dict(window.last_metrics)
        window.temp_selected_mask[:] = True
        window.update_selection_overlay_only()
        np.testing.assert_allclose(window._temp_selection_overlay_artists['3D'].get_clip_box().extents,
                                   window.canvas.ax3d.figure.bbox.extents)
        for size, focused in (((1366,900),None),((1600,1000),'3D'),((1366,900),None)):
            window.canvas.set_focused_view(focused)
            window.resize(*size); self.app.processEvents()
            axis = window.canvas.ax3d
            self.assertTrue(axis._surface_home_fits)
            self.assertTrue(axis._surface_home_labels_fit)
            window.on_canvas_click(SimpleNamespace(button=1,dblclick=True,inaxes=axis,canvas=axis.figure.canvas))
            self.app.processEvents()
            self.assertTrue(axis._surface_home_fits)
        self.assertEqual(metrics, window.last_metrics)
        axis = window.canvas.ax3d
        original_limits = axis.get_xlim3d(), axis.get_ylim3d(), axis.get_zlim3d()
        before = CanvasFitTests.screen_box(axis)
        # Wheel must also work in the card whitespace outside square inaxes.
        event = SimpleNamespace(inaxes=None, canvas=axis.figure.canvas, button='up', step=1)
        window.on_canvas_scroll(event)
        axis.figure.canvas.draw()
        after = CanvasFitTests.screen_box(axis)
        self.assertGreater(np.ptp(after[:,0]), np.ptp(before[:,0]) * 1.15)
        self.assertEqual(original_limits, (axis.get_xlim3d(), axis.get_ylim3d(), axis.get_zlim3d()))
        window.on_3d_canvas_click(SimpleNamespace(button=1, dblclick=True, inaxes=None, canvas=axis.figure.canvas))
        axis.figure.canvas.draw()
        np.testing.assert_allclose(CanvasFitTests.screen_box(axis), before, atol=.1)
        before_width = window.right_stack.width()
        current_tab = window.tabs.currentIndex()
        window.btn_toggle_parameters.click(); self.app.processEvents()
        self.assertTrue(window.tabs.isHidden())
        self.assertGreater(window.right_stack.width(), before_width)
        self.assertEqual(window.btn_toggle_parameters.accessibleName(), '展开左侧参数区')
        self.assertEqual(metrics, window.last_metrics)
        window.btn_toggle_parameters.click(); self.app.processEvents()
        self.assertFalse(window.tabs.isHidden())
        self.assertEqual(window.tabs.currentIndex(), current_tab)
        self.assertEqual(metrics, window.last_metrics)
        window.import_info.update(height_matrix=True, input_layout_mode='height_matrix',
                                  sampling_pitch_x_um=10, sampling_pitch_y_um=20,
                                  sampling_pitch_source='用户手动')
        window._update_import_status_label()
        self.assertIn('X=10 / Y=20', window.lbl_import_status.text())


if __name__ == '__main__':
    unittest.main()
