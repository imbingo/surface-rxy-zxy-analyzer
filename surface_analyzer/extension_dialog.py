"""Inspect and enable installed optional extensions."""
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QPushButton, QMessageBox, QHeaderView

from .format_extensions import FormatRegistry
from .module_extensions import ModuleExtensionRegistry


class ExtensionDialog(QDialog):
    def __init__(self, parent, format_registry=None, module_registry=None):
        super().__init__(parent)
        self.setWindowTitle('扩展管理…')
        self.resize(760, 380)
        layout = QVBoxLayout(self)
        note = QLabel('扩展包安装后自动识别。选择扩展可启用或禁用；重启软件后生效。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['扩展名称', '类型', '版本', '格式', '状态'])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 64)
        self.tree.setColumnWidth(2, 75)
        self.tree.setColumnWidth(3, 90)
        layout.addWidget(self.tree)
        self.format_registry = format_registry or FormatRegistry()
        self.module_registry = module_registry or ModuleExtensionRegistry()
        entries = [
            ('格式', entry) for entry in self.format_registry.entries()
        ] + [
            ('模块', entry) for entry in self.module_registry.entries()
        ]
        for extension_type, entry in entries:
            item = QTreeWidgetItem([
                entry['name'],
                extension_type,
                entry.get('version', ''),
                ', '.join(entry.get('suffixes', [])),
                {'ready': '可用', 'disabled': '已禁用'}.get(entry['status'], entry['status']),
            ])
            item.setToolTip(0, entry['name'])
            item.setToolTip(4, entry['status'])
            item.entry = entry
            item.extension_type = extension_type
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
            item.setText(4, '已禁用' if marker.exists() else '重启后重新检查')
        except OSError as exc:
            QMessageBox.warning(self, '扩展设置失败', str(exc))

    def open_folder(self):
        root = self.format_registry.root.parent
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
