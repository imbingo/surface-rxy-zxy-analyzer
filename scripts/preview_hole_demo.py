"""Capture the actual demo in the application at overview and close-up scales."""
import os
import sys
import time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from surface_analyzer.app import SurfaceAnalyzerPro


app = QApplication.instance() or QApplication([])
w = SurfaceAnalyzerPro()
try:
    w.df_raw = pd.read_csv(root / 'audit_outputs/demo_holes_30x30mm_XYZ_mm.dat', sep='\t')
    w.lbl_source.setText('当前数据: 带孔 XYZ Demo（合成测试数据）')
    for combo, column in ((w.cb_x_col,'X'),(w.cb_y_col,'Y'),(w.cb_z_col,'Z')):
        combo.addItem(column)
        combo.setCurrentText(column)
    w.cb_z_unit.setCurrentText('mm')
    w._df_version += 1
    n = len(w.df_raw)
    w.manual_mask = np.ones(n,bool)
    w.temp_selected_mask = np.zeros(n,bool)
    w.import_info = dict(import_rows=n,sampled=False,mapped_finite_points=n)
    w.resize(1600,900)
    w.show()
    w.update_analysis()
    for name, bounds in (('overview',(-16,16,-16,16)), ('hole_zoom',(-4,4,-4,4))):
        w.canvas.ax_xy.set_xlim(bounds[:2])
        w.canvas.ax_xy.set_ylim(bounds[2:])
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            QTest.qWait(50)
            c = w.xy_raster
            if c.result is not None and not c.timer.isActive() and c.future is None:
                break
        else:
            raise RuntimeError('Render timeout')
        w.statusBar().clearMessage()
        app.processEvents()
        w.grab().save(str(root / f'audit_outputs/demo_{name}.png'))
        print(name, 'scatter' if c.detail_mode else 'raster', c.result.visible_count, c.z_limits)
finally:
    w.close()
