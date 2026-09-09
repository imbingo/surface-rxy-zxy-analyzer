"""Adjustment workflow panel. Fixture settings are separate from DUT results."""
import csv
import io
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QFileDialog, QApplication, QCheckBox)
from PyQt6.QtGui import QColor
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from .widgets import NoWheelComboBox, NoWheelDoubleSpinBox
from .adjustment import (Plane, SupportPoint, AdjustmentConfig, calculate_adjustment,
                         rad_to_urad, mm_to_um)


def default_fixture():
    return {'enabled': True, 'target_mode': 'reference_surface',
            'reference_origin_mode': 'support_centroid',
            'reference_origin_x_mm': 0., 'reference_origin_y_mm': 0.,
            'target_rx_urad': 0., 'target_ry_urad': 0., 'target_z0_um': 0.,
            'supports': [asdict(SupportPoint(f'P{i}', x, y, 100, 0, 500))
                         for i, (x,y) in enumerate([(-80,-50),(80,-50),(0,70)], 1)],
            'quantization': {'enabled': False, 'mode': 'fixed_step', 'default_step_um': 5.},
            'coplanarity_warn_um': 5.}


def validate_fixture(data):
    """Validate before any UI/Recipe mutation; empty editing layouts are allowed."""
    if not isinstance(data, dict):
        raise ValueError('装调配置必须为对象')
    cfg = default_fixture()
    cfg.update(data)
    if cfg['target_mode'] not in ('reference_surface', 'custom'):
        raise ValueError('未知装调目标')
    if cfg['reference_origin_mode'] not in ('support_centroid','coordinate_origin','custom'):
        raise ValueError('未知装调原点')
    for key in ('reference_origin_x_mm','reference_origin_y_mm','target_rx_urad',
                'target_ry_urad','target_z0_um','coplanarity_warn_um'):
        cfg[key] = float(cfg[key])
        if not np.isfinite(cfg[key]) or abs(cfg[key]) > 1e9:
            raise ValueError(f'无效装调数值：{key}')
    if cfg['coplanarity_warn_um'] < 0:
        raise ValueError('共面性阈值不能小于0')
    Plane.from_pose(cfg['target_rx_urad'],cfg['target_ry_urad'],cfg['target_z0_um'],
                    (cfg['reference_origin_x_mm'],cfg['reference_origin_y_mm']))
    q = {'enabled': False, 'mode': 'fixed_step', 'default_step_um': 5.}
    q.update(cfg['quantization'])
    q['default_step_um'] = float(q['default_step_um'])
    if q['mode'] != 'fixed_step' or not 0 < q['default_step_um'] <= 1e9:
        raise ValueError('无效垫片量化模式或步进')
    cfg['quantization'] = q
    points=[]
    for raw in cfg['supports']:
        p=dict(raw)
        if 'shim_step_um' in p:
            p['step_um']=p.pop('shim_step_um')
        for key in ('x_mm','y_mm','base_shim_um','min_shim_um','max_shim_um'):
            p[key]=float(p[key])
        if p.get('step_um') is not None:p['step_um']=float(p['step_um'])
        point=SupportPoint(**p)
        if not point.name.strip():
            raise ValueError('调整点名称不能为空')
        values=(point.x_mm,point.y_mm,point.base_shim_um,point.min_shim_um,point.max_shim_um)
        if not all(np.isfinite(float(v)) for v in values):
            raise ValueError('调整点必须为有限数值')
        if point.min_shim_um > point.max_shim_um:
            raise ValueError('最小厚度不能大于最大厚度')
        if point.step_um is not None and (not np.isfinite(point.step_um) or point.step_um <= 0):
            raise ValueError('步进必须大于0')
        points.append(asdict(point))
    if len({p['name'] for p in points}) != len(points):
        raise ValueError('调整点名称不能重复')
    cfg['supports']=points
    return cfg


