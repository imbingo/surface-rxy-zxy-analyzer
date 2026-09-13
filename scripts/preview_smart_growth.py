"""Exercise actual background growth and capture its intermediate preview."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QEventLoop, QTimer
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.smart_preview import SmartProgressDialog, PreviewMailbox
from surface_analyzer.smart_roi import grow_surface_roi

app = QApplication.instance() or QApplication([])
w = SurfaceAnalyzerPro()
out = root / 'audit_outputs'
captured = []
consume = SmartProgressDialog.consume
def capture(d):
    consume(d)
    selected = int(d.preview.selected.sum())
    if selected > 100 and len(d.preview.frontier) and not captured:
        d.grab().save(str(out/'v467_smart_growth.png'))
        captured.append(selected)
try:
    row, col = np.mgrid[:401, :501]
    x, y = col.ravel()*.06, row.ravel()*.06
    z = .45 + .0001*x + .00008*y + .000001*(x*x+y*y)
    w.df_raw = pd.DataFrame(dict(X=x, Y=y, Z=z, _matrix_row=row.ravel(), _matrix_col=col.ravel()))
    w.manual_mask = np.ones(len(x), dtype=bool)
    w.temp_selected_mask = np.zeros(len(x), dtype=bool)
    w.resize(1366,768)
    w.show()
    w.update_analysis()
    # Match the operator workflow: finish initial plot rendering before clicking a seed.
    QTest.qWait(1000)
    with patch.object(SmartProgressDialog, 'consume', capture):
        w.add_smart_face_roi_from_seed(15,12)
        loop = QEventLoop()
        watchdog = QTimer()
        watchdog.timeout.connect(lambda: loop.quit() if w._task_thread is None else None)
        watchdog.start(100)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(120000)
        loop.exec()
        watchdog.stop()
        timeout.stop()
        if w._task_thread is not None or not w.roi_shapes or not captured:
            details = [(d.detail.text(), d.progress_value) for d in w.findChildren(SmartProgressDialog)]
            raise RuntimeError(f'Growth or intermediate preview failed: ROI={len(w.roi_shapes)}, captures={captured}, dialogs={details}')
    print(f'Actual background growth: {len(x):,} points, ROI={w.roi_shapes[-1]["point_count_at_create"]:,}; captured={captured}')
    topology = next(iter(w._smart_topology_cache.values()))['topology']
    box = PreviewMailbox(x,y)
    results = []
    for observer in (None, box.publish):
        started = time.perf_counter()
        mask = grow_surface_roi(x,y,z,15,12,.02,topology,preview=observer)
        results.append(mask)
        print(f'preview={observer is not None}: {time.perf_counter()-started:.3f}s')
    np.testing.assert_array_equal(*results)
    # Reopen a frozen final snapshot solely for multi-size layout inspection.
    for width, height in ((1366,768),(1600,900),(1920,1080),(900,640)):
        w.resize(width,height)
        mailbox = PreviewMailbox(x,y)
        d = SmartProgressDialog(mailbox,(15,12),w)
        d.set_progress(92,'曲面跟踪已完成，正在整理 ROI')
        mailbox.publish(results[-1],[],len(x))
        d.consume()
        d.show()
        QTest.qWait(80)
        d.grab().save(str(out/f'v467_smart_dialog_{width}.png'))
        d.finish(True)
finally:
    w.close()
