import os
import unittest
import numpy as np
import pandas as pd

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class CompactPlotTests(unittest.TestCase):
    def test_roi_annotation_does_not_expand_data_limits(self):
        from PyQt6.QtWidgets import QApplication, QLabel
        from surface_analyzer.app import SurfaceAnalyzerPro
        app = QApplication.instance() or QApplication([])
        w = SurfaceAnalyzerPro()
        try:
            x, y = np.meshgrid(np.linspace(-15,15,101), np.linspace(-15,15,101))
            x,y = x.ravel(),y.ravel()
            z = 1.253 + .0001*x + .00001*y*y
            w.df_raw = pd.DataFrame(dict(X=x,Y=y,Z=z))
            w.lbl_source.setText('当前数据: 合成 ROI 适配验证数据（测试）')
            for combo, name in ((w.cb_x_col,'X'), (w.cb_y_col,'Y'), (w.cb_z_col,'Z')):
                combo.addItem(name)
                combo.setCurrentText(name)
            w.cb_z_unit.setCurrentText('mm')
            w._df_version += 1
            w.manual_mask = np.ones(len(x),bool)
            w.temp_selected_mask = np.zeros(len(x),bool)
            w.roi_enabled = True
            w.roi_shapes = [dict(type='rectangle',view='XZ',cx=0,cy=1.125,width=44,height=.35,enabled=True)]
            w.update_analysis()
            self.assertLess(np.ptp(w.canvas.ax_xz.get_ylim()), .02)
            self.assertLess(np.ptp(w.canvas.ax_xz.get_xlim()), 35)
            self.assertEqual(len(w.active_idx),len(x))
            self.assertEqual(w.canvas.xy_mode.count(),1)
            self.assertTrue(w.canvas.xy_mode.isHidden())
            self.assertTrue(w.canvas.xy_resolution.isHidden())
            icons = [l for l in w.findChildren(QLabel) if l.objectName() == 'poseIcon']
            self.assertEqual(len(icons),8)
            self.assertTrue(all(not label.pixmap().isNull() for label in icons))
            if os.environ.get('SURFACE_COMPACT_SCREENSHOT'):
                w.resize(1600,900)
                w.show()
                app.processEvents()
                w.statusBar().clearMessage()
                w.grab().save(os.environ['SURFACE_COMPACT_SCREENSHOT'])
        finally:
            w.close()
