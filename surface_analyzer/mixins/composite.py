"""Desktop entry points; computation is delegated to isolated real workspaces."""
from copy import deepcopy
from pathlib import Path
from PyQt6.QtCore import Qt,QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QMessageBox,QPushButton,QDialog,QVBoxLayout,QHBoxLayout,
    QLabel,QPlainTextEdit,QProgressBar,QTableWidget,QTableWidgetItem,QHeaderView)
from ..composite_recipe import build_composite,validate_composite


class CompositeMixin:
    def _workflow_notice(self, level, title, text):
        """Explicit interaction seam, overridden by an isolated automation workspace."""
        return getattr(QMessageBox,level)(self,title,text)

    def _slot_single_recipe_snapshot(self):
        # A computed Gap / in-memory dataset cannot be reproduced by loading
        # one file with a single-layer Recipe. Keep manual use, reject saving
        # that slot as an apparently reproducible file-based composite.
        if self.absolute_raw_df is None or not self.import_info.get('source_path'):
            return None
        return deepcopy(self._current_recipe_dict())

    def _init_composite_actions(self):
        self._composite_save_buttons={}
        for module,tabs in (('thickness',self.gap_parameter_tabs),('parallelism',self.parallel_parameter_tabs)):
            # Parameter pages use scroll areas; append above their final stretch.
            page=tabs.widget(1 if module=='parallelism' else 2)
            container=page.widget() if hasattr(page,'widget') else page
            layout=container.layout()
            save=QPushButton('保存组合 Recipe');save.setToolTip('保存各槽写入时的独立单层参数副本')
            save.clicked.connect(lambda checked=False,m=module:self.save_composite_recipe(m))
            run=QPushButton('运行组合 Recipe / 批量运行')
            run.clicked.connect(lambda checked=False,m=module:self.choose_composite_recipe(m))
            layout.insertWidget(max(0,layout.count()-1),save)
            layout.insertWidget(max(0,layout.count()-1),run)
            self._composite_save_buttons[module]=save
            self._task_controls.extend((save,run))
        self._refresh_composite_save_buttons()

    def _vig_composite_module(self):
        for module in self._extension_modules:
            if module.key == 'vig-image' and hasattr(module, 'compute_snapshot'):
                return module
        entries = self.module_extension_registry.entries()
        detail = '; '.join(f"{e.get('name')}: {e.get('status')}" for e in entries if e['id'] == 'vig-image')
        raise ValueError('未安装或不兼容的 VIG 模块扩展；请安装支持 V5.0.7 的扩展。' + detail)

    def _composite_records(self,module):
        if module == 'vig': return self._vig_composite_module().slots
        if module=='parallelism':return dict(base=self.parallel_base,measure=self.parallel_measure)
        return dict(stack=self.data_stack,base1=self.data_base1,base2=self.data_base2)

    def _refresh_composite_save_buttons(self):
        from ..composite_recipe import ROLE_LABELS
        for module,button in getattr(self,'_composite_save_buttons',{}).items():
            records=self._composite_records(module)
            required=('base','measure') if module=='parallelism' else ('stack','base1')
            missing=[ROLE_LABELS[r] for r in required if not records[r] or not records[r].get('single_recipe')]
            button.setEnabled(not missing and getattr(self,'_task_thread',None) is None)
            button.setToolTip(('缺少数据或写入时参数快照：'+'、'.join(missing)+'。请重新写入。') if missing else '内嵌各槽实际生效的独立单层 Recipe，不保存测量点数据')

    def _compose_recipe(self,module,name,*,include_base2=False,registration_mode='none',keywords=None,policy=None,export=None):
        records=self._composite_records(module)
        registration={}
        if module=='thickness':
            roles=('base1','base2') if include_base2 else ('base1',)
            registration=dict(mode=registration_mode,tolerance_mm=float(self.spin_tol.value()),
                offsets={r:[float((records[r] or {}).get('offset_x',0)),float((records[r] or {}).get('offset_y',0))] for r in roles})
        vig_options = {}
        if module == 'vig':
            export = deepcopy(export or {'csv':True,'report':True})
            export['zero_origin_active_points'] = self._export_origin_zero_active_points_enabled()
            vig = self._vig_composite_module()
            vig_options = dict(electrodes={key: ({'mode':'file'} if records[key] is not None else
                {'mode':'fixed','value_um':float(vig.thickness_spins[key].value())}) for key in ('up','mid','low')},
                calculation={'tolerance_mm':float(vig.tolerance_spin.value())})
        recipe=build_composite(module,records,name,include_base2=include_base2,
            registration=registration,keywords=keywords,policy=policy,export=export,app_version=self.APP_VERSION,**vig_options)
        recipe.setdefault('calculation', {})['judgment'] = deepcopy(self._judgment_spec())
        return validate_composite(recipe)

    def save_composite_recipe(self,module):
        from ..composite_dialog import CompositeSaveDialog
        dialog=CompositeSaveDialog(self,module)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            path=self._recipe_library().save(dialog.recipe,dialog.recipe['name'])
            self._show_status(f'组合 Recipe 已保存：{path.name}',8000)
            QMessageBox.information(self,'组合 Recipe 已保存','已保存各槽独立参数。请从 Recipe 管理加载，或点击“运行组合 Recipe / 批量运行”。')
        except (ValueError,OSError) as exc:QMessageBox.critical(self,'组合 Recipe 保存失败',str(exc))

    def choose_composite_recipe(self,module):
        from ..recipe_dialog import RecipeLibraryDialog
        dialog=RecipeLibraryDialog(self)
        dialog.type_filter.setCurrentText({'thickness':'膜厚组合 Recipe','parallelism':'平行度组合 Recipe','vig':'VIG 组合 Recipe'}[module])
        dialog.exec()

    def open_composite_run(self,recipe):
        validate_composite(recipe)  # Must precede every workspace mutation.
        if recipe['module'] == 'vig': self._vig_composite_module()
        if self._task_thread is not None:return False
        from ..composite_dialog import CompositeRunDialog
        dialog=CompositeRunDialog(self,recipe)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return False
        return self.start_composite_run(dialog.recipe,dialog.groups,dialog.output.text().strip(),timeout=dialog.timeout.value())

    def start_composite_run(self,recipe,groups,output,*,timeout=1800):
        validate_composite(recipe)
        if self._task_thread is not None:return False
        from ..composite_recipe import validate_group
        if not any(g.get('selected',True) and str(g.get('sample_id','')).strip() and not validate_group(g,recipe) for g in groups):
            raise ValueError('没有选中且就绪的分组；当前工作区未修改')
        from ..composite_runner import run_groups
        # Clear only module slots/results. Single-layer data stays available;
        # every worker role is isolated and never applies Recipe to this UI.
        if recipe['module'] == 'vig': self._vig_composite_module()
        self._composite_module=recipe['module']
        self._clear_composite_result()
        self.centralWidget().setEnabled(False)
        self._composite_running=True
        owner=self
        class RunProgressDialog(QDialog):
            def reject(self):
                owner._cancel_background_task()
        dialog=RunProgressDialog(self);dialog.setWindowTitle('组合 Recipe 运行 · '+recipe['name']);dialog.resize(760,420)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        layout=QVBoxLayout(dialog);label=QLabel('准备运行');label.setWordWrap(True);layout.addWidget(label)
        bar=QProgressBar();layout.addWidget(bar);log=QPlainTextEdit();log.setReadOnly(True);log.setMaximumBlockCount(500);layout.addWidget(log,1)
        cancel=QPushButton('取消后续运行');cancel.clicked.connect(self._cancel_background_task);layout.addWidget(cancel)
        self._composite_progress=(dialog,label,bar,log)
        dialog.open()
        started=self._run_background_task('组合 Recipe',
            lambda progress,event:run_groups(recipe,groups,output,progress,event,timeout=timeout),
            self._finish_composite_run,
            lambda error:self._composite_failed(error),
            deliver_result_when_cancelled=True,
            on_cancel=lambda:self._composite_failed('运行已取消'))
        if not started:self._composite_failed('无法启动任务')
        return started

    def _clear_composite_result(self):
        if getattr(self,'_composite_module',None)=='vig':
            module = self._vig_composite_module()
            module.slots = dict.fromkeys(('two','three','up','mid','low'))
            module._invalidate_result(); module._sync_slot_labels()
        elif getattr(self,'_composite_module',None)=='parallelism':self.clear_parallel_surfaces()
        else:self.clear_all_memory_slots()

    def _close_composite_progress(self):
        if getattr(self,'_composite_progress',None):
            dialog,*_=self._composite_progress;self._composite_progress=None
            dialog.done(0);dialog.deleteLater()
        self._composite_running=False
        self.centralWidget().setEnabled(True)

    def _composite_failed(self,error):
        self._close_composite_progress()
        self._clear_composite_result()
        QMessageBox.warning(self,'组合任务未完成',str(error))

    def _finish_composite_run(self,payload):
        self._close_composite_progress();self.last_composite_run=payload
        judgment=payload.get('recipe',{}).get('calculation',{}).get('judgment')
        if judgment is not None:self.set_judgment_spec(judgment,persist=False,update_single=False)
        snapshot=payload.get('snapshot')
        if snapshot:
            records=snapshot['records']
            if snapshot['module'] == 'vig':
                module = self._vig_composite_module()
                module.slots = {role: records.get(role) for role in ('two','three','up','mid','low')}
                config = payload['recipe']
                self._set_export_origin_zero_active_points(config['export'].get('zero_origin_active_points',False))
                for key in ('up','mid','low'):
                    if config['electrodes'][key]['mode'] == 'fixed':
                        spin = module.thickness_spins[key]
                        spin.blockSignals(True); spin.setValue(config['electrodes'][key]['value_um']); spin.blockSignals(False)
                module.tolerance_spin.blockSignals(True)
                module.tolerance_spin.setValue(config['calculation']['tolerance_mm'])
                module.tolerance_spin.blockSignals(False)
                module._sync_slot_labels(); module._apply_vig_payload(snapshot['result'])
                for index, descriptor in enumerate(self.module_descriptors):
                    if descriptor.get('key') == module.key: self.tabs.setCurrentIndex(index)
            elif snapshot['module']=='parallelism':
                self.parallel_base=records['base'];self.parallel_measure=records['measure']
                self._update_parallel_ui();self._apply_parallel_result(snapshot['result'])
                self.tabs.setCurrentIndex(self.parallel_tab_index)
            else:
                self.data_stack=records['stack'];self.data_base1=records['base1'];self.data_base2=records.get('base2')
                self.gap_result=snapshot['result'];self.spin_tol.blockSignals(True);self.spin_tol.setValue(self.gap_result['tolerance']);self.spin_tol.blockSignals(False)
                self._update_gap_slot_labels()
                self._refresh_gap_registration(preserve_view=False,diagnostic=snapshot.get('display_diagnostic'))
                self._sync_gap_action_state();self.tabs.setCurrentIndex(self.math_tab_index)
        # The result table is also the display of successful group metrics.
        # Failed/cancelled rows contain no metric values.
        dialog=QDialog(self);dialog.setWindowTitle('组合运行结果');dialog.resize(1100,540)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel('输出：'+payload['output_dir']))
        results=payload['results'];metric_keys=sorted({k for r in results for k in r['metrics']})
        columns=['sample_id','status','reason','quality','warnings',*metric_keys]
        table=QTableWidget(len(results),len(columns));table.setHorizontalHeaderLabels(['样品编号','状态','原因','结果质量','警告',*metric_keys]);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        labels={'success':'成功','failed':'失败','cancelled':'取消','not_run':'未运行','skipped':'跳过'}
        for i,row in enumerate(results):
            values=dict(row,**row['metrics'])
            values['quality']=' / '.join(f'{role}: {quality.get("label","")}' for role,quality in row.get('quality',{}).items())
            values['warnings']='; '.join(row.get('warnings',[]))
            values['status']=labels.get(row['status'],row['status'])
            for c,key in enumerate(columns):
                item=QTableWidgetItem(str(values.get(key,'')));item.setToolTip(item.text());table.setItem(i,c,item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);layout.addWidget(table,1)
        note=QLabel('各组结果、实际输入哈希、槽参数、质量标记与配准记录见独立目录中的 run.json；汇总见 summary.csv。')
        note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();open_button=QPushButton('打开输出目录');open_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(payload['output_dir'])))
        close=QPushButton('关闭');close.clicked.connect(dialog.accept);row.addWidget(open_button);row.addWidget(close);layout.addLayout(row)
        self._composite_results_dialog=dialog;dialog.open()
