"""Inspect and enable installed optional format readers."""
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QPushButton, QMessageBox, QHeaderView

from .format_extensions import FormatRegistry


class ExtensionDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('格式扩展管理')
        self.resize(660, 340)
        layout = QVBoxLayout(self)
        note = QLabel('扩展包安装后自动识别。选择扩展可启用或禁用；重启软件后生效。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['扩展名称', '版本', '格式', '状态'])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 75)
        self.tree.setColumnWidth(2, 85)
        layout.addWidget(self.tree)
        for entry in FormatRegistry().entries():
            item = QTreeWidgetItem([entry['name'], entry.get('version', ''),
                                    ', '.join(entry.get('suffixes', [])),
                                    {'ready': '可用', 'disabled': '已禁用'}.get(entry['status'], entry['status'])])
            item.setToolTip(0, entry['name'])
            item.setToolTip(3, entry['status'])
            item.entry = entry
            self.tree.addTopLevelItem(item)
        for text, callback in [('启用 / 禁用所选', self.toggle),
                               ('打开扩展目录', self.open_folder), ('关闭', self.accept)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def toggle(self):
        item = self.tree.currentItem()
        if item is None:
            return
        marker = item.entry['path'] / 'disabled'
        try:
            if marker.exists():
                marker.unlink()
            else:
                marker.touch()
            item.setText(3, 'disabled' if marker.exists() else '重启后重新检查')
        except OSError as exc:
            QMessageBox.warning(self, '扩展设置失败', str(exc))

    def open_folder(self):
        root = FormatRegistry().root
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
