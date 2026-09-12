import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.smart_roi import build_adaptive_topology
from surface_analyzer.workers import TaskCancelled


class SmartProgressTests(unittest.TestCase):
    def test_reporting_preserves_all_topology_routes(self):
        row, col = np.mgrid[:12, :15]
        x, y = col.ravel() * .04, row.ravel() * .04
        for options in ({}, {'delaunay_limit': 0},
                        {'matrix_rc': (row.ravel(), col.ravel())}):
            baseline = build_adaptive_topology(x, y, **options)
            events = []
            result = build_adaptive_topology(x, y, progress=lambda p, m: events.append((p, m)), **options)
            self.assertEqual(result['health'], baseline['health'])
            for a, b in zip(result['adjacency'], baseline['adjacency']):
                np.testing.assert_array_equal(a, b)
            values = [p for p, _ in events]
            self.assertEqual(values, sorted(values))
            self.assertEqual(values[-1], 100)
            self.assertGreater(len(set(values)), 5)

    def test_cancel_during_build_does_not_fallback_or_leak(self):
        row, col = np.mgrid[:15, :15]
        for options in ({}, {'delaunay_limit': 0},
                        {'matrix_rc': (row.ravel(), col.ravel())}):
            cancel = threading.Event()
            def progress(value, message):
                if value >= 5:
                    cancel.set()
            with self.assertRaises(TaskCancelled):
                build_adaptive_topology(col.ravel(), row.ravel(),
                                        progress=progress, cancel_event=cancel, **options)
            self.assertGreater(build_adaptive_topology(col.ravel(), row.ravel(), **options)['health']['edge_count'], 0)


class ContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_clear_roi_then_undo_deletion_restores_data(self):
        w = SurfaceAnalyzerPro()
        try:
            row, col = np.mgrid[:10, :10]
            w.df_raw = pd.DataFrame({'X': col.ravel(), 'Y': row.ravel(),
                                     'Z': 1 + col.ravel() * .001})
            w.manual_mask = np.ones(100, dtype=bool)
            w.temp_selected_mask = np.zeros(100, dtype=bool)
            w.update_analysis()
            w.on_select(SimpleNamespace(xdata=0, ydata=0),
                        SimpleNamespace(xdata=2, ydata=2), 'XY')
            menu = w._build_selection_context_menu()
            actions = {a.text(): a for a in menu.actions()}
            actions['删除选中'].trigger()
            self.assertEqual(w.manual_mask.sum(), 91)
            w.on_select(SimpleNamespace(xdata=3, ydata=3),
                        SimpleNamespace(xdata=7, ydata=7), 'XY')
            w.set_temp_selection_as_roi()
            self.assertTrue(w.roi_shapes)
            menu = w._build_selection_context_menu()
            actions = {a.text(): a for a in menu.actions()}
            actions['清空 ROI'].trigger()
            self.assertFalse(w.roi_shapes)
            self.assertFalse(w.roi_enabled)
            self.assertEqual(len(w.active_idx), 91)
            actions['撤销删点'].trigger()
            self.assertEqual(len(w.active_idx), 100)
            self.assertTrue(w.manual_mask.all())
            self.assertFalse(w._build_selection_context_menu().actions())
            w._task_thread = object()
            with patch.object(w, '_show_selection_context_menu') as popup:
                w.on_canvas_click(SimpleNamespace(button=3))
                popup.assert_not_called()
            w._task_thread = None
        finally:
            w._task_thread = None
            w.close()
