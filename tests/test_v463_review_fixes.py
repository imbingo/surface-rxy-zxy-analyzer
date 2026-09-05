import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.api import analyze_xyz
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.mixins.gap import GapAnalysisMixin


def record(x, y, z, name):
    return dict(x=x, y=y, z=z, name=name, n=len(x), offset_x=0., offset_y=0.)


class ReviewFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def gap_window(self):
        x, y = np.meshgrid(np.arange(7.), np.arange(6.))
        x, y = x.ravel(), y.ravel()
        stack = record(x, y, .8 + .001*x + .002*y + .0001*x*x, 'stack')
        base = record(x, y, np.full_like(x, .3), 'base')
        w = SurfaceAnalyzerPro()
        self.addCleanup(w.close)
        w.cb_filter.setCurrentIndex(0)
        w.data_stack, w.data_base1, w.data_base2 = stack, base, None
        payload = GapAnalysisMixin._compute_gap_payload(
            stack, base, None, .01, lambda *_: None, threading.Event())
        with patch.object(QMessageBox, 'information'):
            w._apply_gap_payload(payload)
        return w

    def test_gap_export_snapshot_survives_main_rotation_and_deletion(self):
        w = self.gap_window()
        before = w._gap_plane_metrics().copy()
        w.add_cw90()
        w.manual_mask[:14] = False
        w.update_analysis()
        self.assertNotAlmostEqual(w.last_metrics['rx'], before['rx'])
        self.assertEqual(len(w.active_idx), 28)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'gap.csv'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(path), '')), \
                    patch.object(QMessageBox, 'critical') as error:
                w.export_gap_csv()
                error.assert_not_called()
            data = pd.read_csv(path, comment='#')
            m = AnalysisMixin.compute_plane_metrics(data.X_mm.to_numpy(), data.Y_mm.to_numpy(), data.Gap_mm.to_numpy())
            headers = path.read_text(encoding='utf-8-sig')
            self.assertIn(f"# Gap_Rx_urad: {m['rx']:.9f}", headers)
            self.assertIn(f"# Gap_Ry_urad: {m['ry']:.9f}", headers)
            self.assertEqual(len(data), 42)
            self.assertEqual(w.gap_result['plane_metric_count'], 42)
            self.assertAlmostEqual(w._gap_plane_metrics()['pv'], m['pv'])
            fig = w._render_gap_report_figure()
            summary = '\n'.join(t.get_text() for ax in fig.axes for t in ax.texts)
            self.assertIn(f"Gap Rx  {m['rx']:.2f}", summary)
            fig.clear()

    def test_stale_gap_blocks_both_main_exports_before_file_dialog(self):
        for action in ('move', 'clear'):
            with self.subTest(action=action):
                w = self.gap_window()
                if action == 'move':
                    w.on_gap_layer_moved('base1', .1, 0, True)
                else:
                    w.clear_all_memory_slots()
                self.assertTrue(w.import_info['gap_result_stale'])
                with patch.object(QFileDialog, 'getSaveFileName') as dialog, \
                        patch.object(QMessageBox, 'warning') as warning:
                    w.save_file()
                    w.export_report_image()
                    dialog.assert_not_called()
                    self.assertEqual(warning.call_count, 2)

    def test_degenerate_xy_is_invalid_in_api_and_gap(self):
        x = np.arange(12.)
        for xx, y in ((x, np.zeros_like(x)), (x, 2*x), (np.zeros_like(x), np.zeros_like(x))):
            with self.assertRaisesRegex(ValueError, '共线|退化'):
                analyze_xyz(xx, y, .1+.002*x)
        stack = record(x, np.zeros_like(x), .8+.001*x, 'line')
        base = record(x, np.zeros_like(x), np.full_like(x, .3), 'line-base')
        with self.assertRaisesRegex(ValueError, '共线|退化'):
            GapAnalysisMixin._compute_gap_payload(stack, base, None, .01, lambda *_:None, threading.Event())

    def test_invalid_roi_clears_old_metrics_and_parallel_capture(self):
        w = self.gap_window()
        self.assertIsNotNone(w.last_metrics)
        w.manual_mask = w.df_raw.Y.to_numpy() == 0
        w.update_analysis()
        self.assertIsNone(w.last_metrics)
        self.assertIsNone(w.current_coeffs)
        self.assertEqual(w.high_order_models, {})
        with patch.object(QMessageBox, 'warning'), patch.object(QFileDialog, 'getSaveFileName') as dialog:
            self.assertIsNone(w._current_parallel_record())
            w.save_file()
            dialog.assert_not_called()

    def test_batch_collinear_data_does_not_emit_valid_report(self):
        w = self.gap_window()
        x = np.arange(12.)
        frame = pd.DataFrame({'X': x, 'Y': x*2, 'Z': .1+.002*x})
        params = dict(x_col='X', y_col='Y', z_col='Z', ux=1., uy=1., uz=1.,
                      pipeline=[], pipeline_text='', mode=0, k=12, threshold_mm=.005,
                      sigma_k=3., sigma_iters=5, filter_text='', display_surface_mode='raw',
                      roi_enabled=False, roi_shapes=[])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(w, '_read_table', return_value=frame), \
                patch.object(w, '_render_report_figure') as render:
            payload = w._run_batch(['line.txt'], tmp, params)
            self.assertEqual(payload['results'][0]['status'], 'fail')
            self.assertIn('共线', payload['results'][0]['error'])
            render.assert_not_called()

    def test_large_cloud_registration_is_independent_of_row_order(self):
        x, y = np.meshgrid(np.arange(400)*.1, np.arange(300)*.1)
        xy = np.column_stack((x.ravel(), y.ravel()))
        n = len(xy)
        pick = np.linspace(0, n-1, 60000, dtype=int)
        rest = np.setdiff1d(np.arange(n), pick)
        reordered = np.empty_like(xy)
        reordered[pick], reordered[rest] = xy[rest], xy[pick]
        stack = record(xy[:,0], xy[:,1], np.ones(n), 'stack')
        results = []
        for points in (xy, reordered):
            moving = record(points[:,0]+.015, points[:,1], np.ones(n), 'moving')
            result = GapAnalysisMixin._optimize_translation(stack, moving, .01)
            self.assertTrue(result['accepted'], result['reason'])
            self.assertAlmostEqual(result['offset_x'], -.015, places=9)
            self.assertAlmostEqual(result['offset_y'], 0, places=9)
            self.assertEqual(result['matched'], n)
            self.assertEqual(result['moving_points'], n)
            results.append(result)
        self.assertAlmostEqual(results[0]['overlap_rms'], results[1]['overlap_rms'], places=12)


if __name__ == '__main__':
    unittest.main()
