"""Verify V4.6.7 with the large synthetic wafer and capture real UI states."""
import os
import sys
import tempfile
import time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.recipe_library import RecipeLibrary
from surface_analyzer.recipe_dialog import RecipeLibraryDialog

app = QApplication.instance() or QApplication([])
w = SurfaceAnalyzerPro()
out = root / 'audit_outputs'
try:
    w.resize(1600, 900)
    w.import_z_unit = 'mm'
    w.show()
    captured = set()
    original_progress = w._on_task_progress
    def capture_progress(value, message):
        original_progress(value, message)
        dialog = getattr(w, '_import_dialog', None)
        if dialog and 20 <= value < 88 and 'reading' not in captured:
            captured.add('reading')
            dialog.grab().save(str(out / 'v467_import_progress.png'))
    w._on_task_progress = capture_progress
    w.load_path(out / 'demo_wafer_40x40mm_4_beam_zones_XYZ_mm.dat')
    deadline = time.monotonic()+120
    while w._task_thread is not None and time.monotonic() < deadline:
        QTest.qWait(50)
    if w._task_thread is not None or not w._import_mapping_ready:
        raise RuntimeError('Large file import did not complete')
    QTest.qWait(1000)
    with tempfile.TemporaryDirectory(prefix='surface-recipe-ui-') as directory:
        w._local_recipe_library = RecipeLibrary(directory)
        lib = w._recipe_library()
        first = lib.save(w._current_recipe_dict(), '四束区 · 40 mm Wafer')
        lib.favorite(first)
        lib.mark_used(first)
        lib.save(w._current_recipe_dict(), '装调检查 · 标准参数')
        w._set_recipe_identity('四束区 · 40 mm Wafer')
        w.statusBar().clearMessage()
        for width, height in ((1366,768), (1600,900), (1920,1080), (900,640)):
            w.resize(width, height)
            QTest.qWait(350)
            w.grab().save(str(out / f'v467_main_{width}.png'))
            d = RecipeLibraryDialog(w)
            d.show()
            QTest.qWait(100)
            d.grab().save(str(out / f'v467_recipe_{width}.png'))
            d.close()
    print('Large import, metric highlights and Recipe UI captured.')
finally:
    w.close()
