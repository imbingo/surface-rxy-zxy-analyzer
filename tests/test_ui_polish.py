import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro


class UiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_empty_workspace_prompts_to_open_file(self):
        w = self.window
        self.assertTrue(w.empty_state.isVisible())
        self.assertEqual(w.btn_open.text(), "打开文件")

    def test_status_bar_follows_active_module(self):
        w = self.window
        w.tabs.setCurrentIndex(w.math_tab_index)
        self.app.processEvents()
        self.assertTrue(w.lbl_module_status.isVisible())
        self.assertFalse(w.lbl_import_status.isVisible())
        self.assertTrue(w.lbl_module_status.text().startswith("膜厚"))
        w.tabs.setCurrentIndex(w.parallel_tab_index)
        self.app.processEvents()
        self.assertTrue(w.lbl_module_status.text().startswith("平行度"))
        w.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assertFalse(w.lbl_module_status.isVisible())
        self.assertTrue(w.lbl_import_status.isVisible())


if __name__ == '__main__':
    unittest.main()
