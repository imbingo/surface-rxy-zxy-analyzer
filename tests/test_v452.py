import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro


class V452RoiInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _rect(view, cx, cy, width, height):
        return {'type': 'rect', 'view': view, 'cx': cx, 'cy': cy,
                'width': width, 'height': height, 'enabled': True}

    def test_manual_roi_union_by_view_and_intersection_across_views(self):
        window = SurfaceAnalyzerPro()
        self.assertFalse(hasattr(window, 'btn_smart_gate'))
        self.assertFalse(hasattr(window, 'btn_clear_smart_gates'))
        x = np.array([0.0, 0.5, 1.0, 2.0])
        y = np.array([0.0, 1.0, 2.0, 3.0])
        z = np.array([0.0, 0.0, 1.0, 2.0])
        yz1 = self._rect('YZ', 0.5, 0.0, 1.2, 0.2)
        yz2 = self._rect('YZ', 2.5, 1.5, 1.2, 1.2)
        keep_yz = window._roi_keep_mask_for_arrays(x, y, z, [yz1, yz2], True)
        self.assertTrue(np.array_equal(keep_yz, np.array([True, True, True, True])))

        xz = self._rect('XZ', 0.5, 0.5, 1.2, 1.2)
        keep_combined = window._roi_keep_mask_for_arrays(
            x, y, z, [yz1, yz2, xz], True)
        self.assertTrue(np.array_equal(
            keep_combined, np.array([True, True, True, False])))
        window.close()

    def test_smart_and_yz_roi_drive_effective_and_plot_indices(self):
        window = SurfaceAnalyzerPro()
        x = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
        y = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
        z = np.array([1.00, 1.01, 1.02, 1.03, 1.04, 1.05])
        smart = np.array([True, True, True, True, True, False])
        smart_roi = {'type': 'smart_face', 'id': 1, 'enabled': True,
                     'seed_x': 0.0, 'seed_y': 0.0, 'seed_z': 1.0,
                     'smart_algorithm_version': 3}
        yz = self._rect('YZ', 1.0, 1.02, 2.2, 0.025)
        window.roi_shapes = [smart_roi, yz]
        window.roi_enabled = True
        window.df_raw = pd.DataFrame({'Z': z, 'X': x, 'Y': y})
        window.manual_mask = np.ones(len(x), dtype=bool)
        window.temp_selected_mask = np.zeros(len(x), dtype=bool)

        with patch.object(window, '_smart_face_keep_mask_for_arrays', return_value=smart):
            effective = window._roi_keep_mask_for_arrays(x, y, z)
            expected = smart & np.array([False, True, True, True, False, False])
            self.assertTrue(np.array_equal(effective, expected))
            window.active_idx = np.flatnonzero(effective)
            window.draw_plots(x, y, z, roi_mask_all=effective)

        self.assertTrue(np.array_equal(window._last_xy_plot_indices, np.arange(len(x))))
        self.assertTrue(np.array_equal(window._last_roi_plot_indices, np.flatnonzero(expected)))
        self.assertTrue(np.array_equal(window._last_detail_plot_indices, np.flatnonzero(expected)))
        window.close()

    def test_xy_is_manual_overview_without_active_roi(self):
        window = SurfaceAnalyzerPro()
        x = np.arange(5, dtype=float)
        y = np.arange(5, dtype=float)
        z = np.linspace(1.0, 1.04, 5)
        window.manual_mask = np.array([True, True, False, True, True])
        window.active_idx = np.array([0, 3])
        window.roi_enabled = False
        window.draw_plots(x, y, z)
        self.assertTrue(np.array_equal(window._last_xy_plot_indices, np.array([0, 1, 3, 4])))
        self.assertTrue(np.array_equal(window._last_detail_plot_indices, np.array([0, 3])))
        window.close()

    def test_circle_roi_is_not_available_in_current_ui(self):
        window = SurfaceAnalyzerPro()
        self.assertEqual(
            [window.cb_roi_shape.itemText(i) for i in range(window.cb_roi_shape.count())],
            ['矩形 ROI', '智能抓面'])
        self.assertFalse(hasattr(window, 'spin_roi_r'))
        window.close()

    def test_selection_actions_delete_roi_and_cancel(self):
        window = SurfaceAnalyzerPro()
        x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        y = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        z = np.array([1.0, 1.01, 1.02, 1.03, 1.04])
        window.df_raw = pd.DataFrame({'Z': z, 'X': x, 'Y': y})
        window.manual_mask = np.ones(len(x), dtype=bool)
        window.temp_selected_mask = np.zeros(len(x), dtype=bool)
        window.active_idx = np.arange(len(x))
        window.selection_mode = 'delete'

        start = SimpleNamespace(xdata=0.5, ydata=1.005)
        end = SimpleNamespace(xdata=2.5, ydata=1.025)
        window.on_select(start, end, 'XZ')
        self.assertGreater(int(window.temp_selected_mask.sum()), 0)
        window.cancel_temp_selection()
        self.assertEqual(int(window.temp_selected_mask.sum()), 0)
        self.assertTrue(np.all(window.manual_mask))

        window.cb_roi_shape.setCurrentIndex(1)
        window.on_select(start, end, 'XZ')
        window.set_temp_selection_as_roi()
        self.assertEqual(window.roi_shapes[-1]['type'], 'rect')
        self.assertEqual(window.roi_shapes[-1]['view'], 'XZ')
        self.assertEqual(int(window.temp_selected_mask.sum()), 0)

        window.clear_rois(update=False)
        window.roi_enabled = False
        window.active_idx = np.arange(len(x))
        window.on_select(SimpleNamespace(xdata=0.5, ydata=0.5),
                         SimpleNamespace(xdata=2.5, ydata=2.5), 'XY')
        selected = window.temp_selected_mask.copy()
        window.apply_manual_deletion()
        self.assertTrue(np.array_equal(window.manual_mask, ~selected))
        self.assertEqual(len(window.manual_delete_operations), 1)
        window.close()


if __name__ == '__main__':
    unittest.main()
