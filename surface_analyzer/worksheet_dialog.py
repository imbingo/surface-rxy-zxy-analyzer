"""Transactional Excel-style preparation window; immutable source preview."""
import copy
import json
import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTabWidget, QWidget, QTableView, QPlainTextEdit,
    QMessageBox, QInputDialog, QMenu, QDialogButtonBox)

from .data_preview import PAGE_SIZE, analysis_states, source_text_page
from .data_preview_dialog import PreviewModel, replace_model
from .worksheet import Worksheet, letters, column_number, CELL
from .worksheet_widgets import WorksheetModel, WorksheetView, FormulaEdit
from .workers import FunctionWorker
from .delimited_text import detect_delimiter, tokenize_delimited_line


class DataPreviewDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('数据预览与派生')
        self.resize(min(1120, self.screen().availableGeometry().width()-40),
                    min(720, self.screen().availableGeometry().height()-40))
        self.base = owner.absolute_raw_df.drop(columns=[d['name'] for d in getattr(owner, 'derived_columns', [])], errors='ignore')
        self.sheet = Worksheet(self.base, getattr(owner, 'worksheet_data', None), getattr(owner, 'derived_columns', []))
        self.frame = owner.absolute_raw_df
        self.states = analysis_states(owner, self.frame)
        self.roles = {cb.currentText(): axis for axis, cb in
                      (('X', owner.cb_x_col), ('Y', owner.cb_y_col), ('Z', owner.cb_z_col)) if cb.currentText()}
        self.saved = copy.deepcopy(self.sheet.data)
        self.history, self.future = [], []
        self.position = 0
        self.thread = self.worker = None
        self.editing = None
        self.source_offsets, self.source_page, self.source_next, self.source_eof = [0], 0, 0, False
        self.close_after_apply = False
        self.apply_after_edit = False
        self.allow_close = False
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.details = QPushButton('导入详情')
        self.details.clicked.connect(self.show_details)
        top.addWidget(self.details)
        layout.addLayout(top)
        formula_bar = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setFixedWidth(84)
        self.formula = FormulaEdit()
        self.formula.setPlaceholderText('选中派生单元格，输入 = 后可点击其他单元格引用；Enter 确认，Esc 取消')
        self.formula.submitted.connect(self.submit_formula)
        self.formula.cancelled.connect(self.cancel_edit)
        self.formula.textChanged.connect(self.formula_changed)
        formula_bar.addWidget(self.address)
        formula_bar.addWidget(QLabel('ƒx'))
        formula_bar.addWidget(self.formula, 1)
        self.undo_button = QPushButton('撤销')
        self.redo_button = QPushButton('重做')
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        formula_bar.addWidget(self.undo_button)
        formula_bar.addWidget(self.redo_button)
        layout.addLayout(formula_bar)
        self.tabs = QTabWidget()
        analysis = QWidget()
        al = QVBoxLayout(analysis)
        paging = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItems(['分析输入', '最终计算', '已排除'])
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.previous, self.next = QPushButton('上一页'), QPushButton('下一页')
        self.previous.clicked.connect(lambda: self.turn(-1))
        self.next.clicked.connect(lambda: self.turn(1))
        self.page_label = QLabel()
        for w in (self.scope, self.previous, self.next, self.page_label):
            paging.addWidget(w)
        paging.addStretch()
        al.addLayout(paging)
        self.table = WorksheetView()
        self.table.editRequested.connect(self.begin_edit)
        self.table.referenceRequested.connect(self.insert_reference)
        self.table.fillRequested.connect(self.fill_rows)
        self.table.fillAllRequested.connect(self.fill_all)
        self.table.fillSelectionRequested.connect(self.fill_groups)
        self.table.undoRequested.connect(self.undo)
        self.table.redoRequested.connect(self.redo)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.header_menu)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self.rename_column)
        al.addWidget(self.table)
        self.tabs.addTab(analysis, '分析数据')
        self.grid = QTableView()
        if {'_matrix_row', '_matrix_col'}.issubset(self.base.columns):
            self.tabs.addTab(self.grid, '网格位置（只读）')
        source = QWidget()
        sl = QVBoxLayout(source)
        sl.addWidget(QLabel('源文件只读，每段最多 200 行 / 256 KB；源行参与状态未知，行号不作为分析记录标识。'))
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.source_views=QTabWidget()
        self.source_table=QTableView()
        self.source_table.setAlternatingRowColors(True)
        self.source_views.addTab(self.raw,'原始文本')
        self.source_views.addTab(self.source_table,'字段预览')
        sl.addWidget(self.source_views)
        source_buttons = QHBoxLayout()
        self.raw_previous, self.raw_next = QPushButton('上一段'), QPushButton('下一段')
        self.raw_previous.clicked.connect(lambda: self.read_source(-1))
        self.raw_next.clicked.connect(lambda: self.read_source(1))
        source_buttons.addWidget(self.raw_previous)
        source_buttons.addWidget(self.raw_next)
        source_buttons.addStretch()
        sl.addLayout(source_buttons)
        self.tabs.addTab(source, '源文件预览（只读）')
        self.tabs.currentChanged.connect(self.tab_changed)
        layout.addWidget(self.tabs, 1)
        controls = QHBoxLayout()
        self.mapping_controls = []
        for axis, cb, unit in (('X', owner.cb_x_col, owner.cb_x_unit), ('Y', owner.cb_y_col, owner.cb_y_unit)):
            controls.addWidget(QLabel(axis))
            col = QComboBox()
            col.addItems([c['name'] for c in self.sheet.columns])
            col.setPlaceholderText('请选择列')
            col.setCurrentIndex(col.findText(cb.currentText()))
            units = QComboBox()
            units.addItems(['mm', 'µm', 'nm'])
            units.setPlaceholderText('单位')
            units.setCurrentIndex(units.findText(unit.currentText()))
            controls.addWidget(col, 1); controls.addWidget(units)
            self.mapping_controls.append((col, units))
        self.target_label = QLabel()
        self.saved_mapping = [(c.currentText(),u.currentText()) for c,u in self.mapping_controls]
        controls.addWidget(self.target_label)
        layout.addLayout(controls)
        self.status = QLabel('右键列标题插入派生列；双击填充柄填充全部分析输入。公式不自动换算单位。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.cancel = QPushButton('取消当前任务')
        self.cancel.clicked.connect(self.cancel_task)
        self.cancel.setEnabled(False)
        self.apply = QPushButton('应用分析')
        self.apply.setObjectName('accentBtn')
        self.apply.clicked.connect(self.apply_analysis)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        footer.addWidget(self.cancel); footer.addStretch(); footer.addWidget(self.apply); footer.addWidget(close)
        layout.addLayout(footer)
        self.refresh()
        # Existing values can be displayed immediately without recalculating a
        # million-row recipe in the constructor / GUI thread.
        for col in self.sheet.columns:
            if col.get('source') is None and col['name'] in self.frame:
                value = pd.to_numeric(self.frame[col['name']], errors='coerce').to_numpy(dtype=float)
                self.sheet.values[col['id']] = value
                self.sheet.errors[col['id']] = np.where(np.isfinite(value), 0, 2).astype(np.uint8)
        self.refresh()
        self.watch = QTimer(self)
        self.watch.setInterval(500)
        self.signature = self.analysis_signature()
        self.watch.timeout.connect(self.sync_analysis)
        self.watch.start()

    @property
    def dirty(self):
        return self.sheet.data != self.saved or [(c.currentText(),u.currentText()) for c,u in self.mapping_controls] != self.saved_mapping

    def analysis_signature(self):
        return (id(self.owner.active_idx), id(self.owner.last_metrics), id(getattr(self.owner, '_task_thread', None)))

    def sync_analysis(self):
        if self.thread is None and self.signature != self.analysis_signature():
            self.signature = self.analysis_signature()
            self.states = analysis_states(self.owner, self.frame)
            self.refresh()

    def show_details(self):
        info = self.owner.import_info
        text = (f"解析器：{info.get('parser_engine') or '专用解析器'}\n"
                f"字段完整性：{info.get('parser_integrity_status', '不适用')}\n"
                f"回退原因：{info.get('parser_fast_fallback') or '无'}\n"
                f"抽样方式：{info.get('sample_method_key', 'full')}\n"
                '工作表行号对应分析输入顺序，不是源文件物理行号。')
        QMessageBox.information(self, '导入详情', text)

    def refresh(self):
        self.setWindowTitle('数据预览与派生'+(' · 未应用修改' if self.dirty else ''))
        mode = self.scope.currentIndex()
        mask = np.ones(len(self.frame), bool) if mode == 0 else (
            self.states.eq('参与计算').to_numpy() if mode == 1 else self.states.ne('参与计算').to_numpy())
        positions = np.flatnonzero(mask)
        self.position = min(self.position, max(0, (len(positions)-1)//PAGE_SIZE*PAGE_SIZE))
        selected = positions[self.position:self.position+PAGE_SIZE]
        model = WorksheetModel(self.sheet, selected, self.states, self.roles, self.table)
        replace_model(self.table, model)
        self.table.selectionModel().currentChanged.connect(self.current_changed)
        for i in range(len(self.sheet.columns)):
            self.table.setColumnWidth(i, 150)
        self.previous.setEnabled(self.position > 0)
        self.next.setEnabled(self.position+PAGE_SIZE < len(positions))
        self.page_label.setText(f'{self.position+1 if len(positions) else 0}–{min(self.position+PAGE_SIZE,len(positions))} / {len(positions):,}')
        info = self.owner.import_info
        self.summary.setText(f"{self.owner.current_source_name}  |  分析输入 {len(self.frame):,}  |  {info.get('source_format') or '点表'}")
        if info.get('height_matrix') or info.get('input_semantics') == 'pixel_xy' or str(info.get('source_format', '')).startswith('Zygo'):
            self.summary.setText(self.summary.text()+f"  |  Pitch X/Y：{info.get('sampling_pitch_x_um', '未知')} / {info.get('sampling_pitch_y_um', '未知')} µm（{info.get('sampling_pitch_source', '未知')}）")
        target = self.sheet.data.get('target_z')
        target_col = next((c for c in self.sheet.columns if c['id'] == target), None)
        self.target_label.setText(f"分析 Z：{target_col['name']} [{target_col.get('unit') or '未选单位'}]" if target_col else f"分析 Z：{self.owner.cb_z_col.currentText() or '未选择'}")
        self.undo_button.setEnabled(bool(self.history))
        self.redo_button.setEnabled(bool(self.future))
        if len(selected) and {'_matrix_row', '_matrix_col'}.issubset(self.base.columns):
            point = self.base.iloc[selected[0]]
            r0, c0 = int(point['_matrix_row']), int(point['_matrix_col'])
            cells = self.base.loc[self.base['_matrix_row'].between(r0,r0+19) & self.base['_matrix_col'].between(c0,c0+19)]
            grid = pd.DataFrame('', index=range(r0,r0+20), columns=range(c0,c0+20))
            tips = {}
            z = target_col['name'] if target_col else self.owner.cb_z_col.currentText()
            for idx, row in cells.iterrows():
                r, c = int(row['_matrix_row']), int(row['_matrix_col'])
                value = self.frame.at[idx,z] if z in self.frame else ''
                grid.at[r,c] = str(value)
                mapped = self.owner.df_raw
                coords = f"X={mapped.at[idx,'X']} mm；Y={mapped.at[idx,'Y']} mm" if mapped is not None and idx in mapped.index else '尚未映射'
                tips[(r,c)] = f'{coords}\n输入 Z={value}\n{self.states.loc[idx]}'
            replace_model(self.grid, PreviewModel(grid, parent=self.grid, tips=tips, grid=True))
        if self.editing is not None:
            self.formula_changed()

    def scope_changed(self, *_):
        self.position = 0; self.refresh()

    def turn(self, direction):
        self.position = max(0,self.position+direction*PAGE_SIZE); self.refresh()

    def current_changed(self, index, previous=None):
        if not index.isValid() or self.editing or index.column() >= len(self.sheet.columns):
            return
        row = int(self.table.model().positions[index.row()])
        col = self.sheet.columns[index.column()]
        self.address.setText(letters(index.column())+str(row+2))
        self.formula.blockSignals(True)
        self.formula.setText(self.sheet.formula(col['id'],row) if col.get('source') is None else str(self.base.iloc[row][col['source']]))
        self.formula.blockSignals(False)
        self.formula.setReadOnly(col.get('source') is not None)
        self.status.setText(f'记录标识 {self.base.index[row]} · {self.states.iloc[row]}'+(' · 原始列只读' if col.get('source') is not None else ' · 派生列可编辑'))

    def begin_edit(self, index, text=''):
        if self.thread is not None:
            return
        row = int(self.table.model().positions[index.row()])
        col = self.sheet.columns[index.column()]
        self.editing = (col['id'],row)
        self.address.setText(letters(index.column())+str(row+2))
        self.formula.setReadOnly(False)
        self.formula.setText(text or self.sheet.formula(col['id'],row))
        self.formula.setFocus()
        cursor = self.formula.textCursor(); cursor.movePosition(cursor.MoveOperation.End); self.formula.setTextCursor(cursor)

    def formula_changed(self):
        if self.editing is None:
            index = self.table.currentIndex()
            if index.isValid() and self.sheet.columns[index.column()].get('source') is None:
                self.editing = (self.sheet.columns[index.column()]['id'], int(self.table.model().positions[index.row()]))
        self.table.formula_mode = bool(self.editing and self.formula.text().startswith('='))
        refs = [(int(m[4])-2,column_number(m[2])) for m in CELL.finditer(self.formula.text())]
        self.table.model().references = list(dict.fromkeys(refs))
        self.table.viewport().update()

    def insert_reference(self, index):
        row = int(self.table.model().positions[index.row()])
        self.formula.insertPlainText(letters(index.column())+str(row+2))
        self.formula.setFocus()

    def cancel_edit(self):
        self.editing = None; self.table.formula_mode = False
        self.table.model().references = []
        self.current_changed(self.table.currentIndex())
        self.table.viewport().update()

    def submit_formula(self):
        if self.editing is None:
            return
        identity,row = self.editing
        text = self.formula.text()
        self.change(lambda sheet: sheet.edit(identity,row,text), compute=True)

    def change(self, fn, compute=False):
        if self.thread is not None:
            return
        before = copy.deepcopy(self.sheet.data)
        draft = Worksheet(self.base, before)
        draft.values, draft.errors = self.sheet.values, self.sheet.errors
        try:
            fn(draft)
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc)); return
        def done(result):
            sheet, frame = result
            edited = self.editing
            self.history.append(before); self.future.clear()
            self.sheet, self.frame = sheet, frame
            self.editing = None; self.table.formula_mode = False
            self.refresh_mapping(); self.refresh()
            if edited:
                identity,row=edited
                column=next((i for i,c in enumerate(self.sheet.columns) if c['id']==identity),None)
                matches=np.flatnonzero(self.table.model().positions == row+1)
                if column is not None and len(matches):
                    self.table.setCurrentIndex(self.table.model().index(int(matches[0]),column))
            self.status.setText('工作表修改已计算，尚未应用分析；源文件未修改。')
        if compute:
            self.run(lambda p,c: (draft, draft.evaluate(c,p)), done)
        else:
            done((draft,self.frame))

    def refresh_mapping(self):
        for cb,_ in self.mapping_controls:
            current = cb.currentText()
            cb.clear(); cb.addItems([c['name'] for c in self.sheet.columns]); cb.setCurrentIndex(cb.findText(current))

    def insert_column(self, position):
        self.change(lambda sheet: sheet.insert(position))

    def rename_column(self, section):
        if self.thread is not None or section < 0 or self.editing is not None:
            return
        col = self.sheet.columns[section]
        if col.get('source') is not None:
            return
        name, ok = QInputDialog.getText(self,'重命名派生列','列名',text=col['name'])
        if ok:
            self.change(lambda sheet: sheet.rename(col['id'],name), compute=True)

    def header_menu(self, point):
        if self.thread is not None:
            return
        if self.editing is not None:
            self.status.setText('请先按 Enter 确认公式，或按 Esc 取消编辑。'); return
        section = self.table.horizontalHeader().logicalIndexAt(point)
        if section < 0:
            return
        col = self.sheet.columns[section]
        menu = QMenu(self)
        menu.addAction('左侧插入派生列', lambda: self.insert_column(section))
        menu.addAction('右侧插入派生列', lambda: self.insert_column(section+1))
        if col.get('source') is None:
            menu.addAction('重命名',lambda: self.rename_column(section))
            menu.addAction('删除派生列',lambda: self.delete_column(col['id']))
        menu.addSeparator()
        menu.addAction('设为分析 Z…',lambda: self.set_target(col['id']))
        menu.exec(self.table.horizontalHeader().mapToGlobal(point))

    def delete_column(self, identity):
        if QMessageBox.question(self,'删除派生列','删除后，引用此列的公式将显示 #REF!。是否删除？') == QMessageBox.StandardButton.Yes:
            self.change(lambda sheet: sheet.remove(identity),compute=True)

    def set_target(self, identity):
        col = self.sheet.column(identity)
        units = ['mm','µm','nm']
        current = col.get('unit') or self.owner.cb_z_unit.currentText()
        unit, ok = QInputDialog.getItem(self,'设为分析 Z','输出单位（公式不自动换算输入单位）',units, units.index(current) if current in units else 1,False)
        if ok:
            def update(sheet):
                sheet.column(identity)['unit'] = unit
                sheet.data['target_z'] = identity
            self.change(update)

    def fill_rows(self, column, source, targets):
        self.fill_groups([(column,source,targets)])

    def fill_groups(self, groups):
        model = self.table.model()
        prepared = [(self.sheet.columns[column]['id'], int(model.positions[source]),
                     sorted(int(model.positions[r]) for r in targets)) for column,source,targets in groups
                    if self.sheet.columns[column].get('source') is None]
        def fill(sheet):
            for identity,src,rows in prepared:
                if not rows:
                    continue
                begin = previous = rows[0]
                for row in rows[1:]:
                    if row != previous+1:
                        sheet.fill(identity,src,begin,previous); begin=row
                    previous=row
                sheet.fill(identity,src,begin,previous)
        self.change(fill,compute=True)

    def fill_all(self, index):
        if not index.isValid():
            return
        row = int(self.table.model().positions[index.row()])
        identity = self.sheet.columns[index.column()]['id']
        self.status.setText(f'填充全部分析输入：第 {row+2} 行至末行，共 {len(self.base)-row:,} 行（不限于当前页）。')
        self.change(lambda sheet: sheet.fill(identity,row,row,None),compute=True)

    def restore_history(self, undo):
        source,destination = (self.history,self.future) if undo else (self.future,self.history)
        if not source or self.thread is not None:
            return
        data = source[-1]
        draft = Worksheet(self.base,data)
        def done(result):
            source.pop(); destination.append(copy.deepcopy(self.sheet.data))
            self.sheet,self.frame = result
            self.editing=None; self.table.formula_mode=False
            self.refresh_mapping(); self.refresh(); self.cancel_edit()
        self.run(lambda p,c:(draft,draft.evaluate(c,p)),done)

    def undo(self):
        if self.editing is not None:
            self.cancel_edit(); return
        self.restore_history(True)

    def redo(self):
        if self.editing is not None:
            return
        self.restore_history(False)

    def run(self, fn, done):
        if self.thread is not None:
            return
        self.cancel.setEnabled(True)
        self.table.setEnabled(False); self.formula.setEnabled(False); self.apply.setEnabled(False)
        self.thread = QThread(self)
        self.worker = FunctionWorker(fn)
        self.worker.moveToThread(self.thread)
        self.worker.progress.connect(lambda value,text:self.status.setText(text))
        self.worker.succeeded.connect(done)
        self.worker.failed.connect(self.task_error)
        self.worker.cancelled.connect(lambda:self.task_error('已取消'))
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def task_error(self, text):
        self.apply_after_edit=False
        self.close_after_apply=False
        self.status.setText(text+'；当前分析未修改。')

    def finished(self):
        thread=self.thread; self.thread=self.worker=None; thread.deleteLater()
        self.cancel.setEnabled(False)
        self.table.setEnabled(True); self.formula.setEnabled(True); self.apply.setEnabled(True)
        self.raw_previous.setEnabled(self.source_page > 0)
        self.raw_next.setEnabled(not self.source_eof)
        if self.apply_after_edit:
            self.apply_after_edit=False
            QTimer.singleShot(0,self.apply_analysis)
            return
        if self.close_after_apply and not self.dirty:
            self.allow_close=True; self.reject()
        self.close_after_apply=False

    def cancel_task(self):
        if self.worker:
            self.worker.cancel_event.set()

    def apply_analysis(self):
        if self.thread is not None or getattr(self.owner,'_task_thread',None) is not None:
            self.status.setText('请等待当前任务完成。'); return
        if self.editing is not None:
            self.apply_after_edit=True
            self.submit_formula()
            if self.thread is None:
                self.apply_after_edit=False
            return
        target = self.sheet.data.get('target_z')
        col = next((c for c in self.sheet.columns if c['id']==target),None)
        if col is None or col.get('unit') not in ('mm','µm','nm'):
            self.status.setText('请右键高度列，选择“设为分析 Z”并确认单位。'); return
        mapping=[(c.currentText(),u.currentText()) for c,u in self.mapping_controls]
        if any(not c or not u for c,u in mapping):
            self.status.setText('请选择 X、Y 列与单位。'); return
        deleted = int((~self.owner.manual_mask).sum()) if self.owner.manual_mask is not None else 0
        if deleted and QMessageBox.question(self,'重新分析',f'应用后重置当前 {deleted:,} 个手动删点；滤波与 ROI 设置保留。是否应用？') != QMessageBox.StandardButton.Yes:
            return
        draft=Worksheet(self.base,self.sheet.data)
        def done(result):
            sheet,frame=result
            invalid=int(frame[col['name']].isna().sum())
            if invalid and QMessageBox.question(self,'存在无效单元格',f'高度列有 {invalid:,} 个空白或错误值，将不参与分析。是否继续？') != QMessageBox.StandardButton.Yes:
                return
            if self.owner._commit_derived_preview(frame,sheet.descriptors(),col['name'],col['unit'],mapping,worksheet=sheet.data):
                self.sheet,self.frame=sheet,frame
                self.saved=copy.deepcopy(sheet.data)
                self.saved_mapping=copy.deepcopy(mapping)
                self.roles={mapping[0][0]:'X',mapping[1][0]:'Y',col['name']:'Z'}
                self.states=analysis_states(self.owner,frame)
                self.refresh()
                self.status.setText('已应用分析；源文件未修改。')
        self.run(lambda p,c:(draft,draft.evaluate(c,p)),done)

    def reject(self):
        if self.thread is not None:
            self.cancel_task(); self.status.setText('正在取消，请任务结束后关闭。'); return
        if not self.allow_close and (self.dirty or self.editing is not None):
            message=QMessageBox(self)
            message.setWindowTitle('尚有未应用修改'); message.setText('应用修改后退出，还是放弃本次修改？')
            apply=message.addButton('应用',QMessageBox.ButtonRole.AcceptRole)
            discard=message.addButton('放弃',QMessageBox.ButtonRole.DestructiveRole)
            message.addButton('继续编辑',QMessageBox.ButtonRole.RejectRole)
            message.exec()
            if message.clickedButton() is apply:
                self.close_after_apply=True; self.apply_analysis(); return
            if message.clickedButton() is not discard:
                return
        super().reject()

    def closeEvent(self,event):
        event.ignore(); self.reject()

    def tab_changed(self,index):
        if self.tabs.tabText(index).startswith('源文件') and not self.raw.toPlainText():
            self.read_source(0)

    def read_source(self,direction):
        if self.thread is not None or (direction>0 and self.source_eof):
            return
        page=max(0,self.source_page+direction)
        offset=self.source_offsets[page] if page<len(self.source_offsets) else self.source_next
        path=self.owner.import_info.get('source_path','')
        encoding=self.owner.import_info.get('encoding','utf-8')
        def done(value):
            lines,end,eof=value
            self.source_page,self.source_next,self.source_eof=page,end,eof
            if page>=len(self.source_offsets):
                self.source_offsets.append(offset)
            self.raw.setPlainText('\n'.join(lines))
            columns=[c for c in self.base.columns if not str(c).startswith('_')]
            records=[]
            info=self.owner.import_info
            if not info.get('height_matrix') and not info.get('format_extension_id') and not str(info.get('source_format','')).startswith(('Zygo','Precitec')):
                for line in lines:
                    if not line.strip() or line.lstrip().startswith('#'):
                        continue
                    try:
                        separator=info.get('sep') or info.get('delimiter')
                        if separator not in (',',';','\t','|','；',r'\s+','whitespace'):
                            separator=detect_delimiter(line)
                        tokens=tokenize_delimited_line(line,separator)
                        while tokens and tokens[-1]=='':
                            tokens.pop()
                        if len(tokens)==len(columns) and tokens!=list(columns):
                            records.append(tokens)
                    except ValueError:
                        continue
            replace_model(self.source_table,PreviewModel(pd.DataFrame(records,columns=columns),self.roles,parent=self.source_table))
            self.source_views.setTabEnabled(1,bool(records))
            self.source_table.resizeColumnsToContents()
            self.status.setText(f'源文件字节 {offset:,}–{end:,}；长物理行可能分段。'+('已到末尾。' if eof else ''))
        self.run(lambda p,c:source_text_page(path,offset,encoding,c),done)
