"""Read-only performance audit. Each case runs in an isolated process.

Examples:
  .venv/Scripts/python benchmarks/performance_benchmark.py --suite kernel
  .venv/Scripts/python benchmarks/performance_benchmark.py --suite import --points 2000000
  .venv/Scripts/python benchmarks/performance_benchmark.py --suite smart --points 100000,500000

RSS is sampled every 20 ms (MiB); timing excludes generation and GC. Nested
stage timings are inclusive, never sum them into an end-to-end total.
Synthetic files are warm-cache, not cold-disk measurements. No app code changes.
"""
import argparse
import cProfile
import csv
import ctypes
import gc
import json
import os
from pathlib import Path
import platform
import pstats
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from surface_analyzer.mixins.analysis import AnalysisMixin as Analysis
from surface_analyzer.rendering.raster import build_xy_raster, raster_rgba
from surface_analyzer.rendering.xy_display import build_xy_display, estimate_scan_grid
from surface_analyzer.rendering.lod import spatial_lod_indices, critical_indices


class Memory(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


def rss():
    if os.name != 'nt':
        return 0.
    value = Memory(); value.cb = ctypes.sizeof(value)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(value), value.cb)
    return value.WorkingSetSize / 1024**2


FIELDS = ['test_name', 'points', 'duration_ms', 'memory_mb', 'backend', 'notes',
          'baseline_mb', 'delta_peak_mb', 'repeat', 'status']


