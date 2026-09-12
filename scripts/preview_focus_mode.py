"""Capture the V4.6.6 single-view focus interaction with synthetic hole data."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

import numpy as np
import pandas as pd
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro


def wait_xy(window):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        QTest.qWait(50)
        controller = window.xy_raster
        if (controller.result is not None and not controller.timer.isActive()
                and controller.future is None):
            return
    raise RuntimeError('XY render timeout')


def main():
    source = root / 'audit_outputs' / 'demo_wafer_40x40mm_4_beam_zones_XYZ_mm.dat'
    if not source.exists():
        from generate_beam_zone_demo import main as generate
        generate()
    app = QApplication.instance() or QApplication([])
    window = SurfaceAnalyzerPro()
    try:
        window.df_raw = pd.read_csv(source, sep='\t')
        window.lbl_source.setText('当前数据: 40mm Wafer 四束区孔 Demo（合成测试数据）')
        for combo, column in ((window.cb_x_col, 'X'), (window.cb_y_col, 'Y'),
                              (window.cb_z_col, 'Z')):
            combo.addItem(column)
            combo.setCurrentText(column)
        window.cb_z_unit.setCurrentText('mm')
        window._df_version += 1
        count = len(window.df_raw)
        window.manual_mask = np.ones(count, dtype=bool)
        window.temp_selected_mask = np.zeros(count, dtype=bool)
        window.import_info = dict(import_rows=count, sampled=False,
                                  mapped_finite_points=count)
        window.resize(1600, 900)
        window.show()
        window.update_analysis()
        wait_xy(window)
        output = root / 'audit_outputs'
        window.statusBar().clearMessage()
        app.processEvents()
        window.grab().save(str(output / 'v466_four_view_1600.png'))
        for view in ('3D', 'XY', 'XZ', 'YZ'):
            window.canvas.set_focused_view(view)
            QTest.qWait(350)
            if view == 'XY':
                wait_xy(window)
            window.statusBar().clearMessage()
            app.processEvents()
            window.grab().save(str(output / f'v466_focus_{view.lower()}_1600.png'))
            window.canvas.set_focused_view(None)
        window.canvas.set_focused_view('XY')
        for width, height in ((1366, 768), (1920, 1080), (900, 640)):
            window.resize(width, height)
            QTest.qWait(350)
            wait_xy(window)
            window.statusBar().clearMessage()
            app.processEvents()
            window.grab().save(str(output / f'v466_focus_xy_{width}.png'))
        window.resize(1600, 900)
        window.canvas.ax_xy.set_xlim(-1.2, 1.2)
        window.canvas.ax_xy.set_ylim(2.7, 5.2)
        wait_xy(window)
        window.statusBar().clearMessage()
        app.processEvents()
        window.grab().save(str(output / 'v466_focus_xy_holes_zoom.png'))
    finally:
        window.close()


if __name__ == '__main__':
    main()
