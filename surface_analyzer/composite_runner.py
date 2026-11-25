"""Isolated group execution using the real desktop single-layer workflow.

Each group owns a child process and each role a fresh SurfaceAnalyzerPro.
The parent only schedules completed processes; it never mutates single-layer
controls to drive an automation task. No second metrology implementation.
"""
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from uuid import uuid4

import numpy as np
import pandas as pd
from .composite_recipe import validate_composite, required_roles, validate_group
from .recipe_library import atomic_json
from .workers import TaskCancelled, sha256_file


def plain(value):
    if isinstance(value, np.generic):value=value.item()
    if isinstance(value, np.ndarray):return plain(value.tolist())
    if isinstance(value, dict):return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def save_snapshot(folder, value):
    arrays={}
    def pack(item):
        if isinstance(item,np.ndarray):
            key=f'a{len(arrays)}';arrays[key]=item
            return {'__array__':key}
        if isinstance(item,dict):return {str(k):pack(v) for k,v in item.items()}
        if isinstance(item,(list,tuple)):return [pack(v) for v in item]
        return plain(item)
    metadata=pack(value);folder=Path(folder)
    temporary=folder/'snapshot.tmp.npz'
    np.savez(temporary,**arrays);os.replace(temporary,folder/'snapshot.npz')
    atomic_json(folder/'snapshot.json',metadata)


def load_snapshot(folder):
    folder=Path(folder)
    with np.load(folder/'snapshot.npz',allow_pickle=False) as arrays:
        def unpack(item):
            if isinstance(item,dict):
                if set(item)=={'__array__'}:return arrays[item['__array__']].copy()
                return {k:unpack(v) for k,v in item.items()}
            if isinstance(item,list):return [unpack(v) for v in item]
            return item
        return unpack(json.loads((folder/'snapshot.json').read_text(encoding='utf-8')))


