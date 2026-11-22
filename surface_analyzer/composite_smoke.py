"""Source/frozen acceptance of the real composite child-process protocol."""
import json
import os
import tempfile
import threading
from pathlib import Path


def run(output=None):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    import numpy as np
    import pandas as pd
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    from .app import SurfaceAnalyzerPro
    from .composite_recipe import build_composite
    from .composite_runner import run_groups,process_group,load_snapshot
    from .workers import TaskCancelled
    from .version import APP_VERSION,SOURCE_COMMIT
    app=QApplication.instance() or QApplication([])
    result=dict(version=APP_VERSION,source_commit=SOURCE_COMMIT)
    try:
        with tempfile.TemporaryDirectory(prefix='surface-composite-acceptance-') as directory:
            root=Path(directory)
            window=SurfaceAnalyzerPro(settings=QSettings(str(root/'settings.ini'),QSettings.Format.IniFormat))
            try:
                single=window._current_recipe_dict()
                single['column_mapping']=dict(x_col='X',y_col='Y',z_col='Z')
                single['units']=dict(x_unit='mm',y_unit='mm',z_unit='mm')
                single['large_file']['auto_sample']=False
            finally:window.close()
            x,y=np.meshgrid(np.arange(6.),np.arange(5.))
            def write(name,z):
                path=root/(name+'.csv')
                pd.DataFrame(dict(X=x.ravel(),Y=y.ravel(),Z=np.full(x.size,z))).to_csv(path,index=False)
                return str(path)
            def recipe(module,roles,base2=False):
                reg={} if module=='parallelism' else dict(mode='none',tolerance_mm=.05,offsets={r:[0.,0.] for r in roles[1:]})
                return build_composite(module,{r:dict(single_recipe=single) for r in roles},'Release acceptance',
                    include_base2=base2,registration=reg,app_version=APP_VERSION,export=dict(csv=True,report=True))
            parallel=recipe('parallelism',('base','measure'))
            groups=[]
            for i,step in enumerate((.7,1.2)):
                groups.append(dict(sample_id=f'G{i+1}',selected=True,
                    files=dict(base=write(f'base{i}',1.),measure=write(f'measure{i}',1.+step))))
            bad=root/'invalid.csv';bad.write_text('not measurement data',encoding='utf-8')
            groups.insert(1,dict(sample_id='Expected failure',selected=True,
                files=dict(base=write('failure_base',1.),measure=str(bad))))
            batch=run_groups(parallel,groups,root/'parallel',timeout=90)
            if [r['status'] for r in batch['results']]!=['success','failed','success']:
                raise RuntimeError('Frozen group isolation/continue failed: '+json.dumps(batch['results']))
            for index,expected in ((0,.7),(2,1.2)):
                row=batch['results'][index]
                if not np.isclose(row['metrics']['step_height_mm'],expected,atol=1e-10):
                    raise RuntimeError('Frozen parallel result differs from expected plane difference')
                if not Path(row['outputs']['report']).is_file():raise RuntimeError('Frozen report missing')
            if batch['results'][1]['metrics']:raise RuntimeError('Failure reused prior metrics')
            result['isolated_groups']=True;result['failure_continue']=True
            thickness=recipe('thickness',('stack','base1','base2'),True)
            gap=run_groups(thickness,[dict(sample_id='Three layers',selected=True,files=dict(
                stack=write('stack',2.),base1=write('base1',.4),base2=write('base2',.2)))],root/'gap',timeout=90)
            if gap['results'][0]['status']!='success':raise RuntimeError(str(gap['results'][0]))
            np.testing.assert_allclose(gap['snapshot']['result']['z'],1.4,rtol=0,atol=1e-12)
            result['three_layer_gap']=True;result['module_reports']=True
            cancel=threading.Event();folder=root/'cancel';folder.mkdir()
            try:
                process_group(parallel,groups[0]['files'],folder,lambda value,text:cancel.set(),cancel,timeout=90)
            except TaskCancelled:pass
            else:raise RuntimeError('Frozen child did not honor cancellation')
            (folder/'worker.log').unlink()  # open handles expose orphan processes on Windows
            result['child_cancel']=True
        result['ok']=True
    except Exception as exc:
        result.update(ok=False,error=str(exc))
    if output:
        target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 20
