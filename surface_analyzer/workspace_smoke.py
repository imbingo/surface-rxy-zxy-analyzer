"""Frozen executable acceptance check with isolated settings and synthetic data."""
import json
import os
import tempfile
import time
from pathlib import Path


def run(output=None):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    import numpy as np
    import pandas as pd
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    from . import app as app_module
    from .worksheet_dialog import DataPreviewDialog
    from .recipe_library import validate_recipe
    from .version import APP_VERSION, SOURCE_COMMIT
    application=QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='surface-workspace-check-') as folder:
        settings=QSettings(str(Path(folder)/'settings.ini'),QSettings.Format.IniFormat)
        original_settings=app_module.QSettings
        app_module.QSettings=lambda *args,**kwargs:settings
        window=dialog=None
        try:
            window=app_module.SurfaceAnalyzerPro()
            x,y=np.meshgrid(np.linspace(-2,2,21),np.linspace(-2,2,21));x=x.ravel();y=y.ravel()
            window.absolute_raw_df=pd.DataFrame(dict(X=x,Y=y,Up=.1+.0001*x*x,Down=.15+.0001*y))
            window.current_source_name='synthetic-workspace.csv'
            window.lbl_source.setText('当前数据: synthetic-workspace.csv')
            window.input_layout_mode='point_table'
            for combo,name in ((window.cb_x_col,'X'),(window.cb_y_col,'Y'),(window.cb_z_col,'Up')):
                combo.clear();combo.addItems(window.absolute_raw_df.columns);combo.setCurrentText(name)
            for combo in (window.cb_x_unit,window.cb_y_unit,window.cb_z_unit):combo.setCurrentText('mm')
            if not window.apply_mapping():raise RuntimeError('Initial mapping failed')
            dialog=DataPreviewDialog(window)
            def settle():
                deadline=time.monotonic()+30
                while dialog.thread is not None and time.monotonic()<deadline:
                    application.processEvents();time.sleep(.005)
                if dialog.thread is not None:raise RuntimeError('Worksheet worker timeout')
                application.processEvents()
            dialog.insert_column(4)
            identity=dialog.sheet.columns[4]['id']
            dialog.change(lambda sheet:sheet.rename(identity,'Z_sum'))
            dialog.begin_edit(dialog.table.model().index(0,4),'=')
            dialog.insert_reference(dialog.table.model().index(0,2))
            dialog.formula.insertPlainText('+')
            dialog.insert_reference(dialog.table.model().index(0,3))
            dialog.submit_formula();settle()
            dialog.fill_all(dialog.table.model().index(0,4));settle()
            def choose(sheet):
                sheet.column(identity)['unit']='mm';sheet.data['target_z']=identity
            dialog.change(choose)
            dialog.apply_analysis();settle()
            np.testing.assert_allclose(window.df_raw.Z,window.absolute_raw_df.Up+window.absolute_raw_df.Down)
            if window.cb_z_col.currentText()!='Z_sum':raise RuntimeError('Derived mapping failed')
            recipe=window._current_recipe_dict();validate_recipe(recipe)
            if recipe['schema_version']!=11:raise RuntimeError('Worksheet Recipe schema mismatch')
            dialog.allow_close=True;dialog.close();dialog=None
            z=window.df_raw.Z.to_numpy(copy=True);z[x>0]+=1.;z[-1]=8.
            window.df_raw=window.df_raw.copy();window.df_raw['Z']=z;window._df_version+=1
            window.roi_enabled=True
            window.roi_shapes=[dict(id=1,type='rectangle',view='XY',cx=-1.2,cy=0.,width=1.5,height=4.2,enabled=True)]
            window.update_analysis()
            if window._detail_color_limits[1]>=1.:raise RuntimeError('ROI color range includes other layer')
            window.manual_mask[-1]=False;window.update_analysis()
            if window._shared_color_limits[1]>=2.:raise RuntimeError('Deleted spike still affects colorbar')
            metrics=window.compute_plane_metrics(x[window.active_idx],y[window.active_idx],z[window.active_idx])
            keep=window._get_effective_roi_mask_cached(x,y,z,matrix_rc=None)
            figure=window._render_report_figure('synthetic-workspace',x,y,z,window.active_idx,metrics,0,'原始状态','关闭',{},
                overview_idx=np.flatnonzero(window.manual_mask),roi_mask_all=keep,render_config={'color_scale_mode':'auto'})
            bars=[axis._colorbar for axis in figure.axes if hasattr(axis,'_colorbar')]
            if len(bars)!=2:raise RuntimeError('Report ROI colorbar missing')
            figure.canvas.draw()
            if output:figure.savefig(str(Path(output).with_suffix('.report.png')),dpi=100)
            figure.clear()
            window.resize(1600,900);window.show()
            for _ in range(5):application.processEvents()
            width=window.main_splitter.sizes()[0]
            if abs(width-360)>4:raise RuntimeError('Default inspector width mismatch')
            if output:window.grab().save(str(Path(output).with_suffix('.png')))
            payload=dict(version=APP_VERSION,source_commit=SOURCE_COMMIT,worksheet=True,fill_across_pages=True,
                         recipe_schema=11,roi_colors=True,deletion_updates_colors=True,report_colorbars=2,
                         inspector_width=width,points=len(x))
            if output:Path(output).write_text(json.dumps(payload,indent=2),encoding='utf-8')
            return 0
        finally:
            if dialog is not None:
                if dialog.thread is not None:
                    dialog.cancel_task()
                    while dialog.thread is not None:application.processEvents();time.sleep(.005)
                dialog.allow_close=True;dialog.close()
            if window is not None:window.close()
            application.processEvents()
            app_module.QSettings=original_settings
