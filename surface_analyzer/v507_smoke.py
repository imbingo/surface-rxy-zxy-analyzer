"""Opt-in, isolated GUI/report/VIG subprocess acceptance for source and EXE."""
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path


def run(extension, output):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import numpy as np
    import pandas as pd
    from PyQt6.QtCore import QSettings, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication
    from .app import SurfaceAnalyzerPro
    from .composite_runner import process_group, load_snapshot
    from .composite_recipe import required_roles
    from .composite_dialog import CompositeRunDialog
    from .display_metrics import normalize_single_config
    from .mixins.gap import GapAnalysisMixin
    from .version import APP_VERSION, SOURCE_COMMIT

    out = Path(output).resolve() if output else Path.cwd() / 'v507-check.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    captures = out.parent / (out.stem + '-views')
    captures.mkdir(exist_ok=True)
    application = QApplication.instance() or QApplication([])
    cjk = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if cjk.exists():
        from matplotlib import font_manager
        for name in ('Microsoft YaHei', 'SimHei'):
            font_manager.fontManager.ttflist.insert(0,font_manager.FontEntry(fname=str(cjk),name=name))
    old_local = os.environ.get('LOCALAPPDATA')
    window = None
    payload = dict(ok=False, version=APP_VERSION, source_commit=SOURCE_COMMIT)
    try:
        with tempfile.TemporaryDirectory(prefix='surface-v507-check-') as folder:
            root = Path(folder)
            os.environ['LOCALAPPDATA'] = folder
            if extension:
                shutil.copytree(Path(extension).resolve(), root/'SurfaceRxyZxyAnalyzer/module_extensions/vig-image')
            settings = QSettings(str(root/'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('use_system_frame', True)
            settings.setValue('judgment/spec', '{"rx":1}')
            window = SurfaceAnalyzerPro(settings=settings)
            def notice(level, title, text):
                if level == 'critical': raise RuntimeError(title + ': ' + text)
            window._workflow_notice = notice
            window._confirm_estimated_metrics = lambda action: True
            window.auto_sample_large_text = False
            window.import_x_unit = window.import_y_unit = window.import_z_unit = 'mm'
            assert not window.use_system_frame
            assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
            assert len(window._single_metric_config()['visible']) == 6
            assert all(v is None for v in window._single_metric_config()['limits'].values())
            assert window.current_judgment('single')['verdict'] == 'unset'
            x, y = np.meshgrid(np.linspace(-3,3,7), np.linspace(-2,2,7))
            x, y = x.ravel(), y.ravel()
            def load(name, z):
                path = root/name
                pd.DataFrame(dict(X=x,Y=y,Z=np.broadcast_to(z,x.shape))).to_csv(path,index=False)
                assert window.load_path(str(path))
                deadline=time.monotonic()+45
                while window._task_thread is not None and time.monotonic()<deadline:
                    application.processEvents()
                application.processEvents()
                assert window._task_thread is None and window.current_source_name==name
                assert window.last_metrics is not None
                return str(path)
            load('curved.csv',.4+.012*x-.007*y+.0002*x*x)
            window.resize(1600,950); window.show()
            for _ in range(3): application.processEvents()
            def capture(name):
                for _ in range(3): application.processEvents()
                assert window.grab().save(str(captures/(name+'.png')))
            for theme in ('light','dark'):
                window.set_theme_preference(theme,persist=False)
                capture('single-'+theme)
                window.cb_surface_display.showPopup();application.processEvents()
                popup=window.cb_surface_display.view()
                assert popup.model().rowCount() == 4
                colors=popup.palette()
                text=colors.color(QPalette.ColorRole.Text)
                base=colors.color(QPalette.ColorRole.Base)
                assert abs(text.lightness()-base.lightness()) > 60
                assert popup.window().grab().save(str(captures/('popup-'+theme+'.png')))
                window.cb_surface_display.hidePopup()
            window.set_theme_preference('light',persist=False)
            window.set_single_metric_config(normalize_single_config({'visible':['pv','std3']}))
            window.resize(1180,820);capture('single-two-metrics')
            window.set_single_metric_config(normalize_single_config())
            window._set_system_frame_enabled(True);assert window.use_system_frame
            window._set_system_frame_enabled(False);assert not window.use_system_frame
            window.showMinimized();application.processEvents();window.showNormal()
            window.showMaximized();application.processEvents();window.showNormal()
            def record(name,z):
                return dict(x=x.copy(),y=y.copy(),z=np.broadcast_to(z,x.shape).copy(),n=len(x),name=name,offset_x=0.,offset_y=0.)
            stack=record('总成_'+('long-source-name-'*12)+'.csv',.8+.001*x)
            base=record('单片.csv',.4+.0001*y)
            gap=GapAnalysisMixin._compute_gap_payload(stack,base,None,.01,lambda *a:None,threading.Event())
            window._apply_gap_payload(gap)
            assert window.tabs.currentIndex() == 0
            assert not hasattr(window, 'gap_result_figure')
            figure=window._render_report_figure(window.current_source_name,
                window.df_raw['X'].to_numpy(),window.df_raw['Y'].to_numpy(),window.df_raw['Z'].to_numpy(),
                window.active_idx,window.compute_plane_metrics(window.df_raw['X'].to_numpy(),window.df_raw['Y'].to_numpy(),window.df_raw['Z'].to_numpy()),window.n_filtered,'原始状态','关闭',window.import_info,display_surface_mode='raw')
            figure.savefig(captures/'thickness-main-report.png',dpi=120);figure.clear()
            capture('thickness-main-copy')
            for rec in (stack,base):
                rec['metrics']=window.compute_plane_metrics(rec['x'],rec['y'],rec['z'])
                rec['metric_quality']={'estimated':False,'label':'全量计算'}
            window.parallel_base=base;window.parallel_measure=stack
            window.parallel_result=window._compute_parallel_result()
            figure=window._render_parallel_report_figure();figure.canvas.draw()
            figure.savefig(captures/'parallel-report.png',dpi=120);figure.clear()
            payload.update(parallel_report=True)
            payload.update(startup_defaults=True,themes=True,window_controls=True,thickness_main_copy=True)
            if extension:
                module=window._vig_composite_module()
                files={}
                files['two']=load('两层.csv',(.170+.003*y)+.230);module.write_slot('two')
                files['three']=load('三层.csv',(.040+.002*x)+(.170+.003*y)+.350);module.write_slot('three')
                files['up']=load('上电极.csv',.120);module.write_slot('up')
                module.thickness_spins['mid'].setValue(150.)
                module.thickness_spins['low'].setValue(80.)
                window._set_export_origin_zero_active_points(True)
                recipe=window._compose_recipe('vig','Frozen VIG QA',export={'csv':True,'report':True})
                assert required_roles(recipe) == ('two','three','up')
                result=module.compute_snapshot(module.slots,recipe)
                module._apply_vig_payload(result)
                assert result['n_points'] == len(x)
                original=result['image'].copy()
                figure=module._render_vig_report_figure();figure.canvas.draw()
                figure.savefig(captures/'vig-four-view-report.png',dpi=120);figure.clear()
                window.tabs.setCurrentIndex(next(i for i,d in enumerate(window.module_descriptors) if d['key']==module.key));capture('vig-results')
                from copy import deepcopy
                partial_records=deepcopy(module.slots)
                bad=partial_records['three'];mask=np.asarray(bad['x'])>0
                bad['z'][mask]=np.asarray(partial_records['two']['z'])[mask]+np.asarray(partial_records['up']['z'])[mask]-.01
                partial=module.compute_snapshot(partial_records,recipe)
                assert partial['stage_counts']['invalid_input']==int(mask.sum())
                module._apply_vig_payload(partial);capture('vig-partial-diagnostics')
                figure=module._render_vig_report_figure();figure.savefig(captures/'vig-partial-report.png',dpi=120);figure.clear()
                module._apply_vig_payload(result)
                payload.update(vig_invalid_spacing=True,vig_region_diagnostics=True)
                dialog=CompositeRunDialog(window,recipe)
                dialog.resize(1300,800);dialog.show();application.processEvents()
                dialog.grab().save(str(captures/'vig-composite-table.png'));dialog.close()
                assert module.load_secondary()
                np.testing.assert_allclose(window.df_raw.Z,original*.001)
                window.manual_mask[0]=False;window.update_analysis()
                np.testing.assert_array_equal(result['image'],original)
                assert window._slot_single_recipe_snapshot() is None
                capture('vig-secondary-copy')
                tx,ty,tz=window.get_final_transformed_data(window.df_raw)
                figure=window._render_report_figure(window.current_source_name,tx,ty,tz,
                    window.active_idx,window.compute_plane_metrics(tx[window.active_idx],ty[window.active_idx],tz[window.active_idx]),
                    window.n_filtered,'原始状态','关闭',window.import_info)
                figure.savefig(captures/'vig-secondary-report.png',dpi=100);figure.clear()
                child_dir=captures/'vig-child';child_dir.mkdir(exist_ok=True)
                child=process_group(recipe,files,child_dir,lambda *a:None,threading.Event(),timeout=120)
                assert child['status']=='success',child
                frame=pd.read_csv(child_dir/'result.csv',comment='#')
                assert np.isclose(frame.X_mm.min(),0.) and np.isclose(frame.Y_mm.min(),0.)
                saved=load_snapshot(child_dir)
                np.testing.assert_allclose(saved['result']['image'],original)
                payload.update(vig_loaded=True,vig_four_graphs=True,secondary_copy=True,
                               vig_child=True,electrode_file_fixed_modes=True,points=len(original))
            else:
                try: window._vig_composite_module()
                except ValueError: payload['without_vig']=True
                else: raise AssertionError('Optional extension unexpectedly loaded')
            window.close();window=None;application.processEvents()
            payload['ok']=True
    except Exception as exc:
        import traceback
        payload.update(error=str(exc),traceback=traceback.format_exc())
    finally:
        if window is not None:window.close()
        application.processEvents()
        if old_local is None:os.environ.pop('LOCALAPPDATA',None)
        else:os.environ['LOCALAPPDATA']=old_local
        out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if payload['ok'] else 20
