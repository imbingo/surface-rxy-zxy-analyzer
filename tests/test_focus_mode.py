import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class FocusModeTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        from surface_analyzer.app import SurfaceAnalyzerPro
        self.app = QApplication.instance() or QApplication([])
        self.window = SurfaceAnalyzerPro()
        self.window.resize(1366, 768)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()

    def test_default_3d_camera_uses_engineering_45_degree_azimuth(self):
        canvas = self.window.canvas
        self.assertEqual(canvas.ax3d.elev, 30.0)
        self.assertEqual(canvas.ax3d.azim, -45.0)

    def test_each_title_button_focuses_only_its_card(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        canvas = self.window.canvas
        for view in ('3D', 'XY', 'XZ', 'YZ'):
            button = canvas.focus_buttons[view]
            self.assertEqual(button.accessibleName(), f'放大 {view} 视图')
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(canvas.focused_view, view)
            self.assertTrue(self.window.tabs.isVisible())
            self.assertTrue(self.window._results_strip.isHidden())
            self.assertEqual([key for key, card in canvas._card_by_view.items()
                              if not card.isHidden()], [view])
            self.assertEqual(button.accessibleName(), '还原四视图')
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertIsNone(canvas.focused_view)
            self.assertFalse(self.window._results_strip.isHidden())
            self.assertTrue(all(not card.isHidden() for card in canvas._cards))

    def test_escape_tab_change_and_double_click_contract(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        canvas = self.window.canvas
        canvas.ax_xy.set_xlim(-3, 4)
        canvas.ax_xy.set_ylim(-2, 5)
        canvas.ax3d.view_init(elev=31, azim=-42, roll=3)
        limits = canvas.ax_xy.get_xlim(), canvas.ax_xy.get_ylim()
        camera = canvas.ax3d.elev, canvas.ax3d.azim, canvas.ax3d.roll
        canvas.set_focused_view('XY')
        self.app.processEvents()
        self.assertEqual((canvas.ax_xy.get_xlim(), canvas.ax_xy.get_ylim()), limits)
        self.assertEqual((canvas.ax3d.elev, canvas.ax3d.azim, canvas.ax3d.roll), camera)
        # Existing double-click remains a view reset and cannot toggle focus.
        self.window._plot_home_limits = {'XY': ((-10,10),(-9,9))}
        event = SimpleNamespace(button=1, dblclick=True, inaxes=canvas.ax_xy,
                                canvas=canvas.ax_xy.figure.canvas)
        self.window.on_canvas_click(event)
        self.assertEqual(canvas.focused_view, 'XY')
        self.assertEqual(canvas.ax_xy.get_xlim(), (-10,10))
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertIsNone(canvas.focused_view)
        canvas.set_focused_view('XZ')
        self.window.tabs.setCurrentIndex(self.window.parallel_tab_index)
        self.app.processEvents()
        self.assertIsNone(canvas.focused_view)
        self.assertFalse(self.window._results_strip.isHidden())

    def test_invalid_view_is_rejected_without_state_change(self):
        with self.assertRaises(ValueError):
            self.window.canvas.set_focused_view('BAD')
        self.assertIsNone(self.window.canvas.focused_view)

    def test_plot_refresh_preserves_focus_and_measurement(self):
        import copy
        import numpy as np
        import pandas as pd
        x, y = np.meshgrid(np.linspace(-2,2,21), np.linspace(-3,3,25))
        x, y = x.ravel(), y.ravel()
        z = .45 + .0001*x - .0002*y + .00001*x*y
        self.window.df_raw = pd.DataFrame(dict(X=x,Y=y,Z=z))
        self.window._df_version += 1
        self.window.manual_mask = np.ones(len(x), dtype=bool)
        self.window.temp_selected_mask = np.zeros(len(x), dtype=bool)
        self.window.update_analysis()
        metrics = copy.deepcopy(self.window.last_metrics)
        active = self.window.active_idx.copy()
        self.window.canvas.set_focused_view('YZ')
        self.window.update_plots_only()
        self.assertEqual(self.window.canvas.focused_view, 'YZ')
        np.testing.assert_array_equal(self.window.active_idx, active)
        for key, value in metrics.items():
            np.testing.assert_equal(self.window.last_metrics[key], value)


if __name__ == '__main__':
    unittest.main()
