"""Data preparation inspector. All expensive I/O and expression work is queued."""
import pandas as pd
from PyQt6.QtCore import Qt, QAbstractTableModel
from PyQt6.QtGui import QColor


class PreviewModel(QAbstractTableModel):
    def __init__(self, frame, roles=None, dependencies=(), parent=None, tips=None, grid=False):
        super().__init__(parent)
        self.frame = frame
        self.roles = roles or {}
        self.dependencies = set(dependencies)
        self.tips = tips or {}
        self.grid = grid

    def rowCount(self, parent=None):
        return len(self.frame)

    def columnCount(self, parent=None):
        return len(self.frame.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = self.frame.columns[index.column()]
        value = self.frame.iat[index.row(), index.column()]
        if role == Qt.ItemDataRole.ToolTipRole and self.grid:
            return self.tips.get((self.frame.index[index.row()], column), '当前分析输入未包含此格点；可能为抽样未选中或无效点，不能确定。')
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return '' if pd.isna(value) else str(value)
        if role == Qt.ItemDataRole.BackgroundRole and column in self.roles:
            return QColor('#eaf3fb')
        if role == Qt.ItemDataRole.ForegroundRole and column in self.dependencies:
            return QColor('#286b9f')
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return str(self.frame.index[section] if self.grid else section + 1)
        column = self.frame.columns[section]
        tag = self.roles.get(column, 'ƒx 输入' if column in self.dependencies else '')
        return f'{column}  [{tag}]' if tag else str(column)


def replace_model(view, model):
    old = view.model()
    selection = view.selectionModel()
    view.setModel(model)
    if selection is not None:
        selection.deleteLater()
    if old is not None:
        old.deleteLater()


def __getattr__(name):
    if name == "DataPreviewDialog":
        from .worksheet_dialog import DataPreviewDialog
        return DataPreviewDialog
    raise AttributeError(name)
