import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro

class PointQueryLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_edge_queries_preserve_projection_and_limits(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        values = np.array([0., .5, 1.])
        window._configure_projection_hover(values, values, values, values,
                                           np.arange(3), np.arange(3), 'Z (mm)')
        for view in ('XY', 'XZ', 'YZ'):
            with self.subTest(view=view):
                entry = window._projection_hover[view]
                axis = entry['axis']
                axis.set_xlim(-.1, 1.1)
                axis.set_ylim(-.1, 1.1)
                for _ in range(3):
                    axis.figure.canvas.draw()
                bbox = np.array(axis.bbox.bounds)
                limits = axis.get_xlim(), axis.get_ylim()
                for index in (2, 0, 1, 2, 0):
                    pixel = axis.transData.transform((values[index], values[index]))
                    window._projection_hover_last_motion = 0
                    window.on_projection_hover(SimpleNamespace(
                        inaxes=axis, x=pixel[0], y=pixel[1], key='control'))
                    axis.figure.canvas.draw()
                    self.assertFalse(entry['annotation'].get_in_layout())
                    np.testing.assert_allclose(axis.bbox.bounds, bbox, atol=.1, rtol=0)
                    self.assertEqual((axis.get_xlim(), axis.get_ylim()), limits)
                    window._clear_projection_hover(draw=True)
                    axis.figure.canvas.draw()
                    np.testing.assert_allclose(axis.bbox.bounds, bbox, atol=.1, rtol=0)

    def test_repeating_same_query_does_not_redraw_canvas(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        values = np.array([.5])
        window._configure_projection_hover(values, values, values, values,
                                           [0], [0], 'Z (mm)')
        axis = window.canvas.ax_xy
        axis.figure.canvas.draw()
        pixel = axis.transData.transform((.5,.5))
        event = SimpleNamespace(inaxes=axis, x=pixel[0], y=pixel[1], key='control')
        window.on_projection_hover(event)
        with patch.object(axis.figure.canvas, 'draw_idle') as draw:
            window._projection_hover_last_motion = 0
            window.on_projection_hover(event)
        draw.assert_not_called()
