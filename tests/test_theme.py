import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from matplotlib.colors import to_hex
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QLabel

from surface_analyzer import app as app_module
from surface_analyzer import theme


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "theme.ini"), QSettings.Format.IniFormat)
        patcher = patch.object(app_module, "QSettings", lambda *a, **k: self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = app_module.SurfaceAnalyzerPro()

    def tearDown(self):
        self.window.set_theme_preference("light", persist=False)
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.temp.cleanup()

    def test_theme_button_sits_at_bottom_of_module_rail(self):
        layout = self.window.module_rail.layout()
        self.assertIs(layout.itemAt(layout.count() - 1).widget(), self.window.btn_theme)
        labels = [action.text() for action in self.window.btn_theme.menu().actions()]
        self.assertEqual(labels, ["浅色", "深色", "跟随系统"])

    def test_choice_persists_and_round_trips_stylesheets(self):
        light_qss = self.window.styleSheet()
        label = QLabel("x", self.window)
        label.setStyleSheet("color: #b42318; background: #ffffff;")
        self.window.set_theme_preference("dark")
        self.assertEqual(self.settings.value(theme.THEME_SETTINGS_KEY), "dark")
        self.assertTrue(theme.is_dark())
        self.assertIn(theme.WORKSPACE, self.window.styleSheet())
        self.assertIn(theme.FAIL, label.styleSheet())
        self.assertTrue(self.window.theme_actions["dark"].isChecked())
        label.setStyleSheet("color: #15803d;")
        self.qt.processEvents()
        self.assertIn(theme.PASS, label.styleSheet())
        self.window.set_theme_preference("light")
        self.assertEqual(self.window.styleSheet(), light_qss)
        self.assertEqual(label.styleSheet(), "color: #15803d;")
        self.assertEqual(self.settings.value(theme.THEME_SETTINGS_KEY), "light")

    def test_saved_choice_is_restored_on_start(self):
        self.settings.setValue(theme.THEME_SETTINGS_KEY, "dark")
        second = app_module.SurfaceAnalyzerPro()
        try:
            self.assertTrue(theme.is_dark())
            self.assertTrue(second.theme_actions["dark"].isChecked())
        finally:
            second.close()
            second.deleteLater()

    def test_system_choice_follows_platform_scheme(self):
        for scheme in ("dark", "light"):
            with patch.object(theme.ThemeManager, "system_mode", staticmethod(lambda value=scheme: value)):
                self.window.set_theme_preference("system")
                self.assertEqual(theme.manager().mode, scheme)
                self.assertTrue(self.window.theme_actions["system"].isChecked())

    def test_plot_chrome_follows_theme_but_colormap_and_reports_do_not(self):
        canvas = self.window.canvas._canvases[0]
        axis = canvas.figure.axes[0]
        light_face = to_hex(canvas.figure.get_facecolor())
        self.window.set_theme_preference("dark")
        canvas.draw()
        self.assertEqual(to_hex(canvas.figure.get_facecolor()), theme.CARD)
        self.assertEqual(to_hex(axis.title.get_color()) if axis.title.get_text() else theme.TEXT, theme.TEXT)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        report = Figure()
        FigureCanvasAgg(report)
        report.add_subplot(111).imshow([[0, 1]], cmap="turbo")
        report.canvas.draw()
        self.assertEqual(to_hex(report.get_facecolor()), "#ffffff")
        self.assertEqual(report.axes[0].images[0].get_cmap().name, "turbo")
        self.window.set_theme_preference("light")
        canvas.draw()
        self.assertEqual(to_hex(canvas.figure.get_facecolor()), light_face)


if __name__ == "__main__":
    unittest.main()
