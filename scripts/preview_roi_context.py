"""Capture contextual ROI actions on a populated window."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import numpy as np
import pandas as pd
from PyQt6.QtCore import QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro

app = QApplication.instance() or QApplication([])
w = SurfaceAnalyzerPro()
try:
    row, col = np.mgrid[-15:15:.3, -15:15:.3]
    w.df_raw = pd.DataFrame({'X': col.ravel(), 'Y': row.ravel(),
                            'Z': .45 + .000003 * (row.ravel()**2 + col.ravel()**2)})
    w.manual_mask = np.ones(row.size, dtype=bool)
    w.temp_selected_mask = np.zeros(row.size, dtype=bool)
    w.show()
    w.update_analysis()
    w.on_select(SimpleNamespace(xdata=-14, ydata=-14), SimpleNamespace(xdata=-12, ydata=-12), 'XY')
    w.apply_manual_deletion()
    w.on_select(SimpleNamespace(xdata=-8, ydata=-5), SimpleNamespace(xdata=8, ydata=5), 'XY')
    w.set_temp_selection_as_roi()
    output = root / 'audit_outputs'
    output.mkdir(exist_ok=True)
    for width, height in ((1366,768), (1600,900), (1920,1080), (900,640)):
        w.resize(width, height)
        QTest.qWait(500)
        menu = w._build_selection_context_menu()
        menu.popup(w.mapToGlobal(QPoint(width-230, 250)))
        QTest.qWait(100)
        # Composite the actual popup on the actual window capture (offscreen).
        from PyQt6.QtGui import QPainter
        shot = w.grab()
        painter = QPainter(shot)
        painter.drawPixmap(menu.mapTo(w, QPoint(0,0)), menu.grab())
        painter.end()
        shot.save(str(output / f'v466_roi_context_{width}.png'))
        menu.close()
        menu.deleteLater()
finally:
    w.close()
