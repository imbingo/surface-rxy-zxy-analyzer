import os
import threading
import unittest
from collections import deque
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PyQt6.QtWidgets import QApplication
from surface_analyzer.smart_roi import build_adaptive_topology, grow_surface_roi
from surface_analyzer.smart_preview import PreviewMailbox, SmartProgressDialog
from surface_analyzer.workers import TaskCancelled


class GrowthPreviewTests(unittest.TestCase):
    def test_preview_is_observational_for_both_modes(self):
        y, x = np.mgrid[:50, :50]
        x, y = x.ravel()*.1, y.ravel()*.1
        z = .45 + .0001*x + .0002*y
        topo = build_adaptive_topology(x, y)
        for mode in ('surface_following', 'plane_residual'):
            baseline = grow_surface_roi(x, y, z, 2, 2, .01, topo, mode=mode)
            frames = []
            result = grow_surface_roi(x, y, z, 2, 2, .01, topo, mode=mode,
                                      preview=lambda mask, q, n: frames.append((mask.copy(), len(q), n)))
            np.testing.assert_array_equal(baseline, result)
            self.assertGreaterEqual(len(frames), 2)
            self.assertEqual(frames[0][0].sum(), 1)
            np.testing.assert_array_equal(frames[-1][0], result)
            self.assertEqual(frames[-1][1], 0)

    def test_latest_frame_is_bounded_independent_and_closed(self):
        x = np.arange(100000)
        box = PreviewMailbox(x, x)
        mask = x < 90000
        box.publish(mask, deque(range(5000)), 80000)
        mask[:] = False
        frame = box.take()
        self.assertTrue(frame[0].any())
        self.assertLessEqual(len(frame[0]), 12000)
        self.assertLessEqual(len(frame[1]), 1200)
        self.assertIsNone(box.take())
        box.close()
        box.publish(mask, deque(), 100000)
        self.assertIsNone(box.take())

    def test_preview_cancellation_still_raises(self):
        y, x = np.mgrid[:40, :40]
        x, y = x.ravel(), y.ravel()
        topo = build_adaptive_topology(x, y)
        cancel = threading.Event()
        with self.assertRaises(TaskCancelled):
            grow_surface_roi(x, y, np.ones(len(x)), 10, 10, .01, topo,
                             cancel_event=cancel,
                             preview=lambda *_: cancel.set())

    def test_dialog_frontier_updates_and_cancel_keeps_partial(self):
        app = QApplication.instance() or QApplication([])
        x = np.arange(100)
        box = PreviewMailbox(x, x)
        d = SmartProgressDialog(box, (0,0))
        d.show()
        box.publish(x < 50, deque([49]), 49)
        d.set_progress(60, '跟踪中')
        d.consume()
        self.assertEqual(d.preview.selected.sum(), 50)
        self.assertEqual(len(d.preview.frontier), 1)
        events = []
        d.cancelRequested.connect(lambda: events.append(True))
        d.reject(); d.reject()
        self.assertEqual(len(events), 1)
        d.finish(False, '已取消')
        self.assertNotEqual(d.rings[-1].value, 100)
        self.assertTrue(box.closed)
        d.close()
