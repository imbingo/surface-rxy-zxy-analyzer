"""Bounded latest-frame mailbox for Smart ROI's inline XY preview."""
from threading import Lock
import numpy as np


class PreviewMailbox:
    def __init__(self, x, y, limit=12000, visible_mask=None):
        candidates = np.arange(len(x)) if visible_mask is None else np.flatnonzero(visible_mask)
        self.indices = np.sort(np.random.default_rng(467).choice(candidates, min(len(candidates), limit), replace=False))
        self.xy = np.column_stack((x[self.indices], y[self.indices]))
        self.x, self.y = x, y
        self.lock = Lock()
        self.latest = None
        self.progress = None
        self.closed = False

    def publish(self, accepted, queue, processed):
        # No frontier geometry is copied; only gray sampled points are displayed.
        frame = (accepted[self.indices].copy(), None,
                 int(processed), int(np.count_nonzero(accepted)))
        with self.lock:
            if not self.closed:
                self.latest = frame

    def take(self):
        with self.lock:
            frame, self.latest = self.latest, None
            return frame

    def report_progress(self, value, message):
        with self.lock:
            if not self.closed:
                self.progress = (value, message)

    def take_progress(self):
        with self.lock:
            value, self.progress = self.progress, None
            return value

    def close(self):
        with self.lock:
            self.closed = True
            self.latest = self.progress = None
