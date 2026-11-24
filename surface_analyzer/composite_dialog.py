"""Composite save options and editable role-based single/batch run confirmation."""
from copy import deepcopy
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QLabel,
    QComboBox,QCheckBox,QPushButton,QDialogButtonBox,QFileDialog,QTableWidget,QTableWidgetItem,
    QHeaderView,QSpinBox,QMessageBox)
from .composite_recipe import (required_roles,ROLE_LABELS,DEFAULT_KEYWORDS,validate_composite,
                               validate_group,scan_groups,supported_suffixes)
from .dialog_paths import dialog_initial_path,remember_dialog_path


def file_filter():
    from .format_extensions import FormatRegistry
    return FormatRegistry().file_filter()


class CompositeSaveDialog(QDialog):
    def __init__(self, owner, module):
        super().__init__(owner);self.owner=owner;self.module=module;self.recipe=None
        self.setWindowTitle('保存组合 Recipe');self.resize(560,570)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.name=QLineEdit();self.name.setPlaceholderText('例如 A 产品膜厚 / B 产品平行度');form.addRow('名称',self.name)
        self.base2=QCheckBox('参与单片 2 扣减（配方固定，不随文件缺失改变）')
        self.base2.setChecked(module=='thickness' and owner.data_base2 is not None)
        if module=='thickness':form.addRow(self.base2)
        self.keywords={}
        if module == 'vig':
            vig = owner._vig_composite_module()
            fixed = [f"{ROLE_LABELS[key]}: {vig.thickness_spins[key].value():g} µm（固定值，无文件列）"
                     for key in ('up','mid','low') if vig.slots[key] is None]
            form.addRow(QLabel('；'.join(fixed) or '三个电极均使用文件输入'))
            form.addRow(QLabel('保存导出原点：' + ('有效点左下角归零' if owner._export_origin_zero_active_points_enabled() else '与界面坐标一致')))

            role_names = ('two','three') + tuple(key for key in ('up','mid','low') if vig.slots[key] is not None)
        else:
            role_names = ('stack','base1','base2') if module=='thickness' else ('base','measure')
        for role in role_names:
            edit=QLineEdit(DEFAULT_KEYWORDS[role]);self.keywords[role]=edit;form.addRow(ROLE_LABELS[role]+'关键词',edit)
        self.mode=QComboBox();self.mode.addItem('保持原坐标','none');self.mode.addItem('固定 XY 偏移','fixed');self.mode.addItem('每组重新自动匹配（保存偏移为初始位置）','auto')
        if module=='thickness':
            records=[owner.data_base1,owner.data_base2]
            mode='auto' if any(r and r.get('registration_mode')=='auto' for r in records) else ('fixed' if any(r and (r.get('offset_x',0) or r.get('offset_y',0)) for r in records) else 'none')
            self.mode.setCurrentIndex(self.mode.findData(mode));form.addRow('配准方式',self.mode)
            offsets='；'.join(f'{ROLE_LABELS[r]} ΔX {(record or {}).get("offset_x",0):+.4g} / ΔY {(record or {}).get("offset_y",0):+.4g} mm' for r,record in zip(('base1','base2'),records))
            reg=QLabel(f'容差 {owner.spin_tol.value():g} mm。'+offsets+'。自动模式每组重新计算，不保存匹配点。')
            reg.setWordWrap(True);form.addRow(reg)
        self.estimated=QComboBox();self.estimated.addItem('拒绝抽样／估算结果','reject');self.estimated.addItem('允许并明确标记（不可直接用于放行）','allow_marked');form.addRow('结果质量策略',self.estimated)
        self.deletion=QComboBox();self.deletion.addItem('校验不兼容则该组失败','fail');self.deletion.addItem('跳过不兼容删点并记录警告','skip_marked');form.addRow('手动删点重放',self.deletion)
        self.csv=QCheckBox('结果 CSV');self.csv.setChecked(True);self.report=QCheckBox('报告图 PNG');self.report.setChecked(True)
        row=QHBoxLayout();row.addWidget(self.csv);row.addWidget(self.report);form.addRow('每组输出',row)
        note=QLabel('每个槽使用写入当时的完整单层参数；不使用当前界面参数补齐旧槽。\n关键词匹配完整文件名（含后缀），不区分大小写，只扫描直接子文件夹。')
        note.setWordWrap(True);layout.addWidget(note)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        for edit in [self.name,*self.keywords.values()]:edit.textChanged.connect(self.validate)
        for combo in (self.mode,self.estimated,self.deletion):combo.currentIndexChanged.connect(self.validate)
        for check in (self.base2,self.csv,self.report):check.toggled.connect(self.validate)
        self.validate()

    def validate(self,*_):
        if 'base2' in self.keywords:self.keywords['base2'].setEnabled(self.base2.isChecked())
        try:
            self.recipe=self.owner._compose_recipe(self.module,self.name.text(),include_base2=self.base2.isChecked(),
                registration_mode=self.mode.currentData(),keywords={r:e.text() for r,e in self.keywords.items()},
                policy=dict(estimated=self.estimated.currentData(),deletion_mismatch=self.deletion.currentData()),
                export=dict(csv=self.csv.isChecked(),report=self.report.isChecked()))
            self.error.setText('参数快照完整，可以保存。');valid=True
        except (ValueError,TypeError) as exc:self.recipe=None;self.error.setText(str(exc));valid=False
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)