class AdjustmentPanel(QWidget):
    columns=('名称','X (mm)','Y (mm)','基础垫片 (µm)','最小厚度 (µm)','最大厚度 (µm)','步进 (µm)')
    keys=('name','x_mm','y_mm','base_shim_um','min_shim_um','max_shim_um','step_um')

    def __init__(self, host):
        super().__init__(host)
        self.host=host
        self.result=None
        self._loading=True
        self.context={}
        layout=QVBoxLayout(self)
        self.enabled=QCheckBox('启用装调计算'); self.enabled.setChecked(True)
        layout.addWidget(self.enabled)
        form=QGridLayout(); layout.addLayout(form)
        self.form=form;self.form_pairs=[];self._compact=None
        def combo(label, values, row, col):
            widget=NoWheelComboBox()
            for text,key in values: widget.addItem(text,key)
            lab=QLabel(label);form.addWidget(lab,row,col);form.addWidget(widget,row,col+1)
            self.form_pairs.append((lab,widget))
            widget.currentIndexChanged.connect(self.changed)
            return widget
        def spin(label,row,col,value=0,minimum=-1e9):
            widget=NoWheelDoubleSpinBox();widget.setRange(minimum,1e9)
            widget.setDecimals(6);widget.setValue(value)
            lab=QLabel(label);form.addWidget(lab,row,col);form.addWidget(widget,row,col+1)
            self.form_pairs.append((lab,widget))
            widget.valueChanged.connect(self.changed)
            return widget
        self.target=combo('目标',[('测量面调整至基准面','reference_surface'),('自定义目标','custom')],0,0)
        self.origin=combo('参考原点',[('支撑点几何中心','support_centroid'),('坐标原点 (0,0)','coordinate_origin'),('自定义','custom')],0,2)
        self.x0=spin('X0 (mm)',1,0);self.y0=spin('Y0 (mm)',1,2)
        self.rx=spin('目标 Rx (µrad)',2,0);self.ry=spin('目标 Ry (µrad)',2,2)
        self.z0=spin('目标 Z0 (µm)',3,0)
        self.quant=combo('垫片量化',[('不量化','none'),('固定步进','fixed_step')],3,2)
        self.step=spin('默认步进 (µm)',4,0,5,0.000001)
        self.threshold=spin('共面 PV 警告阈值 (µm)',4,2,5,0)
        note=QLabel('坐标必须为真实支撑 / 垫片接触点，且与两面处于同一坐标系；模板坐标须按工装修改。步进留空使用默认值。')
        note.setWordWrap(True);layout.addWidget(note)
        bar=QGridLayout();layout.addLayout(bar);self.point_bar=bar;self.point_buttons=[]
        for label,fn in [('三点模板',lambda:self.template(3)),('四角模板',lambda:self.template(4)),
                         ('添加点',self.add_point),('复制选中',self.duplicate),('删除选中',self.delete),('清空',self.clear)]:
            b=QPushButton(label);b.clicked.connect(fn);bar.addWidget(b,0,len(self.point_buttons));self.point_buttons.append(b)
        self.supports=QTableWidget(0,7);self.supports.setHorizontalHeaderLabels(self.columns)
        self.supports.setToolTip('填写真实支撑点 / 垫片接触点的 XY 坐标，不是工件外轮廓四角。坐标必须与拟合面处于同一坐标系。')
        self.supports.setMinimumHeight(170)
        self.supports.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.supports.horizontalHeader().setMinimumSectionSize(100)
        self.supports.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.supports.itemChanged.connect(self.changed);layout.addWidget(self.supports)
        self.origin_label=QLabel();self.origin_label.setWordWrap(True);layout.addWidget(self.origin_label)
        actions=QHBoxLayout();layout.addLayout(actions)
        self.calculate_button=QPushButton('计算装调量');self.calculate_button.setObjectName('accentBtn')
        self.calculate_button.clicked.connect(self.calculate);actions.addWidget(self.calculate_button)
        self.export_button=QPushButton('导出装调 CSV');self.export_button.clicked.connect(self.export_csv);actions.addWidget(self.export_button)
        self.copy_button=QPushButton('复制装调结果');self.copy_button.clicked.connect(self.copy_result);actions.addWidget(self.copy_button)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.cards=QLabel();self.cards.setWordWrap(True);self.cards.setStyleSheet('background:#eaf3fc;padding:12px;color:#163b62;');layout.addWidget(self.cards)
        self.results=QTableWidget(0,13)
        self.results.setHorizontalHeaderLabels(['点位','X mm','Y mm','当前 Z µm','目标 Z µm','当前误差 E µm','理论调整 ΔH µm','基础 µm','理论厚度 µm','推荐厚度 µm','实际调整 µm','量化误差 µm','状态'])
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setMinimumHeight(180);layout.addWidget(self.results)
        self.figure=Figure(figsize=(8,4));self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(340);layout.addWidget(self.canvas)
        self.enabled.toggled.connect(self.changed)
        self.load_fixture(default_fixture())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        compact=self.width()<800
        if compact==self._compact:return
        self._compact=compact
        for lab,widget in self.form_pairs:
            self.form.removeWidget(lab);self.form.removeWidget(widget)
        for i,(lab,widget) in enumerate(self.form_pairs):
            row,col=(i,0) if compact else (i//2,(i%2)*2)
            self.form.addWidget(lab,row,col);self.form.addWidget(widget,row,col+1)
        for b in self.point_buttons:self.point_bar.removeWidget(b)
        for i,b in enumerate(self.point_buttons):
            self.point_bar.addWidget(b,i//3 if compact else 0,i%3 if compact else i)

    def fixture(self):
        points=[]
        for row in range(self.supports.rowCount()):
            raw={}
            for col,key in enumerate(self.keys):
                item=self.supports.item(row,col)
                text=item.text().strip() if item else ''
                raw[key]=text if col==0 else None if col==6 and not text else float(text)
            points.append(raw)
        return validate_fixture({'enabled':self.enabled.isChecked(),'target_mode':self.target.currentData(),
            'reference_origin_mode':self.origin.currentData(),'reference_origin_x_mm':self.x0.value(),
            'reference_origin_y_mm':self.y0.value(),'target_rx_urad':self.rx.value(),
            'target_ry_urad':self.ry.value(),'target_z0_um':self.z0.value(),'supports':points,
            'quantization':{'enabled':self.quant.currentData()=='fixed_step','mode':'fixed_step','default_step_um':self.step.value()},
            'coplanarity_warn_um':self.threshold.value()})

    def load_fixture(self,data):
        cfg=validate_fixture(data)
        self._loading=True
        try:
            self.enabled.setChecked(cfg['enabled'])
            for widget,key in [(self.target,'target_mode'),(self.origin,'reference_origin_mode')]:
                widget.setCurrentIndex(widget.findData(cfg[key]))
            for widget,key in [(self.x0,'reference_origin_x_mm'),(self.y0,'reference_origin_y_mm'),
                               (self.rx,'target_rx_urad'),(self.ry,'target_ry_urad'),(self.z0,'target_z0_um'),(self.threshold,'coplanarity_warn_um')]:
                widget.setValue(cfg[key])
            self.quant.setCurrentIndex(int(bool(cfg['quantization']['enabled'])))
            self.step.setValue(cfg['quantization']['default_step_um'])
            self.supports.setRowCount(0)
            for p in cfg['supports']:self._append(p)
        finally:self._loading=False
        self.changed()

    def _append(self,p):
        row=self.supports.rowCount();self.supports.insertRow(row)
        for col,key in enumerate(self.keys):
            value=p.get(key)
            self.supports.setItem(row,col,QTableWidgetItem('' if value is None else str(value)))

    def add_point(self):
        names={self.supports.item(i,0).text() for i in range(self.supports.rowCount()) if self.supports.item(i,0)}
        index=1
        while f'P{index}' in names:index+=1
        self._append(asdict(SupportPoint(f'P{index}',0,0,100,0,500)))

    def duplicate(self):
        row=self.supports.currentRow()
        if row<0:return
        values=[self.supports.item(row,c).text() for c in range(7)]
        self.add_point();new=self.supports.rowCount()-1
        for c in range(1,7):self.supports.item(new,c).setText(values[c])

    def delete(self):
        for row in sorted({i.row() for i in self.supports.selectedIndexes()},reverse=True):self.supports.removeRow(row)
        self.changed()

    def clear(self):
        self.supports.setRowCount(0);self.changed()

    def template(self,n):
        self._loading=True
        self.supports.setRowCount(0)
        xy=[(-80,-50),(80,-50),(0,70)] if n==3 else [(-80,-50),(80,-50),(-80,50),(80,50)]
        for i,(x,y) in enumerate(xy,1):self._append(asdict(SupportPoint(f'P{i}',x,y,100,0,500)))
        self._loading=False;self.changed()

    def invalidate(self,message='输入已变化，请重新计算装调量。'):
        self.result=None;self.context={}
        self.results.setRowCount(0);self.cards.setText('调整前 → 预计残余：待计算')
        self.figure.clear();self.canvas.draw_idle()
        self.status.setText(message);self.status.setStyleSheet('color:#73521a;')
        self.export_button.setEnabled(False);self.copy_button.setEnabled(False)
        self.calculate_button.setEnabled(self.enabled.isChecked() and self.host.parallel_result is not None
                                         and self.host.parallel_base is not None and self.host.parallel_measure is not None)

    def changed(self,*args):
        if self._loading:return
        custom=self.target.currentData()=='custom'
        for widget in (self.rx,self.ry,self.z0):widget.setEnabled(custom)
        for widget in (self.x0,self.y0):widget.setEnabled(self.origin.currentData()=='custom')
        self.step.setEnabled(self.quant.currentData()=='fixed_step')
        self.invalidate()
        try:
            cfg=self.fixture();points=cfg['supports']
            origin=self._origin(cfg)
            self.origin_label.setText(f'实际参考原点：X0={origin[0]:.6f} mm，Y0={origin[1]:.6f} mm；E=当前−目标，ΔH=目标−当前；正数加，负数抽。')
            if not points:self.origin_label.setText('请添加至少三个非共线的真实支撑点。')
        except (ValueError,TypeError):self.origin_label.setText('输入尚未完整，请检查调整点和参数。')

    def _origin(self,cfg):
        if cfg['reference_origin_mode']=='coordinate_origin':return (0.,0.)
        if cfg['reference_origin_mode']=='custom':return cfg['reference_origin_x_mm'],cfg['reference_origin_y_mm']
        if not cfg['supports']:raise ValueError('请添加调整点')
        return tuple(np.mean([(p['x_mm'],p['y_mm']) for p in cfg['supports']],axis=0))

    def calculate(self):
        self.invalidate()
        if not self.calculate_button.isEnabled():
            self.status.setText('请先设置基准面、测量面并完成平行度计算。');return
        try:
            cfg=self.fixture()
            m=self.host.parallel_measure['metrics'];b=self.host.parallel_base['metrics']
            current=Plane(m['a'],m['b'],m['c'])
            target=Plane(b['a'],b['b'],b['c']) if cfg['target_mode']=='reference_surface' else Plane.from_pose(cfg['target_rx_urad'],cfg['target_ry_urad'],cfg['target_z0_um'],self._origin(cfg))
            options=AdjustmentConfig(cfg['reference_origin_mode'],cfg['reference_origin_x_mm'],cfg['reference_origin_y_mm'],
                                     'fixed_step' if cfg['quantization']['enabled'] else 'none',cfg['quantization']['default_step_um'],cfg['coplanarity_warn_um'])
            self.result=calculate_adjustment(current,target,[SupportPoint(**p) for p in cfg['supports']],options)
            self.context={'timestamp':datetime.now().astimezone().isoformat(),'recipe':getattr(self.host,'adjustment_recipe_name',''),
                          'project':getattr(self.host,'current_source_name',''),'target_mode':cfg['target_mode'],
                          'fixture':cfg,'base_source':self.host.parallel_base.get('name',''),
                          'measure_source':self.host.parallel_measure.get('name',''),
                          'estimated':bool(self.host.parallel_result.get('estimated'))}
            self.render()
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            self.invalidate(str(exc))

    def render(self):
        r=self.result;c=r.coplanarity
        pose=lambda p:f'Rx {p.rx_urad:+.4f} µrad / Ry {p.ry_urad:+.4f} µrad / ΔZ0 {p.dz0_um:+.4f} µm'
        self.cards.setText(f'调整前（当前−目标）：{pose(r.before_error)}\n预计残余（预计−目标）：{pose(r.residual_pose)}\n最大垫片变化 {max(abs(p.actual_adjustment_um) for p in r.support_results):.4f} µm ｜ 支撑共面 PV {c.pv_um:.4f} / RMS {c.rms_um:.4f} / 最大偏离 {c.max_abs_um:.4f} µm ｜ 最差点 {c.worst_point}')
        warnings=list(r.warnings)
        if self.context.get('estimated'):warnings.insert(0,'输入面包含抽样估计，装调预测同样为估计结果。')
        self.status.setText('\n'.join(warnings) or '计算完成：几何预测有效。')
        self.status.setStyleSheet('color:#a34f00;background:#fff4df;padding:8px;' if warnings else 'color:#166534;')
        self.results.setRowCount(len(r.support_results))
        for i,p in enumerate(r.support_results):
            values=[p.support.name,p.support.x_mm,p.support.y_mm,p.current_z_um,p.target_z_um,p.error_height_um,
                    p.theoretical_adjustment_um,p.support.base_shim_um,p.ideal_shim_um,p.recommended_shim_um,p.actual_adjustment_um,p.quantization_error_um,'；'.join(p.statuses)]
            for j,value in enumerate(values):
                item=QTableWidgetItem(value if isinstance(value,str) else f'{value:.6f}')
                if any('厚度' in s and ('超过' in s or '低于' in s) for s in p.statuses):item.setForeground(QColor('#b91c1c'))
                self.results.setItem(i,j,item)
        self.results.resizeColumnsToContents()
        self.figure.clear();ax=self.figure.add_subplot(111,projection='3d')
        points=r.support_results;x=np.array([p.support.x_mm for p in points]);y=np.array([p.support.y_mm for p in points]);h=np.array([p.theoretical_adjustment_um for p in points])
        xx,yy=np.meshgrid(np.linspace(x.min(),x.max(),12),np.linspace(y.min(),y.max(),12))
        ax.plot_surface(xx,yy,mm_to_um(r.adjustment_plane.at(xx,yy)),color='#7cb5ec',alpha=.3)
        ax.scatter(x,y,h,c=['#c2410c' if v<0 else '#2563eb' for v in h],depthshade=False)
        ax.quiver(x,y,np.zeros_like(h),np.zeros_like(h),np.zeros_like(h),h,color='#2563eb',arrow_length_ratio=.15)
        for p in points:ax.text(p.support.x_mm,p.support.y_mm,p.theoretical_adjustment_um,f'{p.support.name} {p.theoretical_adjustment_um:+.2f} µm')
        ax.set(xlabel='X (mm)',ylabel='Y (mm)',zlabel='ΔH (µm)',title='装调补偿预览：正数加 / 负数抽')
        self.figure.tight_layout();self.canvas.draw_idle()
        self.export_button.setEnabled(True);self.copy_button.setEnabled(True)

    def csv_text(self):
        if self.result is None:raise ValueError('装调结果已失效，请重新计算')
        r=self.result;stream=io.StringIO();w=csv.writer(stream)
        w.writerow(['装调结果','Error=Current-Target; Adjustment=Target-Current; Residual=Predicted-Target'])
        for key,value in self.context.items():w.writerow([key,json.dumps(value,ensure_ascii=False) if isinstance(value,dict) else value])
        w.writerow(['ReferenceOriginX_mm','ReferenceOriginY_mm']);w.writerow(r.reference_origin)
        for name,plane in [('Current',r.current_plane),('Target',r.target_plane)]:
            w.writerow([name,'Rx_urad','Ry_urad','Z0_um','a','b','c_mm'])
            w.writerow(['',rad_to_urad(np.arctan(plane.b)),rad_to_urad(np.arctan(-plane.a)),mm_to_um(plane.at(*r.reference_origin)),plane.a,plane.b,plane.c])
        for name,p in [('Error',r.before_error),('PredictedResidual',r.residual_pose)]:
            w.writerow([name,'Rx_urad','Ry_urad','DZ0_um']);w.writerow(['',p.rx_urad,p.ry_urad,p.dz0_um])
        w.writerow(['Name','X_mm','Y_mm','CurrentZ_um','TargetZ_um','ErrorHeight_um','TheoreticalAdjustment_um','BaseShim_um','IdealShim_um','RecommendedShim_um','ActualAdjustment_um','QuantizationError_um','Status'])
        for p in r.support_results:w.writerow([p.support.name,p.support.x_mm,p.support.y_mm,p.current_z_um,p.target_z_um,p.error_height_um,p.theoretical_adjustment_um,p.support.base_shim_um,p.ideal_shim_um,p.recommended_shim_um,p.actual_adjustment_um,p.quantization_error_um,'; '.join(p.statuses)])
        for key,value in asdict(r.coplanarity).items():w.writerow(['SupportCoplanarity_'+key,value])
        w.writerow(['Executable',r.executable]);w.writerow(['Warnings','; '.join(r.warnings)])
        return stream.getvalue()

    def copy_result(self):
        if self.result is None:return
        r=self.result
        text=f"装调目标：{'测量面 → 基准面' if self.context['target_mode']=='reference_surface' else '自定义目标'}\n参考原点：{self.origin_label.text()}\n{self.cards.text()}\n"
        for p in r.support_results:text+=f'{p.support.name}: 理论调整 {p.theoretical_adjustment_um:+.4f} µm；基础 {p.support.base_shim_um:g} → 理论 {p.ideal_shim_um:.4f} → 推荐实际 {p.recommended_shim_um:.4f} µm；实际调整 {p.actual_adjustment_um:+.4f} µm；{"；".join(p.statuses)}\n'
        QApplication.clipboard().setText(text+self.status.text())

    def export_csv(self):
        if self.result is None:return
        path,_=QFileDialog.getSaveFileName(self,'导出装调 CSV','adjustment.csv','CSV (*.csv)')
        if not path:return
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8-sig',newline='',dir=Path(path).parent,delete=False) as f:
                tmp=f.name;f.write(self.csv_text())
            os.replace(tmp,path);tmp=None
            self.host.statusBar().showMessage('装调 CSV 已导出',5000)
        except OSError as exc:self.status.setText(f'导出失败：{exc}')
        finally:
            if tmp:os.unlink(tmp)