def execute_group(recipe, files, output, report=lambda value,text:None):
    """Called only in an isolated GUI-capable child, or an isolated test."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    from .app import SurfaceAnalyzerPro
    validate_composite(recipe)
    errors=validate_group(dict(files=files),recipe)
    if errors:raise ValueError('; '.join(errors))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    roles=required_roles(recipe);records={};provenance={};windows=[];warnings=[]
    app=QApplication.instance() or QApplication([])
    class RoleWorkspace(SurfaceAnalyzerPro):
        def _workflow_notice(self, level, title, text):
            if level=='critical':raise ValueError(f'{title}: {text}')
            warnings.append(f'{title}: {text}')
        def _show_status(self, message, timeout=0):
            self._automation_last_status=str(message)
            report(int(index*60/len(roles)+10),str(message))
    try:
        for index,role in enumerate(roles):
            report(int(index*60/len(roles)),f'{role}：加载单层 Recipe')
            settings=QSettings(str(output/f'.{role}.ini'),QSettings.Format.IniFormat)
            w=RoleWorkspace(settings=settings);windows.append(w)
            w._automation_mode=True
            if recipe['module'] == 'vig': w._vig_composite_module()
            single=deepcopy(recipe['roles'][role])
            w.apply_recipe(single,remap_current_data=False,show_message=False)
            source=Path(files[role]).resolve()
            # Hash before and after processing: a changing measurement file is invalid.
            before=sha256_file(source)
            report(int(index*60/len(roles)+5),f'{role}：执行现有导入、单位、变换、滤波与 ROI 分析')
            if not w.load_path(source) or w.last_metrics is None:
                raise ValueError(f'{role}：单层分析未完成或无有效结果；{getattr(w, "_automation_last_status", "")}')
            for axis in 'xyz':
                column=single['column_mapping'][f'{axis}_col']
                if column not in w.absolute_raw_df.columns:
                    raise ValueError(f'{role}：Recipe 所需源列 {column!r} 缺失，不允许改用自动映射')
            if w._task_thread is not None:
                raise RuntimeError('单层处理尚有未完成后台任务，禁止生成槽')
            replay=getattr(w,'_last_deletion_replay',{'status':'empty'})
            if replay['status'] not in ('empty','ok','pending'):
                message=f'{role}：手动删除未重放（{replay["status"]}）'
                if recipe['policy']['deletion_mismatch']=='fail':raise ValueError(message)
                warnings.append(message+'；按运行前策略跳过并标记')
            if sha256_file(source)!=before:raise ValueError(f'{role}：处理期间源文件内容发生变化')
            # Snapshot functions are exactly the manual write-slot functions.
            record=w._current_parallel_record() if recipe['module']=='parallelism' else w._current_gap_record()
            if record is None:raise ValueError(f'{role}：无法生成有效槽快照')
            # Preserve the requested parameters, plus runtime effective parameters
            # (including device-owned mapping/units) for traceability.
            record['requested_single_recipe']=single
            quality=record['metric_quality']
            if quality.get('estimated') and recipe['policy']['estimated']=='reject':
                raise ValueError(f'{role}：抽样／估算结果被运行前质量策略拒绝')
            if quality.get('estimated'):warnings.append(f'{role}：{quality.get("label", "抽样估算")}')
            records[role]=record
            provenance[role]=dict(path=str(source),sha256=before,
                single_recipe=single,effective_single_recipe=record['single_recipe'],
                deletion_replay=replay,quality=quality,import_info=record.get('import_info',{}))
        target=windows[-1]
        registration_results={}
        if recipe.get('calculation',{}).get('judgment'):
            target.set_judgment_spec(recipe['calculation']['judgment'],persist=False)
        if recipe['module'] == 'vig':
            target._set_export_origin_zero_active_points(recipe['export'].get('zero_origin_active_points',False))
            report(70, '调用可选扩展现有 VIG 光线追迹')
            module = target._vig_composite_module()
            result = module.compute_snapshot(records, recipe,
                progress=lambda value,text: report(70+int(value*.18),text))
            module.slots = {key: records.get(key) for key in ('two','three','up','mid','low')}
            module._apply_vig_payload(result)
            for error in result.get('errors', []):
                warnings.append(f"VIG 点 {error['index']}: {error['message']}")
            plane = result['plane']
            metrics = {'rx_urad':plane['rx']*1e6,'ry_urad':plane['ry']*1e6,
                       'rms_um':plane['rms']*1e6,'std3_um':plane['std3']*1e6,
                       'p97_half_um':plane['p97_half']*1e6,'matched_points':result['n_points'],
                       'failed_points':result.get('n_errors',0)}
        elif recipe['module']=='parallelism':
            report(70,'调用现有平行度计算')
            result=target._compute_parallel_result_from_records(records['base'],records['measure'])
            target.parallel_base=records['base'];target.parallel_measure=records['measure'];target.parallel_result=result
            metrics={f'{k}_urad':result[k] for k in ('drx','dry','angle')}
            metrics.update(step_height_mm=result['step_height'],ref_x_mm=result['ref_x'],ref_y_mm=result['ref_y'])
            for role in roles:
                for name,unit in (('rx','urad'),('ry','urad'),('pv','um'),('rms','um'),('ttv','um'),('mean_z','mm')):
                    metrics[f'{role}_{name}_{unit}']=records[role]['metrics'][name]
        else:
            reg=recipe['registration'];mode=reg['mode'];tol=reg['tolerance_mm']
            for role in roles[1:]:
                dx,dy=reg['offsets'][role] if mode!='none' else (0.,0.)
                records[role].update(offset_x=dx,offset_y=dy,registration_mode='manual' if mode!='none' else 'none')
            if mode=='auto':
                report(65,'按本组新数据重新执行现有自动配准')
                registration_results=target._auto_registration_payload(records['stack'],records['base1'],records.get('base2'),tol,lambda v,t:report(65+int(v*.13),t),threading.Event())
                for role,proposal in registration_results.items():
                    # _optimize_translation returns explicit acceptance. Do not
                    # silently reuse an old/fixed offset when auto matching fails.
                    if not proposal.get('accepted'):
                        raise ValueError(f'{role} 自动配准未接受：{proposal.get("reason", "unknown")}')
                    records[role].update(offset_x=float(proposal['offset_x']),offset_y=float(proposal['offset_y']),registration_mode='auto')
            report(78,'调用现有点匹配及膜厚扣减')
            result=target._compute_gap_payload(records['stack'],records['base1'],records.get('base2'),tol,lambda v,t:report(78+int(v*.10),t),threading.Event())
            target.data_stack=records['stack'];target.data_base1=records['base1'];target.data_base2=records.get('base2');target.gap_result=result
            target.spin_tol.setValue(tol)
            metrics={f'{key}_{unit}':result['plane_metrics'][key] for key,unit in
                     (('rx','urad'),('ry','urad'),('pv','um'),('rms','um'),('ttv','um'),('mean_z','mm'))}
            metrics['matched_points']=len(result['z'])
        report(88,'导出本组结果与追溯元数据')
        paths={}
        if recipe['export']['csv']:
            path=output/'result.csv'
            if recipe['module'] == 'vig': module.write_csv(path)
            elif recipe['module']=='thickness':target._gap_result_frame().to_csv(path,index=False,encoding='utf-8-sig')
            else:pd.DataFrame([metrics]).to_csv(path,index=False,encoding='utf-8-sig')
            paths['csv']=str(path.resolve())
        if recipe['export']['report'] and recipe['module']=='thickness':
            target.load_gap_secondary()
            x,y,z=target.get_final_transformed_data(target.df_raw)
            figure=target._render_report_figure(target.current_source_name,x,y,z,target.active_idx,
                target.compute_plane_metrics(x[target.active_idx],y[target.active_idx],z[target.active_idx]),target.n_filtered,'原始状态','关闭',target.import_info,display_surface_mode='raw')
        elif recipe['export']['report']:
            figure=(module._render_vig_report_figure() if recipe['module']=='vig' else
                    target._render_gap_report_figure() if recipe['module']=='thickness' else target._render_parallel_report_figure())
        if recipe['export']['report']:
            path=output/'report.png';figure.savefig(path,dpi=150);figure.clear()
            paths['report']=str(path.resolve())
        display_diagnostic=(target._registration_diagnostic(records['stack'],records['base1'],records.get('base2'),recipe['registration']['tolerance_mm']) if recipe['module']=='thickness' else None)
        for record in records.values():record.pop('_gap_xy_tree',None)
        save_snapshot(output,dict(module=recipe['module'],records=records,result=result,display_diagnostic=display_diagnostic))
        metadata=dict(status='success',recipe=recipe,inputs=provenance,metrics=metrics,
                      registration_results=registration_results,warnings=warnings,
                      quality={r:records[r]['metric_quality'] for r in roles},outputs=paths,
                      calculation_errors=result.get('errors', []), failed_points=result.get('n_errors',0),
                      completed_at=datetime.now(timezone.utc).isoformat())
        atomic_json(output/'run.json',plain(metadata))
        report(100,'本组成功完成')
        return metadata
    finally:
        # Child process lifetime owns Qt objects. Do not schedule deferred
        # widget destruction while Matplotlib draw-idle callbacks are pending.
        for w in windows:w.close()


def child_main(request_path):
    os.environ['QT_QPA_PLATFORM']='offscreen'
    request=json.loads(Path(request_path).read_text(encoding='utf-8'))
    output=Path(request['output'])
    def report(value,text):
        with (output/'progress.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(value=int(value),text=str(text)),ensure_ascii=False)+'\n')
    try:
        execute_group(request['recipe'],request['files'],output,report)
        return 0
    except Exception as exc:
        atomic_json(output/'run.json',dict(status='failed',reason=str(exc)))
        return 1


def process_group(recipe, files, output, progress, cancel, timeout=1800):
    output=Path(output)
    request=output/'request.json'
    atomic_json(request,dict(recipe=recipe,files=files,output=str(output.resolve())))
    if getattr(sys,'frozen',False):command=[sys.executable,'--composite-worker',str(request)]
    else:command=[sys.executable,'-m','surface_analyzer','--composite-worker',str(request)]
    env=os.environ.copy();env['QT_QPA_PLATFORM']='offscreen';env['PYTHONIOENCODING']='utf-8'
    # Ensure source checkout works regardless of the caller's working directory.
    root=str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH']=root+os.pathsep+env.get('PYTHONPATH','')
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    with (output/'worker.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(command,stdout=log,stderr=log,env=env,**options)
        start=time.monotonic();offset=0
        try:
            while True:
                if cancel.is_set():raise TaskCancelled()
                if time.monotonic()-start>timeout:raise TimeoutError(f'本组超过 {timeout:g} 秒运行时限')
                events=output/'progress.jsonl'
                if events.exists():
                    with events.open(encoding='utf-8') as stream:
                        stream.seek(offset)
                        while True:
                            position=stream.tell();line=stream.readline()
                            if not line or not line.endswith('\n'):
                                stream.seek(position);break
                            event=json.loads(line);progress(event['value'],event['text'])
                        offset=stream.tell()
                try:code=child.wait(timeout=.15);break
                except subprocess.TimeoutExpired:pass
            if cancel.is_set():raise TaskCancelled()
            path=output/'run.json'
            if not path.exists():raise RuntimeError(f'组合计算进程异常退出（{code}），见 worker.log')
            result=json.loads(path.read_text(encoding='utf-8'))
            if code or result.get('status')!='success':raise ValueError(result.get('reason') or f'计算进程退出 {code}')
            return result
        finally:
            if child.poll() is None:
                if os.name=='nt':
                    # A venv python.exe can be a launcher with a real Python
                    # child. Stop our owned tree, not only the launcher.
                    subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                   timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                else:child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


def run_groups(recipe, groups, root_output, progress=None, cancel=None, *, executor=process_group, timeout=1800):
    validate_composite(recipe)
    recipe=deepcopy(recipe);groups=deepcopy(groups)
    progress=progress or (lambda value,text:None);cancel=cancel or threading.Event()
    # Every invocation gets an independent run directory. Never overwrite a
    # prior batch, even if sample IDs or file names are identical.
    output=Path(root_output)/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'composite.recipe.json',recipe)
    results=[];last_snapshot=None
    duplicates={};conflicts=set()
    for i,group in enumerate(groups):
        if not group.get('selected',True):continue
        if validate_group(group,recipe):continue
        for role in required_roles(recipe):
            value=group['files'][role];value=value[0] if isinstance(value,list) else value
            key=str(Path(value).resolve()).casefold()
            if key in duplicates:conflicts.update((i,duplicates[key]))
            duplicates[key]=i
    for i,group in enumerate(groups):
        files={r:(v[0] if isinstance(v,list) and len(v)==1 else v) for r,v in group['files'].items()}
        row=dict(sample_id=group['sample_id'],status='not_run',reason='',files=files,
                 recipe_name=recipe['name'],recipe_schema=recipe['schema_version'],recipe_app_version=recipe.get('app_version',''),module=recipe['module'],metrics={},outputs={})
        results.append(row)
        errors=validate_group(group,recipe)
        if i in conflicts:errors.append('冲突：多个选中分组复用了同一文件')
        if cancel.is_set():row['reason']='用户取消，未运行';continue
        if not group.get('selected',True) or errors:
            row['status']='skipped';row['reason']='; '.join(errors) or '未选中';continue
        safe=re.sub(r'[^\w\-.\u4e00-\u9fff]','_',str(group['sample_id']))[:80].strip('.') or 'sample'
        folder=output/f'{i+1:04d}_{safe}';folder.mkdir()
        row['output_dir']=str(folder.resolve());last_snapshot=None
        def group_progress(value,text):
            progress(int((i+max(0,min(100,value))/100)*100/max(1,len(groups))),f'[{i+1}/{len(groups)} {group["sample_id"]}] {text}')
        try:
            group_progress(0,'开始')
            result=executor(recipe,files,folder,group_progress,cancel,timeout=timeout)
            if cancel.is_set():raise TaskCancelled()
            result['sample_id']=group['sample_id']
            atomic_json(folder/'run.json',plain(result))
            row.update(status='success',metrics=result['metrics'],quality=result.get('quality',{}),
                       warnings=result.get('warnings',[]),outputs=result.get('outputs',{}),metadata=str(folder/'run.json'))
            last_snapshot=str(folder)
        except TaskCancelled:
            row.update(status='cancelled',reason='用户取消');cancel.set()
            atomic_json(folder/'run.json',dict(status='cancelled',reason=row['reason'],recipe=recipe,files=files))
        except Exception as exc:
            row.update(status='failed',reason=str(exc))
            atomic_json(folder/'run.json',dict(status='failed',reason=str(exc),recipe=recipe,files=files))
        # Save summary after each group; a crash does not erase prior rows.
        _write_summary(output,results)
    _write_summary(output,results)
    atomic_json(output/'batch.json',plain(dict(recipe=recipe,groups=results,cancelled=cancel.is_set())))
    snapshot=load_snapshot(last_snapshot) if last_snapshot and not cancel.is_set() else None
    return dict(recipe=recipe,results=results,output_dir=str(output),summary=str(output/'summary.csv'),snapshot=snapshot,cancelled=cancel.is_set())


def _write_summary(output, results):
    rows=[]
    for row in results:
        item={k:v for k,v in row.items() if k not in ('files','metrics','outputs','quality','warnings')}
        item.update({f'input_{k}':json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in row['files'].items()})
        item.update(row['metrics']);item.update({f'output_{k}':v for k,v in row['outputs'].items()})
        item['quality']=json.dumps(row.get('quality',{}),ensure_ascii=False)
        item['warnings']='; '.join(row.get('warnings',[]));rows.append(item)
    path=Path(output)/'summary.csv';temporary=path.with_suffix('.tmp')
    pd.DataFrame(rows).to_csv(temporary,index=False,encoding='utf-8-sig');os.replace(temporary,path)
