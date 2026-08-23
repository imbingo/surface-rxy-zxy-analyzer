"""Reusable Qt workers for long-running analyzer operations."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QEventLoop, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QProgressDialog


class TaskCancelled(RuntimeError):
    """Raised by a cooperative worker when the user cancels a task."""


class FunctionWorker(QObject):
    """Run ``fn(progress, cancel_event)`` in a QThread."""

    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.cancel_event = threading.Event()

    @pyqtSlot()
    def run(self):
        try:
            result = self._fn(self.progress.emit, self.cancel_event)
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
        except TaskCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @pyqtSlot()
    def cancel(self):
        self.cancel_event.set()


def sha256_file(path, progress=None, cancel_event=None, chunk_size=8 * 1024 * 1024):
    """Hash a file with cooperative cancellation and percentage progress."""
    source = Path(path)
    total = max(1, source.stat().st_size)
    consumed = 0
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise TaskCancelled()
            block = stream.read(chunk_size)
            if not block:
                break
            digest.update(block)
            consumed += len(block)
            if progress is not None:
                progress(min(100, int(consumed * 100 / total)), f"正在校验 {source.name}")
    if progress is not None:
        progress(100, f"校验完成 {source.name}")
    return digest.hexdigest()


def sha256_file_dialog(parent, path):
    """Compute SHA-256 off the GUI thread while showing a cancellable dialog."""
    dialog = QProgressDialog("正在计算源文件 SHA-256…", "取消", 0, 100, parent)
    dialog.setWindowTitle("文件完整性校验")
    dialog.setMinimumDuration(250)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    state = {"value": "", "error": "", "cancelled": False}
    thread = QThread(parent)
    worker = FunctionWorker(lambda progress, cancel: sha256_file(path, progress, cancel))
    worker.moveToThread(thread)
    loop = QEventLoop(parent)

    worker.progress.connect(lambda value, message: (dialog.setValue(value), dialog.setLabelText(message)))
    worker.succeeded.connect(lambda value: state.update(value=value))
    worker.failed.connect(lambda error: state.update(error=error))
    worker.cancelled.connect(lambda: state.update(cancelled=True))
    worker.finished.connect(thread.quit)
    worker.finished.connect(loop.quit)
    dialog.canceled.connect(worker.cancel)
    thread.started.connect(worker.run)
    thread.start()
    loop.exec()
    thread.wait()
    dialog.close()
    worker.deleteLater()
    thread.deleteLater()
    if state["cancelled"]:
        raise TaskCancelled()
    if state["error"]:
        raise RuntimeError(state["error"])
    return state["value"]
