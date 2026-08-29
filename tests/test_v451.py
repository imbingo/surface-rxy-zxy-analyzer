import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
import surface_analyzer.mixins.roi as roi_module


class V451CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def _write_bow_surface(path):
        yy, xx = np.mgrid[0:24, 0:32]
        x = xx.ravel() * 0.08
        y = yy.ravel() * 0.10
        z = 1.0 + 0.0015 * (x - np.mean(x)) ** 2 + 0.001 * (y - np.mean(y)) ** 2
        lines = ['X,Y,Z']
        lines.extend(f'{vx:.8f},{vy:.8f},{vz:.10f}' for vx, vy, vz in zip(x, y, z))
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    @staticmethod
    def _roi(window, seed_x, seed_y):
        tx, ty, tz = window.get_final_transformed_data(window.df_raw)
        seed = int(np.argmin((tx - seed_x) ** 2 + (ty - seed_y) ** 2))
        return {
            'type': 'smart_face',
            'seed_x': float(tx[seed]), 'seed_y': float(ty[seed]), 'seed_z': float(tz[seed]),
            'z_tolerance_mm': 0.01,
            'smart_algorithm_version': 2,
            'smart_mode': 'surface_following',
            'sensitivity': 'standard',
            'connectivity': 'auto_xy',
            'xy_radius_mm': 0.0,
        }

    def test_smart_roi_build_and_grow_are_not_repeated_by_ui_operations(self):
        real_build = roi_module.build_adaptive_topology
        real_grow = roi_module.grow_surface_roi
        calls = {'build': 0, 'grow': 0}

        def counted_build(*args, **kwargs):
            calls['build'] += 1
            return real_build(*args, **kwargs)

        def counted_grow(*args, **kwargs):
            calls['grow'] += 1
            return real_grow(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(roi_module, 'build_adaptive_topology', side_effect=counted_build), \
                patch.object(roi_module, 'grow_surface_roi', side_effect=counted_grow):
            path = Path(directory) / 'bow.csv'
            self._write_bow_surface(path)
            window = SurfaceAnalyzerPro()
            self.assertTrue(window.load_path(path))
            tx, ty, tz = window.get_final_transformed_data(window.df_raw)
            matrix_rc = window._matrix_rc_for_current_data()

            roi1 = self._roi(window, float(np.mean(tx)), float(np.mean(ty)))
            first_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, roi1, matrix_rc=matrix_rc)
            self.assertEqual(calls, {'build': 1, 'grow': 1})
            window._complete_smart_face_roi(
                roi1, first_mask, tx, ty, tz, matrix_rc,
                topology_key=window._smart_topology_cache_key(
                    tx, ty, tz, matrix_rc, roi1['sensitivity']))
            self.assertEqual(calls, {'build': 1, 'grow': 1})

            cached_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, window.roi_shapes[0], matrix_rc=matrix_rc)
            self.assertTrue(np.array_equal(first_mask, cached_mask))

            window.on_select(SimpleNamespace(xdata=float(tx.min()), ydata=float(ty.min())),
                             SimpleNamespace(xdata=float(np.percentile(tx, 5)),
                                             ydata=float(np.percentile(ty, 5))), 'XY')
            if int(window.temp_selected_mask.sum()) > 0:
                window.apply_manual_deletion()
                window.undo_manual_deletion()
            for mode in ('raw', 'residual_1', 'residual_2', 'residual_3'):
                index = window.cb_surface_display.findData(mode)
                window.cb_surface_display.setCurrentIndex(index)
                window._on_surface_display_changed()
            window.update_plots_only()
            window._refresh_roi_ui(update=False)
            window.tabs.setCurrentIndex(min(1, window.tabs.count() - 1))
            QApplication.processEvents()
            self.assertEqual(calls, {'build': 1, 'grow': 1})

            roi2 = self._roi(window, float(np.percentile(tx, 65)), float(np.percentile(ty, 55)))
            second_mask = window._smart_face_keep_mask_for_arrays(
                tx, ty, tz, roi2, matrix_rc=matrix_rc)
            self.assertEqual(calls['build'], 1)
            self.assertEqual(calls['grow'], 2)
            window._complete_smart_face_roi(
                roi2, second_mask, tx, ty, tz, matrix_rc,
                topology_key=window._smart_topology_cache_key(
                    tx, ty, tz, matrix_rc, roi2['sensitivity']))
            self.assertEqual(calls, {'build': 1, 'grow': 2})
            window.close()


if __name__ == '__main__':
    unittest.main()
