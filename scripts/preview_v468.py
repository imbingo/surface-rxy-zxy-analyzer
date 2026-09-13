"""Real Qt screenshots of both XY modes, using synthetic axis-error scan data."""
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
out = root / 'audit_outputs'
out.mkdir(exist_ok=True)

def ready():
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        QTest.qWait(40)
        c = w.xy_raster
        if c.result is not None and c.future is None and not c.timer.isActive():
            app.processEvents()
            return
    raise RuntimeError('XY rendering timed out')

try:
    x,y = np.meshgrid(np.linspace(-15,15,301),np.linspace(-15,15,301))
    x,y = x.ravel(),y.ravel()
    rng = np.random.default_rng(468)
    x += .015*np.sin(x/5)+rng.normal(0,.001,len(x))
    y += .015*np.sin(y/4)+rng.normal(0,.001,len(y))
    keep = (x*x+y*y > 2.5**2) & ~((y>5)&(y<6))
    keep &= ((x+8)**2+(y+6)**2 > .16**2) & ((x-8)**2+(y+6)**2 > .3**2)
    x,y = x[keep],y[keep]
    z = .45 + .0001*x-.00006*y+.0003*np.sin(x/4)*np.cos(y/3)
    w.df_raw = pd.DataFrame(dict(X=x,Y=y,Z=z))
    w._df_version += 1
    w.manual_mask = np.ones(len(x),bool)
    w.temp_selected_mask = np.zeros(len(x),bool)
    w.import_info = dict(import_rows=len(x),sampled=False,mapped_finite_points=len(x))
    w.lbl_source.setText('当前数据：30×30 mm 轴组误差扫描（模拟）')
    for combo,label in ((w.cb_x_col,'X'),(w.cb_y_col,'Y'),(w.cb_z_col,'Z')):
        combo.addItem(label)
    w.cb_z_unit.setCurrentText('mm')
    w.resize(1600,900)
    w.show()
    w.update_analysis()
    ready()
    for width,height in ((1366,768),(1600,900),(1920,1080),(900,640)):
        w.resize(width,height)
        for mode in (0,1):
            w.canvas.xy_mode.setCurrentIndex(mode)
            ready()
            w.statusBar().clearMessage()
            app.processEvents()
            w.grab().save(str(out/f'v468_{width}_{mode}.png'))
    w.resize(1600,900)
    w.canvas.set_focused_view('XY')
    w.canvas.xy_mode.setCurrentIndex(0)
    ready()
    w.statusBar().clearMessage()
    app.processEvents()
    w.grab().save(str(out/'v468_focused_smallholes.png'))
    from types import SimpleNamespace
    w.on_select(SimpleNamespace(xdata=-12,ydata=-12),SimpleNamespace(xdata=-4,ydata=-9),'XY')
    app.processEvents()
    w.grab().save(str(out/'v468_selection_stable.png'))
    w.cancel_temp_selection()
    for mode in (0,1):
        w.canvas.xy_mode.setCurrentIndex(mode)
        w.canvas.ax_xy.set_xlim(-5,5)
        w.canvas.ax_xy.set_ylim(-5,5)
        ready()
        w.grab().save(str(out/f'v468_zoom_{mode}.png'))
    print('Captured both XY modes and focused zoom at four window sizes.')
finally:
    w.close()
