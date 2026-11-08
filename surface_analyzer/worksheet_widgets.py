"""Qt worksheet presentation, selection references and fill-handle gestures."""
from PyQt6.QtCore import Qt, QAbstractTableModel, pyqtSignal, QRect
from PyQt6.QtGui import QColor, QPainter, QKeySequence, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QTableView, QAbstractItemView, QPlainTextEdit
import pandas as pd
from .worksheet import letters, ERRORS, CELL

COLORS = ['#2878bd', '#c47925', '#8a57bb', '#258a73']


class ReferenceHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        seen = {}
        for match in CELL.finditer(text):
            key = (match[2].upper(), match[4])
            i = seen.setdefault(key,len(seen))
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(COLORS[i % len(COLORS)]))
            self.setFormat(match.start(), match.end()-match.start(), fmt)


class FormulaEdit(QPlainTextEdit):
    submitted = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMaximumHeight(38)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.highlighter = ReferenceHighlighter(self.document())

    def text(self):
        return self.toPlainText()

    def setText(self, value):
        self.setPlainText(value)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit(); return
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); return
        super().keyPressEvent(event)


class WorksheetModel(QAbstractTableModel):
    def __init__(self, sheet, positions, states, roles, parent=None):
        super().__init__(parent)
        self.sheet, self.positions, self.states, self.roles = sheet, positions, states, roles
        self.references = []

    def rowCount(self, parent=None):
        return len(self.positions)

    def columnCount(self, parent=None):
        return len(self.sheet.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = int(self.positions[index.row()])
        col = self.sheet.columns[index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole:
                return (f"记录标识 {self.sheet.base.index[row]} · {self.states.iloc[row]}\n"
                        f"{self.sheet.formula(col['id'], row) if col.get('source') is None else '原始列（只读）'}")
            if col.get('source') is not None:
                value = self.sheet.base.iloc[row][col['source']]
                return '' if pd.isna(value) else str(value)
            if col['id'] not in self.sheet.values:
                return ''
            error = int(self.sheet.errors[col['id']][row])
            if error:
                return '' if error == 1 else ERRORS[error]
            return f"{self.sheet.values[col['id']][row]:.12g}"
        if role == Qt.ItemDataRole.BackgroundRole:
            for i, (r, c) in enumerate(self.references):
                if r == row and c == index.column():
                    color = QColor(COLORS[i % len(COLORS)])
                    color.setAlpha(40)
                    return color
            if col['name'] in self.roles:
                return QColor('#eaf3fb')
        if role == Qt.ItemDataRole.ForegroundRole and col.get('source') is None:
            if col['id'] in self.sheet.errors and self.sheet.errors[col['id']][row] > 1:
                return QColor('#b74436')
            return QColor('#286b9f')
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Vertical:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(int(self.positions[section])+2)
            if role == Qt.ItemDataRole.BackgroundRole:
                state = self.states.iloc[int(self.positions[section])]
                return QColor('#e6f2eb' if state == '参与计算' else '#f1f2f4')
            if role == Qt.ItemDataRole.ToolTipRole:
                row = int(self.positions[section])
                return f'记录标识 {self.sheet.base.index[row]} · {self.states.iloc[row]}'
        elif role == Qt.ItemDataRole.DisplayRole:
            col = self.sheet.columns[section]
            tag = self.roles.get(col['name'], 'ƒx' if col.get('source') is None else '')
            return f"{letters(section)}  {col['name']}"+(f' [{tag}]' if tag else '')
        return None


class WorksheetView(QTableView):
    editRequested = pyqtSignal(object, str)
    referenceRequested = pyqtSignal(object)
    fillRequested = pyqtSignal(int, object, object)
    fillAllRequested = pyqtSignal(object)
    undoRequested = pyqtSignal()
    redoRequested = pyqtSignal()
    fillSelectionRequested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.formula_mode = False
        self.drag_fill = None
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)

    def handle_rect(self):
        index = self.currentIndex()
        if not index.isValid() or self.model().sheet.columns[index.column()].get('source') is not None:
            return QRect()
        rect = self.visualRect(index)
        return QRect(rect.right()-4, rect.bottom()-4, 9, 9)

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self.handle_rect()
        if not rect.isEmpty():
            painter = QPainter(self.viewport())
            painter.fillRect(rect, QColor('#2878bd'))
            painter.end()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undoRequested.emit(); return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redoRequested.emit(); return
        if event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            selected = self.selectionModel().selectedIndexes()
            groups = []
            for col in sorted({i.column() for i in selected}):
                rows = sorted({i.row() for i in selected if i.column() == col})
                if len(rows) > 1:
                    groups.append((col, rows[0], rows[1:]))
            if groups:
                self.fillSelectionRequested.emit(groups)
            return
        index = self.currentIndex()
        if index.isValid() and (event.key() in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter) or
                                (event.text() and not event.modifiers() & Qt.KeyboardModifier.ControlModifier)):
            if self.model().sheet.columns[index.column()].get('source') is None:
                self.editRequested.emit(index, event.text() if event.key() not in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter) else '')
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if self.formula_mode and index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.referenceRequested.emit(index)
            return
        if event.button() == Qt.MouseButton.LeftButton and self.handle_rect().contains(event.position().toPoint()):
            self.drag_fill = self.currentIndex()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.formula_mode:
            return
        if self.drag_fill is not None:
            start = self.drag_fill
            self.drag_fill = None
            end = self.indexAt(event.position().toPoint())
            if end.isValid() and end.row() != start.row():
                rows = list(range(min(start.row(), end.row()), max(start.row(), end.row())+1))
                rows.remove(start.row())
                self.fillRequested.emit(start.column(), start.row(), rows)
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.handle_rect().contains(event.position().toPoint()):
            self.drag_fill = None
            self.fillAllRequested.emit(self.currentIndex())
            return
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and self.model().sheet.columns[index.column()].get('source') is None:
            self.editRequested.emit(index, '')
            return
        super().mouseDoubleClickEvent(event)
