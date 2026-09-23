"""SOMA-style searchable local library, favorites and recent recipes."""
from pathlib import Path
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
                            QTreeWidget, QTreeWidgetItem, QHeaderView, QPushButton,
                            QLabel, QFileDialog, QMessageBox)
from .recipe_library import atomic_json, read_recipe
from .dialog_paths import dialog_initial_path, remember_dialog_path


class RecipeLibraryDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.library = owner._recipe_library()
        self.setWindowTitle('Recipe 管理 · 双击加载')
        self.resize(820, 510)
        self.setStyleSheet('QTreeWidget { border: 1px solid #dce4ec; border-radius: 6px; }'
                           'QTreeWidget::item { height: 28px; }'
                           'QHeaderView::section { background: #edf3f9; padding: 6px; border: none; }')
        layout = QVBoxLayout(self)
        self.current = QLabel('当前 Recipe：' + getattr(owner, 'current_recipe_name', '未选择'))
        layout.addWidget(self.current)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索 Recipe 名称、版本或文件名')
        self.search.setClearButtonEnabled(True)
        self.filter = QComboBox()
        self.filter.addItems(['全部 Recipe', '收藏', '最近使用'])
        row.addWidget(self.search, 1)
        row.addWidget(self.filter)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['收藏', 'Recipe 名称', '软件版本', '保存时间'])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 50)
        self.tree.setColumnWidth(2, 90)
        self.tree.setColumnWidth(3, 145)
        layout.addWidget(self.tree, 1)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        path = QLabel(str(self.library.root))
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)
        row = QHBoxLayout()
        self.actions = {}
        for title, fn in [('从文件导入', self.import_files), ('保存当前参数', self.save_current),
                          ('导出所选', self.export_selected), ('收藏 / 取消收藏', self.favorite),
                          ('加载所选', self.load_selected)]:
            b = QPushButton(title)
            b.clicked.connect(fn)
            self.actions[title] = b
            row.addWidget(b)
        layout.addLayout(row)
        bottom = QHBoxLayout()
        for title, fn in [('打开配方库', self.open_folder), ('关闭', self.reject)]:
            b = QPushButton(title)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        layout.addLayout(bottom)
        self.search.textChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.load_selected())
        self.refresh()

    def selected(self):
        item = self.tree.currentItem()
        return Path(item.data(0, Qt.ItemDataRole.UserRole)) if item else None

    def selection_changed(self):
        for title in ('导出所选', '收藏 / 取消收藏', '加载所选'):
            self.actions[title].setEnabled(self.selected() is not None)

    def refresh(self, *_):
        selected = self.selected()
        self.tree.clear()
        try:
            entries, errors = self.library.entries()
            query = self.search.text().strip().lower()
            for entry in entries:
                if query not in f"{entry['name']} {entry['version']} {entry['path'].name}".lower():
                    continue
                if self.filter.currentIndex() == 1 and not entry['favorite']:
                    continue
                if self.filter.currentIndex() == 2 and not entry['recent']:
                    continue
                item = QTreeWidgetItem(['★' if entry['favorite'] else '', str(entry['name']),
                                       str(entry['version']), entry['modified']])
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry['path']))
                item.setToolTip(1, str(entry['path']))
                self.tree.addTopLevelItem(item)
                if entry['path'] == selected:
                    self.tree.setCurrentItem(item)
            self.notice.setText(f'共 {len(entries)} 个 Recipe；保存同名参数会新增一份，原文件保留。' + (f' {len(errors)} 个文件无法读取。' if errors else ''))
            self.notice.setToolTip('\n'.join(errors))
        except (OSError, ValueError) as exc:
            self.notice.setText(str(exc))
        self.selection_changed()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '导入到 Recipe 库', dialog_initial_path('import'),
            'Recipe JSON (*.json)')
        if paths:
            remember_dialog_path('import', paths[0])
        failures = []
        for path in paths:
            try:
                self.library.import_file(path)
            except (OSError, ValueError) as exc:
                failures.append(f'{Path(path).name}: {exc}')
        self.refresh()
        if failures:
            QMessageBox.warning(self, '部分 Recipe 未导入', '\n'.join(failures))

    def save_current(self):
        self.owner.save_recipe_to_library()
        self.current.setText('当前 Recipe：' + getattr(self.owner, 'current_recipe_name', '未选择'))
        self.refresh()

    def favorite(self):
        if self.selected():
            try:
                self.library.favorite(self.selected())
                self.refresh()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '收藏失败', str(exc))

    def export_selected(self):
        source = self.selected()
        if not source:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '导出所选 Recipe', dialog_initial_path('export', source.name),
            'Recipe JSON (*.json)')
        if path:
            remember_dialog_path('export', path)
            try:
                atomic_json(path, read_recipe(source))
                self.notice.setText('已导出：' + path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '导出失败', str(exc))

    def load_selected(self):
        if self.selected() and self.owner.load_library_recipe(self.selected()):
            self.accept()

    def open_folder(self):
        try:
            self.library.root.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))
        except OSError as exc:
            QMessageBox.warning(self, '无法打开目录', str(exc))
