import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtTest import QTest
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.import_progress import ImportProgressDialog
from surface_analyzer.recipe_library import RecipeLibrary, atomic_json, read_recipe
from surface_analyzer.recipe_dialog import RecipeLibraryDialog


class LibraryTests(unittest.TestCase):
    def test_duplicate_import_save_and_favorites_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = RecipeLibrary(Path(directory)/'library')
            recipe = {'schema_version':8, 'filter':{'mode_index':1}}
            first = lib.save(recipe, '工件 A')
            second = lib.save(recipe, '工件 A')
            self.assertNotEqual(first, second)
            self.assertEqual(read_recipe(first)['filter'], recipe['filter'])
            self.assertIn(lib.import_file(first), (first, second))
            self.assertEqual(len(lib.entries()[0]), 2)
            lib.favorite(first)
            lib.mark_used(first)
            entries, errors = RecipeLibrary(lib.root).entries()
            self.assertFalse(errors)
            self.assertTrue(entries[0]['favorite'])
            self.assertTrue(entries[0]['recent'])
            bad = Path(directory)/'future.json'
            atomic_json(bad, {'schema_version':99, 'filter':{}})
            with self.assertRaises(ValueError):
                lib.import_file(bad)
            self.assertEqual(len(lib.entries()[0]), 2)

    def test_failed_atomic_write_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'recipe.json'
            atomic_json(target, {'filter':{}})
            original = target.read_bytes()
            with patch('surface_analyzer.recipe_library.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    atomic_json(target, {'filter':{'mode_index':1}})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_progress_cancel_and_commit_boundary(self):
        d = ImportProgressDialog('demo.dat')
        signals = []
        d.cancelRequested.connect(lambda: signals.append(True))
        d.set_progress(50, '读取中')
        self.assertEqual(d.rings[0].value, 100)
        self.assertGreater(d.rings[1].value, 0)
        self.assertEqual(d.rings[2].value, 0)
        d.set_progress(20, '另一遍扫描')
        self.assertEqual(d.progress_value, 50)
        d.reject(); d.reject()
        self.assertEqual(len(signals), 1)
        d.finish(False, '已取消')
        self.assertNotEqual(d.rings[-1].value, 100)
        d.close()
        d = ImportProgressDialog('demo.dat')
        d.cancelRequested.connect(lambda: signals.append(True))
        d.begin_commit(); d.reject()
        self.assertEqual(len(signals), 1)
        d.finish(True)
        self.assertTrue(all(r.value == 100 for r in d.rings))
        d.close()

    def test_recipe_search_load_and_invalid_input_preserves_parameters(self):
        w = SurfaceAnalyzerPro()
        try:
            with tempfile.TemporaryDirectory() as directory:
                w._local_recipe_library = RecipeLibrary(directory)
                data = w._current_recipe_dict()
                data['filter']['threshold_um'] = 7.25
                path = w._recipe_library().save(data, '测试工件')
                d = RecipeLibraryDialog(w)
                d.search.setText('不存在')
                self.assertEqual(d.tree.topLevelItemCount(), 0)
                d.search.setText('测试')
                self.assertEqual(d.tree.topLevelItemCount(), 1)
                d.tree.setCurrentItem(d.tree.topLevelItem(0))
                d.load_selected()
                self.assertEqual(w.spin_thresh.value(), 7.25)
                self.assertEqual(w.current_recipe_name, '测试工件')
                broken = dict(data, filter={'threshold_um':'broken'})
                bad = Path(directory)/'bad.json'
                atomic_json(bad, broken)
                with patch.object(QMessageBox, 'critical'):
                    self.assertFalse(w.load_library_recipe(bad))
                self.assertEqual(w.spin_thresh.value(), 7.25)
                self.assertEqual(w.lbl_rx.objectName(), w.lbl_pv.objectName())
                self.assertEqual(w.lbl_ry.objectName(), w.lbl_pv.objectName())
        finally:
            w.close()

    def test_real_background_import_completes_dialog(self):
        w = SurfaceAnalyzerPro()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'demo.xyz'
                path.write_text('X Y Z\n' + '\n'.join(f'{x} {y} {1+x*.001}' for y in range(12) for x in range(12)), encoding='utf-8')
                w.show()
                self.assertTrue(w.load_path(path))
                dialog = w._import_dialog
                self.assertTrue(dialog.isVisible())
                deadline = time.monotonic()+20
                while w._task_thread is not None and time.monotonic() < deadline:
                    QTest.qWait(20)
                self.assertIsNone(w._task_thread)
                self.assertIsNone(w._import_dialog)
                self.assertIsNotNone(w.last_metrics)
                self.assertEqual(len(w.df_raw), 144)
                self.assertEqual(w.statusBar().currentMessage(), '')
        finally:
            w.close()
