import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.extension_dialog import ExtensionDialog


class ModuleHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'SurfaceRxyZxyAnalyzer' / 'module_extensions'
        self.root.mkdir(parents=True)
        self.localdata_patcher = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.localdata_patcher.start()

    def tearDown(self):
        self.localdata_patcher.stop()
        self.temp.cleanup()

    def _write_extension(self, extension_id, source, **updates):
        folder = self.root / extension_id
        folder.mkdir()
        manifest = {
            'id': extension_id,
            'name': extension_id,
            'version': '1.0.0',
            'api_version': 1,
            'minimum_app_version': '5.0.0',
            'entrypoint': '__init__.py',
        }
        manifest.update(updates)
        (folder / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
        (folder / '__init__.py').write_text(source, encoding='utf-8')
        return folder

    @staticmethod
    def _working_source():
        return (
            'from PyQt6.QtGui import QIcon\n'
            'from PyQt6.QtWidgets import QLabel, QWidget\n'
            'class Module:\n'
            '    key = "test-module"\n'
            '    label = "TEST"\n'
            '    caption = "Test module"\n'
            '    export_label = "Export TEST"\n'
            '    uses_export_origin = True\n'
            '    def __init__(self, host): self.host = host; self.icon_colors = []\n'
            '    def icon(self, color): self.icon_colors.append(color); return QIcon()\n'
            '    def build_sidebar(self, parent): return QLabel("sidebar", parent)\n'
            '    def build_panel(self): return QWidget()\n'
            '    def export_actions(self): return [("Export", lambda: None, False)]\n'
            '    def status_text(self): return "test status"\n'
            '    def on_idle(self): pass\n'
            'def create_module(host): return Module(host)\n'
        )

    def _new_window(self):
        return SurfaceAnalyzerPro(settings=None)

    def test_no_extension_keeps_three_builtin_modules_and_export_chrome(self):
        window = self._new_window()
        try:
            self.assertEqual(window.tabs.count(), 3)
            self.assertEqual(len(window.module_buttons), 3)
            self.assertEqual(window.module_caption.text(), '单层分析')
            window._refresh_module_export_menu()
            single_actions = [action.text() for action in window.btn_export_report.menu().actions()]
            self.assertEqual(single_actions[-1], '导出坐标按有效点左下角归零')
            window.tabs.setCurrentIndex(1)
            self.assertEqual(window.module_caption.text(), '膜厚分析')
            window._refresh_module_export_menu()
            self.assertNotIn(
                '导出坐标按有效点左下角归零',
                [action.text() for action in window.btn_export_report.menu().actions()])
            window.tabs.setCurrentIndex(2)
            self.assertEqual(window.module_caption.text(), '平行度分析')
            self.assertEqual(window.right_stack.currentIndex(), 1)
        finally:
            window.close()
            window.deleteLater()
            self.qt.processEvents()

    def test_valid_module_adds_fourth_navigation_item_and_manager_row(self):
        self._write_extension('test-module', self._working_source())
        window = self._new_window()
        try:
            self.assertEqual(window.tabs.count(), 4)
            self.assertEqual(len(window.module_buttons), 4)
            window.module_buttons[3].click()
            self.qt.processEvents()
            self.assertTrue(window.module_buttons[3].isCheckable())
            self.assertEqual(
                [index for index, button in enumerate(window.module_buttons)
                 if button.isChecked()],
                [3])
            self.assertEqual(
                window._extension_modules[0].icon_colors[-1], '#286b9f')
            self.assertEqual(window.module_caption.text(), 'Test module')
            self.assertEqual(window.right_stack.currentIndex(), 3)
            dialog = ExtensionDialog(
                window, module_registry=window.module_extension_registry)
            try:
                self.assertEqual(dialog.tree.topLevelItemCount(), 1)
                self.assertEqual(dialog.tree.topLevelItem(0).text(1), '模块')
                self.assertEqual(dialog.tree.topLevelItem(0).text(4), '可用')
            finally:
                dialog.close()
        finally:
            window.close()
            window.deleteLater()
            self.qt.processEvents()

    def test_invalid_import_factory_build_disabled_and_version_are_isolated(self):
        invalid = self.root / 'invalid-module'
        invalid.mkdir()
        (invalid / 'extension.json').write_text('{', encoding='utf-8')
        self._write_extension('import-module', 'import missing_for_extension_test\n')
        self._write_extension(
            'factory-module',
            'def create_module(host): raise RuntimeError("factory error")\n')
        self._write_extension(
            'build-module',
            'from PyQt6.QtWidgets import QWidget\n'
            'class Module:\n'
            ' key="build-module"; label="BUILD"; caption="Build"; export_label="Build"\n'
            ' def __init__(self, host): pass\n'
            ' def icon(self, color): return None\n'
            ' def build_sidebar(self, parent): raise RuntimeError("build error")\n'
            ' def build_panel(self): return QWidget()\n'
            ' def export_actions(self): return []\n'
            ' def status_text(self): return ""\n'
            ' def on_idle(self): pass\n'
            'def create_module(host): return Module(host)\n')
        disabled = self._write_extension('disabled-module', self._working_source())
        (disabled / 'disabled').touch()
        self._write_extension(
            'newer-module', self._working_source().replace('test-module', 'newer-module'),
            minimum_app_version='6.0.0')

        window = self._new_window()
        try:
            self.assertEqual(window.tabs.count(), 3)
            self.assertEqual(len(window.module_buttons), 3)
            dialog = ExtensionDialog(
                window, module_registry=window.module_extension_registry)
            try:
                statuses = {
                    dialog.tree.topLevelItem(i).text(0):
                    dialog.tree.topLevelItem(i).text(4)
                    for i in range(dialog.tree.topLevelItemCount())
                }
                self.assertIn('invalid-module', statuses)
                self.assertIn('missing_for_extension_test', statuses['import-module'])
                self.assertIn('factory error', statuses['factory-module'])
                self.assertIn('build error', statuses['build-module'])
                self.assertEqual(statuses['disabled-module'], '已禁用')
                self.assertIn('升级主程序', statuses['newer-module'])
            finally:
                dialog.close()
        finally:
            window.close()
            window.deleteLater()
            self.qt.processEvents()


if __name__ == '__main__':
    unittest.main()