class CompositeRunDialog(QDialog):
    def __init__(self,owner,recipe):
        super().__init__(owner);validate_composite(recipe);self.recipe=deepcopy(recipe);self.groups=[]
        self.roles=required_roles(recipe);self.setWindowTitle('运行组合 Recipe · '+recipe['name']);self.resize(1050,630)
        layout=QVBoxLayout(self)
        note=QLabel('单组：按角色逐个选择文件。批量：每个直接子文件夹为一组。双击角色单元格可手动选择／更换文件；不按排列顺序推测角色。')
        note.setWordWrap(True);layout.addWidget(note)
        if recipe['module'] == 'vig':
            fixed = [f"{ROLE_LABELS[k]} = {v['value_um']:g} µm（固定值）" for k,v in recipe['electrodes'].items() if v['mode']=='fixed']
            fixed_note = QLabel('；'.join(fixed) or '上/中/下电极均为文件角色')
            fixed_note.setWordWrap(True); layout.addWidget(fixed_note)
        row=QHBoxLayout()
        self.single=QPushButton('配置单组');self.scan=QPushButton('选择批量根目录并扫描')
        self.single.clicked.connect(self.single_group);self.scan.clicked.connect(self.scan_root)
        row.addWidget(self.single);row.addWidget(self.scan);layout.addLayout(row)
        self.table=QTableWidget(0,len(self.roles)+3);self.table.setHorizontalHeaderLabels(['运行','样品编号',*[ROLE_LABELS[r] for r in self.roles],'状态／原因'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for c in range(2,2+len(self.roles)):self.table.horizontalHeader().setSectionResizeMode(c,QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False);self.table.cellDoubleClicked.connect(self.choose_file);self.table.itemChanged.connect(self.revalidate)
        layout.addWidget(self.table,1)
        form=QFormLayout();layout.addLayout(form)
        self.output=QLineEdit();button=QPushButton('选择输出目录');button.clicked.connect(self.choose_output)
        row=QHBoxLayout();row.addWidget(self.output);row.addWidget(button);form.addRow('输出位置',row)
        self.estimated=QComboBox();self.estimated.addItem('拒绝抽样／估算结果','reject');self.estimated.addItem('允许抽样并标记','allow_marked')
        self.estimated.setCurrentIndex(self.estimated.findData(recipe['policy']['estimated']));form.addRow('本次质量策略',self.estimated)
        self.deletion=QComboBox();self.deletion.addItem('不兼容删点使该组失败','fail');self.deletion.addItem('跳过不兼容删点并标记','skip_marked')
        self.deletion.setCurrentIndex(self.deletion.findData(recipe['policy']['deletion_mismatch']));form.addRow('本次删点策略',self.deletion)
        self.timeout=QSpinBox();self.timeout.setRange(10,86400);self.timeout.setValue(1800);self.timeout.setSuffix(' 秒 / 组');form.addRow('组运行时限',self.timeout)
        self.notice=QLabel();self.notice.setWordWrap(True);layout.addWidget(self.notice)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始运行')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.start);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.output.textChanged.connect(self.revalidate);self.single_group()

    def single_group(self):
        self.groups=[dict(sample_id='单组',files={r:[] for r in self.roles},selected=True)];self.populate()

    def scan_root(self):
        root=QFileDialog.getExistingDirectory(self,'选择数据根目录',dialog_initial_path('import'))
        if not root:return
        remember_dialog_path('import',root,directory=True)
        # Directory traversal only, no measurement files parsed on the GUI thread.
        from .workers import run_cancelable_dialog, TaskCancelled
        try:self.groups=run_cancelable_dialog(self,'正在识别分组文件',lambda progress,cancel: self._scan(root,progress,cancel))
        except TaskCancelled:
            self.notice.setText('扫描已取消，原确认表保持不变。');return
        except Exception as exc:QMessageBox.warning(self,'扫描未完成',str(exc));return
        self.populate()

    def _scan(self,root,progress,cancel):
        from .workers import TaskCancelled
        def check():
            if cancel.is_set():raise TaskCancelled()
        check();result=scan_groups(root,self.recipe,check_cancel=check);check();return result

    def populate(self):
        self.table.blockSignals(True);self.table.setRowCount(len(self.groups))
        for i,group in enumerate(self.groups):
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if group.get('selected',True) else Qt.CheckState.Unchecked);self.table.setItem(i,0,check)
            self.table.setItem(i,1,QTableWidgetItem(group['sample_id']))
            for c,role in enumerate(self.roles,2):
                value=group['files'].get(role,[]);paths=value if isinstance(value,list) else ([value] if value else [])
                item=QTableWidgetItem(' / '.join(Path(p).name for p in paths) or '双击选择文件')
                item.setData(Qt.ItemDataRole.UserRole,paths);item.setToolTip('\n'.join(paths));item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(i,c,item)
            self.table.setItem(i,len(self.roles)+2,QTableWidgetItem())
        self.table.blockSignals(False);self.revalidate()

    def current_groups(self):
        return [dict(sample_id=self.table.item(i,1).text().strip(),selected=self.table.item(i,0).checkState()==Qt.CheckState.Checked,
                     files={r:self.table.item(i,c).data(Qt.ItemDataRole.UserRole) or [] for c,r in enumerate(self.roles,2)}) for i in range(self.table.rowCount())]

    def choose_file(self,row,col):
        if col not in range(2,2+len(self.roles)):return
        path,_=QFileDialog.getOpenFileName(self,'指定'+ROLE_LABELS[self.roles[col-2]],dialog_initial_path('import'),file_filter())
        if not path:return
        remember_dialog_path('import',path)
        item=self.table.item(row,col);self.table.blockSignals(True);item.setData(Qt.ItemDataRole.UserRole,[str(Path(path).resolve())]);item.setText(Path(path).name);item.setToolTip(path);self.table.blockSignals(False);self.revalidate()

    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'选择批量输出位置',dialog_initial_path('export'))
        if path:remember_dialog_path('export',path,directory=True);self.output.setText(path)

    def revalidate(self,*_):
        groups=self.current_groups();ready=0;selected=0;seen={};conflicts=set()
        for i,g in enumerate(groups):
            if not g['selected'] or validate_group(g,self.recipe):continue
            for values in g['files'].values():
                key=str(Path(values[0]).resolve()).casefold()
                if key in seen:conflicts.update((i,seen[key]))
                seen[key]=i
        self.table.blockSignals(True)
        for i,g in enumerate(groups):
            errors=validate_group(g,self.recipe)
            if i in conflicts:errors.append('冲突：选中分组复用了文件')
            if g['selected']:selected+=1;ready+=not bool(errors)
            item=self.table.item(i,len(self.roles)+2);item.setText('; '.join(errors) or '就绪');item.setToolTip(item.text());item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
        self.table.blockSignals(False)
        self.notice.setText(f'选中 {selected} 组；其中就绪 {ready} 组。未选中或有错误的组会记录跳过原因。每次运行使用新的独立输出目录。')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready>0 and bool(self.output.text().strip()))

    def start(self):
        self.revalidate();self.groups=self.current_groups()
        if not self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():return
        self.recipe['policy']=dict(estimated=self.estimated.currentData(),deletion_mismatch=self.deletion.currentData())
        validate_composite(self.recipe);self.accept()