class Recorder:
    def __init__(self, args):
        self.args = args
        self.path = Path(args.output) / 'benchmark_results.csv'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def row(self, name, ms, peak, base, notes='', repeat=0, status='ok', backend='CPU'):
        fresh = not self.path.exists()
        with self.path.open('a', newline='', encoding='utf-8-sig' if fresh else 'utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if fresh: writer.writeheader()
            writer.writerow(dict(test_name=name, points=self.args.n, duration_ms=round(ms, 4),
                memory_mb=round(peak, 2), backend=backend, notes=notes,
                baseline_mb=round(base, 2), delta_peak_mb=round(max(0, peak-base), 2),
                repeat=repeat, status=status))
        print(f'{name} N={self.args.n} {ms:.1f} ms RSS={peak:.0f} MiB {status}', flush=True)

    def measure(self, name, fn, repeats=1, notes='', backend='CPU'):
        result = None
        for i in range(repeats):
            result = None; gc.collect()
            base = rss(); samples = [base]; stop = threading.Event()
            def sample():
                while not stop.wait(.02): samples.append(rss())
            thread = threading.Thread(target=sample, daemon=True); thread.start()
            start = time.perf_counter()
            try: result = fn()
            finally:
                elapsed = (time.perf_counter()-start)*1000
                samples.append(rss()); stop.set(); thread.join()
            self.row(name, elapsed, max(samples), base, notes, i, backend=backend)
        return result

    def profile(self, name, fn):
        profile = cProfile.Profile(); value = profile.runcall(fn)
        base = Path(self.args.output)/f'{name}_{self.args.n}'
        profile.dump_stats(str(base)+'.prof')
        stats = pstats.Stats(profile)
        rows = []
        for (filename, line, function), (cc, nc, own, cumulative, callers) in stats.stats.items():
            rows.append(dict(file=filename, line=line, function=function, calls=nc,
                             self_ms=own*1000, cumulative_ms=cumulative*1000))
        pd.DataFrame(rows).sort_values('cumulative_ms', ascending=False).to_csv(
            str(base)+'_profile.csv', index=False, encoding='utf-8-sig')
        return value


def data(n, jitter=False):
    cols = 1000 if n >= 100000 else max(3, int(np.sqrt(n)))
    ids = np.arange(n, dtype=np.int64); rows, columns = ids//cols, ids%cols
    x = columns.astype(float)*.03; y = rows.astype(float)*.03
    if jitter:
        rng = np.random.default_rng(469)
        x += rng.normal(0, .0001, n); y += rng.normal(0, .0001, n)
    z = .45+1e-5*x-2e-5*y+.0001*np.sin(x/4)*np.cos(y/4)
    return x, y, z, rows, columns


def kernel(rec, args):
    x,y,z,rows,cols = data(args.n, True); n=len(x); rep=args.repeats
    source = np.arange(n); extent=(float(x.min()),float(x.max()),float(y.min()),float(y.max()))
    roi = (x>10)&(x<20)
    coeffs=rec.measure('fit_plane',lambda:Analysis.fit_plane(x,y,z),rep)
    rec.measure('plane_metrics_including_fit',lambda:Analysis.compute_plane_metrics(x,y,z),rep)
    rec.measure('mean_min_max',lambda:(z.mean(),z.min(),z.max()),rep)
    residual=rec.measure('residual_normal',lambda:(z-(coeffs[0]*x+coeffs[1]*y+coeffs[2]))/np.sqrt(1+coeffs[0]**2+coeffs[1]**2),rep)
    rec.measure('PV_RMS',lambda:(np.ptp(residual)*1000,np.sqrt(np.mean(residual**2))*1000),rep)
    rec.measure('Rx_Ry',lambda:(np.arctan(coeffs[1])*1e6,np.arctan(-coeffs[0])*1e6),rep)
    rec.measure('transform_empty_pipeline',lambda:Analysis._apply_transform_pipeline(x,y,z,[]),rep)
    rec.measure('mask_intersection',lambda:np.isfinite(x)&np.isfinite(y)&np.isfinite(z)&roi&(z>.449),rep)
    rec.measure('dataframe_numeric',lambda:pd.DataFrame(dict(X=x,Y=y,Z=z)),rep)
    frame=pd.DataFrame(dict(X=x,Y=y,Z=z))
    rec.measure('dataframe_selected_to_numpy',lambda:frame[['X','Y','Z']].to_numpy(dtype=float),rep)
    xy=rec.measure('xy_column_stack',lambda:np.column_stack((x,y)),rep)
    tree=rec.measure('KDTree_build',lambda:cKDTree(xy),rep)
    rec.measure('KDTree_query_4096_k32',lambda:tree.query(xy[np.linspace(0,n-1,min(n,4096),dtype=int)],k=32),rep)
    rec.measure('KDTree_query_all_k9',lambda:tree.query(xy,k=9),1)
    del tree,xy; gc.collect()
    grid=rec.measure('scan_grid_estimate_including_tree',lambda:estimate_scan_grid(x,y),rep)
    raster=rec.measure('raster_full',lambda:build_xy_raster(x,y,z,extent,(1000,800),roi),rep)
    rec.measure('raster_rgba',lambda:raster_rgba(raster),rep)
    rec.measure('XY_display_cold',lambda:build_xy_display(x,y,z,extent,(1000,800),roi),rep)
    rec.measure('XY_display_grid_cached',lambda:build_xy_display(x,y,z,extent,(1000,800),roi,grid=grid),rep)
    midx=(extent[0]+extent[1])/2; midy=(extent[2]+extent[3])/2
    zoom=(midx-(extent[1]-extent[0])*.1,midx+(extent[1]-extent[0])*.1,
          midy-(extent[3]-extent[2])*.1,midy+(extent[3]-extent[2])*.1)
    rec.measure('XY_zoom_rebuild_grid_cached',lambda:build_xy_display(x,y,z,zoom,(1000,800),roi,grid=grid),rep)
    required=rec.measure('LOD_critical_indices',lambda:critical_indices(x,y,z,source,coeffs),rep)
    idx=rec.measure('LOD_30k',lambda:spatial_lod_indices(x,y,source,30000,required),rep)
    rec.measure('render_prepare_LOD_arrays',lambda:(x[idx],y[idx],z[idx]),rep)
    rec.measure('render_prepare_full_snapshot',lambda:(x[source],y[source],z[source],roi[source]),rep)
    from surface_analyzer.polynomial import fit_polynomial_surface
    for order in (2,3):
        rec.measure(f'polynomial_order{order}',lambda:fit_polynomial_surface(x,y,z,order),1)
    if args.profile:
        rec.profile('kernel',lambda:(build_xy_display(x,y,z,extent,(1000,800),roi),
                                   spatial_lod_indices(x,y,source,30000,required)))


def make_window():
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from surface_analyzer.app import SurfaceAnalyzerPro
    def fail(_parent,title,message,*a,**kw):
        raise RuntimeError(f'{title}: {message}')
    QMessageBox.critical=fail
    app=QApplication.instance() or QApplication([])
    return app, SurfaceAnalyzerPro()


def generate_file(path, kind, n):
    # Generator excluded from all timing; deterministic decimal roundtrip.
    x,y,z,r,c=data(n)
    with path.open('w',encoding='utf-8',newline='') as f:
        if kind=='zygo':
            header=['Zygo XYZ Data File - Format 1','audit','audit',f'0 0 1000 {n//1000}',
                    '0','0','0','0 0 0 0 0 0 0.00003 0','0','0','0','0','0','0','#']
            f.write('\n'.join(header)+'\n')
        elif kind=='matrix':
            pass
        elif kind=='keyence':
            f.write(f'Keyence VR\nX Pitch,30\nY Pitch,30\nRows,{n//1000}\nColumns,1000\n')
        else: f.write('X,Y,Z\n' if kind in ('csv','quoted','pixel') else ('X\tY\tZ\n' if kind=='tab' else 'X Y Z\n'))
        for start in range(0,n,100000):
            end=min(n,start+100000)
            if kind in ('matrix','keyence'):
                np.savetxt(f,z[start:end].reshape(-1,1000),fmt='%.8f',delimiter=',')
            else:
                values=np.column_stack((c[start:end],r[start:end],z[start:end])) if kind in ('pixel','zygo') else np.column_stack((x[start:end],y[start:end],z[start:end]))
                delimiter=',' if kind in ('csv','quoted','pixel') else ('\t' if kind=='tab' else ' ')
                fmt='"%.8f"' if kind=='quoted' else '%.8f'
                np.savetxt(f,values,fmt=fmt,delimiter=delimiter)
        if kind=='zygo': f.write('#\n')


def imports(rec,args):
    kind=args.kind; app,w=make_window()
    w.auto_sample_large_text=args.sampled
    w.input_layout_mode='height_matrix' if kind in ('matrix','keyence') else ('pixel_xy' if kind=='pixel' else ('zygo_xyz' if kind=='zygo' else 'point_table'))
    if kind in ('matrix','keyence'):
        w.height_matrix_cols=1000; w.height_matrix_rows=args.n//1000
    path=Path(args.output)/f'input_{kind}_{args.n}.dat'
    generate_file(path,kind,args.n)
    size=path.stat().st_size
    rec.measure(f'{kind}_file_open',lambda:open(path,'rb').close())
    def raw_read():
        with path.open('rb') as f:
            while f.read(8*1024*1024): pass
    rec.measure(f'{kind}_raw_read_warm',raw_read,notes=f'{size} bytes; warm OS cache')
    # Method wrappers only in benchmark process; nested inclusive stage totals.
    totals={}
    frame_init=pd.DataFrame.__init__
    def timed_frame_init(self,*a,**kw):
        start=time.perf_counter()
        try:return frame_init(self,*a,**kw)
        finally:totals['DataFrame_constructor']=totals.get('DataFrame_constructor',0)+(time.perf_counter()-start)*1000
    pd.DataFrame.__init__=timed_frame_init
    names=['_text_format_signature','_detect_text_layout','_extract_text_preamble_metadata',
           '_scan_height_matrix_metadata','_prescan_height_matrix','_height_matrix_dataframe',
           '_read_full_delimited_text','_read_height_matrix_table','_read_zygo_xyz',
           '_sample_large_text','_sample_large_pixel_text','_sample_large_height_matrix_by_stride']
    for name in names:
        original=getattr(w,name)
        def timed(*a,_fn=original,_name=name,**kw):
            start=time.perf_counter()
            try:return _fn(*a,**kw)
            finally:totals[_name]=totals.get(_name,0)+(time.perf_counter()-start)*1000
        setattr(w,name,timed)
    try:
        frame=rec.measure(f'{kind}_read_table'+('_sampled' if args.sampled else '_full'),
                          lambda:w._read_table(path,progress=lambda *_:None),notes=f'{size} bytes; parser+DataFrame; excludes mapping/UI')
        pd.DataFrame.__init__=frame_init
        for name,ms in totals.items():rec.row(f'{kind}_stage{name}',ms,rss(),rss(),'inclusive nested stage; no separate peak sample')
        rec.row(f'{kind}_retained_frame',0,rss(),rss(),json.dumps(dict(rows=len(frame),deep_bytes=int(frame.memory_usage(deep=True).sum()),info=w.import_info),default=str,ensure_ascii=False))
        if args.profile:
            rec.profile(f'import_{kind}',lambda:w._read_table(path,progress=lambda *_:None))
        if kind=='matrix' and not args.sampled:
            def fromstring_candidate():
                with path.open(encoding='utf-8') as f:
                    return np.vstack([np.fromstring(line,sep=',') for line in f])
            candidate=rec.measure('matrix_candidate_fromstring_clean',fromstring_candidate,
                                  notes='numeric matrix only; skips production validation, metadata and topology')
            assert np.array_equal(candidate.ravel(),frame['Z'].to_numpy())
            del candidate
        if kind in ('csv','quoted','space','tab') and not args.sampled:
            sep=',' if kind in ('csv','quoted') else (r'\s+' if kind=='space' else '\t')
            candidate=rec.measure(f'{kind}_candidate_pandas_C',lambda:pd.read_csv(path,sep=sep,engine='c',dtype=float),notes='benchmark-only clean numeric contract; not production replacement')
            numeric=rec.measure(f'{kind}_to_numeric',lambda:frame.iloc[:,:3].apply(pd.to_numeric,errors='coerce'))
            assert np.allclose(candidate.to_numpy(),numeric.to_numpy(),rtol=0,atol=0)
            del candidate,numeric
        # Actual mapping and initialization; intercept analysis separately.
        w.absolute_raw_df=frame
        for combo,column in zip((w.cb_x_col,w.cb_y_col,w.cb_z_col),frame.columns[:3]):
            combo.clear();combo.addItem(str(column))
        w.cb_x_unit.setCurrentText('mm');w.cb_y_unit.setCurrentText('mm');w.cb_z_unit.setCurrentText('mm')
        update=w.update_analysis;w.update_analysis=lambda:None
        rec.measure(f'{kind}_mapping_initialization_no_analysis',lambda:w.apply_mapping())
        w.update_analysis=update
        if w.df_raw is None: raise RuntimeError('mapping failed')
        rec.row(f'{kind}_mapped_points',0,rss(),rss(),f'rows={len(w.df_raw)}')
    finally:
        pd.DataFrame.__init__=frame_init
        w.close()
        if not args.keep_files:path.unlink(missing_ok=True)


def smart(rec,args):
    import surface_analyzer.smart_roi as s
    totals={}
    for name in ('_matrix_edges','_edge_pairs_to_adjacency','_topology_health','_edge_lengths',
                 '_delaunay_edges','_adaptive_knn_edges','_prune_edges_by_local_scale','_choose_adaptive_knn_k'):
        original=getattr(s,name)
        def wrapped(*a,_fn=original,_name=name,**kw):
            start=time.perf_counter()
            try:return _fn(*a,**kw)
            finally:totals[_name]=totals.get(_name,0)+(time.perf_counter()-start)*1000
        setattr(s,name,wrapped)
    class TimedTree(cKDTree):
        def __init__(self,*a,**kw):
            start=time.perf_counter();super().__init__(*a,**kw)
            totals['KDTree_build']=totals.get('KDTree_build',0)+(time.perf_counter()-start)*1000
        def query(self,*a,**kw):
            start=time.perf_counter()
            try:return super().query(*a,**kw)
            finally:totals['KDTree_query']=totals.get('KDTree_query',0)+(time.perf_counter()-start)*1000
    s.cKDTree=TimedTree
    x,y,z,rows,cols=data(args.n,args.kind=='physical')
    rc=None if args.kind=='physical' else (rows,cols)
    if args.kind=='sampled_matrix':
        # Compressed topology coordinates are what the sampled matrix importer supplies.
        x=x*2;y=y*2
    topology=rec.measure(args.kind+'_topology',lambda:s.build_adaptive_topology(x,y,rc))
    for name,ms in totals.items():rec.row(args.kind+'_stage_'+name,ms,rss(),rss(),'inclusive nested stage')
    rec.row(args.kind+'_topology_metadata',0,rss(),rss(),json.dumps(
        {k:v for k,v in topology.items()
         if k not in ('adjacency', 'matrix_grid', 'matrix_point_rows', 'matrix_point_cols')},
        default=str))
    for mode in ('plane_residual','surface_following'):
        stats={}
        mask=rec.measure(args.kind+'_'+mode,lambda:s.grow_surface_roi(x,y,z,x[len(x)//2],y[len(x)//2],.005,topology,mode=mode,stats=stats))
        rec.row(args.kind+'_'+mode+'_stats',0,rss(),rss(),json.dumps(stats))
        assert mask.all(), 'Smooth synthetic surface should be connected'
    if args.profile:
        rec.profile(args.kind+'_topology',lambda:s.build_adaptive_topology(x,y,rc))
        rec.profile(args.kind+'_growth',lambda:s.grow_surface_roi(x,y,z,x[len(x)//2],y[len(x)//2],.005,topology))


def render(rec,args):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from PyQt6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    x,y,z,_,_=data(args.n,True)
    for view in ('XY','3D','XZ','YZ'):
        fig=Figure(figsize=(6,4),dpi=100); canvas=FigureCanvasQTAgg(fig)
        ax=fig.add_subplot(111,projection='3d' if view=='3D' else None)
        rec.measure(view+'_artist_create',lambda:ax.scatter(x,y,z,c=z,s=3,edgecolors='none',cmap='turbo') if view=='3D' else ax.scatter(x if view!='YZ' else y,y if view=='XY' else z,c=z,s=3,edgecolors='none',cmap='turbo'))
        rec.measure(view+'_canvas_draw',canvas.draw,args.repeats,backend='Matplotlib QtAgg offscreen',notes='600x400; exact requested display points, stress case, no production LOD')
        if args.profile and view=='3D':rec.profile('render3D',canvas.draw)
        canvas.close();del canvas,fig,ax;gc.collect()


def gui(rec,args):
    from PyQt6.QtTest import QTest
    from PyQt6.QtCore import QTimer
    app,w=make_window();x,y,z,_,_=data(args.n,True)
    w.df_raw=pd.DataFrame(dict(X=x,Y=y,Z=z));w._df_version+=1
    w.manual_mask=np.ones(args.n,bool);w.temp_selected_mask=np.zeros(args.n,bool)
    w.import_info=dict(import_rows=args.n,mapped_finite_points=args.n,sampled=False)
    for combo,name in ((w.cb_x_col,'X'),(w.cb_y_col,'Y'),(w.cb_z_col,'Z')):combo.addItem(name)
    w.resize(1600,900);w.show();app.processEvents()
    timings={}; beats=[];timer=QTimer();timer.setInterval(20);timer.timeout.connect(lambda:beats.append(time.perf_counter()));timer.start()
    for view,canvas in w.canvas._canvas_by_view.items():
        original=canvas.draw
        def wrapped(_fn=original,_name=view):
            start=time.perf_counter()
            try:return _fn()
            finally:timings[_name]=timings.get(_name,0)+(time.perf_counter()-start)*1000
        canvas.draw=wrapped
    def ready():
        start=time.perf_counter()
        while time.perf_counter()-start<60:
            QTest.qWait(10)
            c=w.xy_raster
            if c.result is not None and c.future is None and not c.timer.isActive():
                app.processEvents();return
        raise RuntimeError('XY GUI rendering timeout')
    def refresh():
        w.update_analysis();ready()
        if w.last_metrics is None:raise RuntimeError('analysis failed')
    rec.measure('GUI_update_analysis_to_XY_ready',refresh)
    for name,ms in timings.items():rec.row('GUI_canvas_'+name,ms,rss(),rss(),'sum of draw calls during first refresh')
    rec.row('GUI_existing_phase_timers',0,rss(),rss(),json.dumps(w.smart_roi_performance))
    rec.measure('GUI_XY_actual_cache_hit_apply_draw',lambda:(w.xy_raster.start(),app.processEvents()))
    rec.measure('GUI_transform_actual_cache_hit',lambda:w.get_final_transformed_data(w.df_raw),3)
    rec.measure('GUI_repeat_analysis_to_XY_ready',refresh)
    if len(beats)>1:rec.row('GUI_max_event_gap',max(np.diff(beats))*1000,rss(),rss(),'20ms QTimer; synchronous GUI blocking included')
    w.grab().save(str(Path(args.output)/f'gui_{args.n}.png'))
    timer.stop();w.close()


def raster_stages(rec,args):
    x,y,z,_,_=data(args.n,True);xmin,xmax=x.min(),x.max();ymin,ymax=y.min(),y.max();nx,ny=1000,800
    visible=rec.measure('raster_stage_visible_bbox',lambda:np.isfinite(x)&np.isfinite(y)&(x>=xmin)&(x<=xmax)&(y>=ymin)&(y<=ymax),3)
    xv,yv,zv=rec.measure('raster_stage_visible_copies',lambda:(x[visible],y[visible],z[visible]),3)
    ix,iy=rec.measure('raster_stage_bin_index',lambda:(np.minimum(((xv-xmin)/(xmax-xmin)*nx).astype(np.int64),nx-1),np.minimum(((yv-ymin)/(ymax-ymin)*ny).astype(np.int64),ny-1)),3)
    flat=rec.measure('raster_stage_flat_index',lambda:iy*nx+ix,3)
    count=rec.measure('raster_stage_bincount',lambda:np.bincount(flat,minlength=nx*ny),3)
    finite=np.isfinite(zv)
    sums=rec.measure('raster_stage_z_aggregation',lambda:np.bincount(flat[finite],weights=zv[finite],minlength=nx*ny),3)
    def mean():
        target=np.full(nx*ny,np.nan);np.divide(sums,count,out=target,where=count>0);return target
    rec.measure('raster_stage_mean',mean,3)
    rec.measure('raster_stage_roi_aggregation',lambda:np.bincount(flat[xv>15],minlength=nx*ny),3)
    xy=np.column_stack((x,y));tree=cKDTree(xy)
    for workers in (1,6):
        rec.measure(f'KDTree_query_all_k9_workers{workers}',lambda:tree.query(xy,k=9,workers=workers),1)


def extras(rec,args):
    import pickle
    from scipy import ndimage
    from surface_analyzer.mixins.gap import GapAnalysisMixin
    x,y,z,rows,cols=data(args.n)
    rec.measure('filter_local_median_k12',lambda:Analysis.filter_keep_mask(x,y,z,2),1)
    rec.measure('filter_MAD',lambda:Analysis.filter_keep_mask(x,y,z,1),1)
    rec.measure('filter_sigma_5',lambda:Analysis.filter_keep_mask(x,y,z,3),1)
    stack=dict(x=x,y=y,z=z,name='stack');base=dict(x=x,y=y,z=z*.5,name='base')
    rec.measure('gap_one_base_payload',lambda:GapAnalysisMixin._compute_gap_payload(stack,base,None,.01,lambda *_:None,threading.Event()),1)
    payload=rec.measure('process_pickle_dump_XYZ',lambda:pickle.dumps((x,y,z),protocol=5),1,notes='serialization only; no pipe or process-start overhead')
    rec.measure('process_pickle_load_XYZ',lambda:pickle.loads(payload),1)
    del payload
    frame=pd.DataFrame(dict(X=x,Y=y,Z=z));xyz=frame[['X','Y','Z']].to_numpy(dtype=float)
    fragmented=pd.DataFrame();fragmented['X']=x;fragmented['Y']=y;fragmented['Z']=z
    frag=rec.measure('fragmented_frame_to_numpy',lambda:fragmented[['X','Y','Z']].to_numpy(dtype=float))
    rec.row('copy_semantics',0,rss(),rss(),json.dumps(dict(
        consolidated_shares_column=bool(np.shares_memory(xyz,frame['X'].to_numpy())),
        fragmented_shares_column=bool(np.shares_memory(frag,fragmented['X'].to_numpy())),
        frame_deep_bytes=int(frame.memory_usage(deep=True).sum()),xyz_bytes=int(xyz.nbytes))))
    gate=(x<25).reshape(-1,1000)
    rec.measure('candidate_ndimage_label_matrix8_gate_only',lambda:ndimage.label(gate,structure=np.ones((3,3))),3,
                notes='strict fixed-gate component only; NOT equivalent to surface_following or all fallback topologies')
    if args.n==100000:
        import surface_analyzer.smart_roi as s
        topology=s.build_adaptive_topology(x,y,(rows,cols))
        existing=s.grow_surface_roi(x,y,z,x[500],y[500],.005,topology,mode='plane_residual',candidate_mask=gate.ravel(),seed_index=500)
        labels,_=ndimage.label(gate,structure=np.ones((3,3)))
        candidate=(labels==labels.ravel()[500]).ravel()
        assert np.array_equal(existing,candidate)
        rec.row('candidate_component_equivalence_fixture',0,rss(),rss(),'exact equality on smooth gated 100k fixture only')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--suite',choices=['kernel','import','smart','render','gui','raster_stages','extras'],default='kernel')
    p.add_argument('--points',default='100000,500000,1000000,2000000,5000000')
    p.add_argument('--output',default=str(ROOT/'audit_outputs'/'performance_20260914'))
    p.add_argument('--kind',default='matrix')
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--profile',action='store_true')
    p.add_argument('--sampled',action='store_true')
    p.add_argument('--keep-files',action='store_true')
    p.add_argument('--worker',action='store_true')
    p.add_argument('--n',type=int,default=0)
    args=p.parse_args();Path(args.output).mkdir(parents=True,exist_ok=True)
    rec=Recorder(args)
    if args.worker:
        # Bound audit impact on a workstation shared with other applications.
        def memory_guard():
            while True:
                time.sleep(.25)
                used=rss()
                if used>7500:
                    rec.row(args.suite+'_'+args.kind,0,used,0,'7.5 GiB process RSS audit budget reached',status='memory_limit')
                    os._exit(3)
        threading.Thread(target=memory_guard,daemon=True).start()
        try:globals()[{'import':'imports'}.get(args.suite,args.suite)](rec,args)
        except Exception as exc:
            rec.row(args.suite+'_'+args.kind,0,rss(),rss(),str(exc),status='error')
            traceback.print_exc();sys.exit(1)
        return
    for n in map(int,args.points.split(',')):
        args.n=n
        command=[sys.executable,__file__,'--worker','--suite',args.suite,'--kind',args.kind,
                 '--n',str(n),'--output',args.output,'--repeats',str(args.repeats)]
        for flag in ('profile','sampled','keep_files'):
            if getattr(args,flag):command.append('--'+flag.replace('_','-'))
        try:
            run=subprocess.run(command,timeout=args.timeout)
            if run.returncode:print(f'Case failed: {n}',flush=True)
        except subprocess.TimeoutExpired:
            rec.row(args.suite+'_'+args.kind,args.timeout*1000,0,0,'case timeout; lower bound includes completed stages',status='timeout')


if __name__=='__main__':main()
